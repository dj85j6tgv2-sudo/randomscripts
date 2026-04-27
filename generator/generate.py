"""Calico NetworkPolicy generator from egress-allowlist.yaml."""

from __future__ import annotations

import argparse
import ipaddress
import json as _json
import logging
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

log = logging.getLogger("egress.generate")

Port = int | tuple[int, int]
VALID_PROTOCOLS = {"tcp", "http", "https", "grpc"}

Kind = Literal["ip", "cidr", "hostname", "wildcard"]


def resolve_hostnames(hostnames: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    resolved: dict[str, list[str]] = {}
    failed: list[str] = []
    for host in sorted(set(hostnames)):
        try:
            _, _, ips = socket.gethostbyname_ex(host)
        except (socket.gaierror, socket.herror) as exc:
            log.warning(
                "Hostname %r could not be resolved (%s). Check DNS or replace with IP/CIDR.",
                host,
                exc,
            )
            failed.append(host)
            continue
        ips = sorted(set(ips))
        log.info("Resolved %s -> %s", host, ",".join(ips))
        resolved[host] = ips
    return resolved, failed


def filter_by_env(rules: list[Rule], env: str) -> list[Rule]:
    return [r for r in rules if r.envs is None or env in r.envs]


def classify(value: str) -> tuple[Kind, str]:
    if "*" in value:
        return ("wildcard", value)
    if "/" in value:
        try:
            ipaddress.ip_network(value, strict=False)
            return ("cidr", value)
        except ValueError:
            pass
    try:
        ipaddress.ip_address(value)
        return ("ip", value)
    except ValueError:
        pass
    return ("hostname", value)


class ConfigError(ValueError):
    """Fatal user-config error. Exit code 1."""


@dataclass(frozen=True)
class Rule:
    destinations: tuple[str, ...]
    ports: tuple[Port, ...]
    envs: frozenset[str] | None
    description: str | None


def _collect_destinations(entry: dict) -> tuple[str, ...]:
    keys = [k for k in ("destination", "destinations", "domains") if k in entry]
    if not keys:
        raise ConfigError(f"rule has no destination/destinations/domains: {entry!r}")
    if len(keys) > 1:
        raise ConfigError(
            f"rule sets multiple of destination/destinations/domains ({keys}): {entry!r}"
        )
    value = entry[keys[0]]
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ConfigError(f"invalid {keys[0]!r} value in {entry!r}")


def _collect_ports(entry: dict) -> tuple[Port, ...]:
    has_port, has_ports, has_range = (
        "port" in entry,
        "ports" in entry,
        "port_range" in entry,
    )
    if sum([has_port, has_ports, has_range]) > 1:
        raise ConfigError(f"rule sets multiple of port/ports/port_range: {entry!r}")
    if has_port:
        value = entry["port"]
        if not isinstance(value, int):
            raise ConfigError(f"port must be int: {entry!r}")
        return (value,)
    if has_ports:
        values = entry["ports"]
        if not (isinstance(values, list) and all(isinstance(v, int) for v in values)):
            raise ConfigError(f"ports must be a list of ints: {entry!r}")
        return tuple(values)
    if has_range:
        pr = entry["port_range"]
        if not (
            isinstance(pr, dict)
            and isinstance(pr.get("start"), int)
            and isinstance(pr.get("end"), int)
        ):
            raise ConfigError(f"port_range must be {{start,end}} ints: {entry!r}")
        if pr["start"] > pr["end"]:
            raise ConfigError(f"port_range start > end: {entry!r}")
        return ((pr["start"], pr["end"]),)
    raise ConfigError(f"rule has no port/ports/port_range: {entry!r}")


def load_allowlist(path: Path | str) -> list[Rule]:
    doc = yaml.safe_load(Path(path).read_text())
    if not isinstance(doc, dict) or "egress" not in doc:
        raise ConfigError("top-level 'egress' list is required")
    entries = doc["egress"]
    if not isinstance(entries, list):
        raise ConfigError("'egress' must be a list")

    rules: list[Rule] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError(f"rule must be a mapping: {entry!r}")
        protocol = entry.get("protocol")
        if protocol not in VALID_PROTOCOLS:
            raise ConfigError(
                f"protocol must be one of {sorted(VALID_PROTOCOLS)}: {entry!r}"
            )
        # protocol validated; all accepted values (http/https/tcp/grpc) map to TCP in Calico
        envs_raw = entry.get("envs")
        envs = frozenset(envs_raw) if envs_raw is not None else None
        rules.append(
            Rule(
                destinations=_collect_destinations(entry),
                ports=_collect_ports(entry),
                envs=envs,
                description=entry.get("description"),
            )
        )
    return rules


def _nets_for_destination(dest: str, resolved: dict[str, list[str]]) -> list[str]:
    kind, value = classify(dest)
    if kind == "wildcard":
        log.warning(
            "Wildcard %r cannot be enforced by Calico OSS (no hostname-header matching); skipping.",
            value,
        )
        return []
    if kind == "cidr":
        return [value]
    if kind == "ip":
        return [f"{value}/32"]
    # hostname
    ips = resolved.get(value, [])
    return [f"{ip}/32" for ip in ips]


def build_policy(
    app: str,
    env: str,
    rules: list[Rule],
    selector: dict[str, str],
    resolved: dict[str, list[str]],
) -> dict:
    egress: list[dict] = [
        {"_comment": "DNS (CoreDNS)", "action": "Allow", "protocol": "UDP", "destination": {"ports": [53]}},
        {"_comment": "DNS (CoreDNS)", "action": "Allow", "protocol": "TCP", "destination": {"ports": [53]}},
    ]

    middle: list[dict] = []
    for rule in rules:
        nets: list[str] = []
        for dest in rule.destinations:
            nets.extend(_nets_for_destination(dest, resolved))
        if not nets:
            continue  # hostname(s) unresolved
        nets = sorted(set(nets))
        ports = [f"{p[0]}:{p[1]}" if isinstance(p, tuple) else p for p in rule.ports]
        middle.append(
            {
                "_comment": rule.description or ", ".join(rule.destinations),
                "action": "Allow",
                "protocol": "TCP",
                "destination": {"nets": nets, "ports": ports},
            }
        )

    middle.sort(
        key=lambda r: (
            r.get("protocol", ""),
            tuple((r["destination"].get("nets") or [])[:1]),
            tuple(str(p) for p in (r["destination"].get("ports") or [])[:1]),
        )
    )
    egress.extend(middle)
    egress.append({"_comment": "default deny", "action": "Deny"})

    return {
        "apiVersion": "crd.projectcalico.org/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": f"{app}-egress", "namespace": f"{app}-{env}"},
        "spec": {
            "selector": " && ".join(
                f'{k} == "{v}"' for k, v in sorted(selector.items())
            ),
            "types": ["Egress"],
            "egress": egress,
        },
    }


