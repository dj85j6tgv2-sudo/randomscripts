"""Calico NetworkPolicy generator from egress-allowlist.yaml."""
from __future__ import annotations

import ipaddress
import logging
import socket
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
                "Hostname %r could not be resolved (%s). "
                "Check DNS, or replace with IP/CIDR in egress-allowlist.yaml.",
                host, exc,
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
    has_port = "port" in entry
    has_ports = "ports" in entry
    has_range = "port_range" in entry
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
        if not (isinstance(pr, dict) and isinstance(pr.get("start"), int)
                and isinstance(pr.get("end"), int)):
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


def _port_to_yaml(p: Port) -> int | str:
    if isinstance(p, tuple):
        return f"{p[0]}:{p[1]}"
    return p


def _nets_for_destination(dest: str, resolved: dict[str, list[str]]) -> list[str]:
    kind, value = classify(dest)
    if kind == "wildcard":
        raise ConfigError(
            f"wildcard destination {value!r} is not supported by Calico OSS; "
            "replace with explicit hostnames or a CIDR."
        )
    if kind == "cidr":
        return [value]
    if kind == "ip":
        return [f"{value}/32"]
    # hostname
    ips = resolved.get(value, [])
    return [f"{ip}/32" for ip in ips]


def _selector_expr(selector: dict[str, str]) -> str:
    return " && ".join(f'{k} == "{v}"' for k, v in sorted(selector.items()))


def _rule_key(rule: dict) -> tuple:
    dest = rule.get("destination", {})
    nets = dest.get("nets") or []
    ports = dest.get("ports") or []
    return (rule.get("protocol", ""), tuple(nets[:1]), tuple(str(p) for p in ports[:1]))


def build_policy(
    app: str,
    env: str,
    rules: list[Rule],
    selector: dict[str, str],
    resolved: dict[str, list[str]],
) -> dict:
    egress: list[dict] = [
        {"action": "Allow", "protocol": "UDP",
         "destination": {"ports": [53]}},
        {"action": "Allow", "protocol": "TCP",
         "destination": {"ports": [53]}},
    ]

    middle: list[dict] = []
    for rule in rules:
        nets: list[str] = []
        for dest in rule.destinations:
            nets.extend(_nets_for_destination(dest, resolved))
        if not nets:
            continue  # hostname(s) unresolved
        nets = sorted(set(nets))
        ports = [_port_to_yaml(p) for p in rule.ports]
        middle.append({
            "action": "Allow",
            "protocol": "TCP",
            "destination": {"nets": nets, "ports": ports},
        })

    middle.sort(key=_rule_key)
    egress.extend(middle)
    egress.append({"action": "Deny"})

    return {
        "apiVersion": "projectcalico.org/v3",
        "kind": "NetworkPolicy",
        "metadata": {"name": f"{app}-egress", "namespace": f"{app}-{env}"},
        "spec": {
            "selector": _selector_expr(selector),
            "types": ["Egress"],
            "egress": egress,
        },
    }