def write_outputs(
    out_dir: Path | str, policies: dict[str, dict], resolved: dict[str, list[str]]
) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for env, policy in sorted(policies.items()):
        (out / f"networkpolicy-{env}.yaml").write_text(_dump_policy(policy))
    sorted_resolved = {k: sorted(v) for k, v in sorted(resolved.items())}
    (out / "resolved-ips.json").write_text(
        _json.dumps(sorted_resolved, indent=2, sort_keys=True) + "\n"
    )


def _dump_policy(policy: dict) -> str:
    """Serialize a NetworkPolicy dict to YAML with # comments on each egress rule."""
    egress = policy["spec"]["egress"]
    skeleton = {**policy, "spec": {**policy["spec"], "egress": None}}
    base = yaml.safe_dump(skeleton, sort_keys=False, default_flow_style=False)
    base = base.replace("  egress: null\n", "  egress:\n")
    parts = [base.rstrip("\n")]
    for rule in egress:
        rule = rule.copy()
        comment = rule.pop("_comment", None)
        if comment:
            parts.append(f"  # {comment}")
        for line in yaml.safe_dump([rule], sort_keys=False, default_flow_style=False).rstrip("\n").split("\n"):
            parts.append(f"  {line}")
    return "\n".join(parts) + "\n"


def _parse_selectors(items: list[str] | None, app: str) -> dict[str, str]:
    if not items:
        return {"app": app}
    if bad := [i for i in items if "=" not in i]:
        raise ConfigError(f"--selector must be key=value, got {bad!r}")
    return {k.strip(): v.strip() for i in items for k, v in [i.split("=", 1)]}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr
    )
    p = argparse.ArgumentParser(prog="generate")
    p.add_argument("--allowlist", required=True, type=Path)
    p.add_argument("--app", required=True)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument(
        "--selector",
        action="append",
        metavar="KEY=VAL",
        help="Selector label (repeatable). Default: app=<--app>.",
    )
    p.add_argument(
        "--envs",
        default="dev,stg,prd",
        metavar="ENV,...",
        help="Comma-separated environments.",
    )
    args = p.parse_args(argv)
    try:
        rules = load_allowlist(args.allowlist)
        selector = _parse_selectors(args.selector, args.app)
    except ConfigError as exc:
        log.error("%s", exc)
        return 1
    hostnames = [
        d for r in rules for d in r.destinations if classify(d)[0] == "hostname"
    ]
    resolved, failed = resolve_hostnames(hostnames)
    envs = [e.strip() for e in args.envs.split(",") if e.strip()]
    try:
        policies = {
            env: build_policy(
                args.app, env, filter_by_env(rules, env), selector, resolved
            )
            for env in envs
        }
    except ConfigError as exc:
        log.error("%s", exc)
        return 1
    write_outputs(args.output_dir, policies, resolved)
    if failed:
        log.error("Unresolved hostnames: %s", ", ".join(failed))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
