"""Calico NetworkPolicy / Envoy egress config generator from egress-allowlist.yaml."""

from __future__ import annotations

import argparse
import ipaddress
import json as _json
import logging
import re
import socket
import sys
from dataclasses import dataclass, field
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
class TlsConfig:
    cert: str
    key: str
    ca: str | None = None


@dataclass(frozen=True)
class Rule:
    destinations: tuple[str, ...]
    ports: tuple[Port, ...]
    envs: frozenset[str] | None
    description: str | None
    protocol: str = "tcp"
    tls: TlsConfig | None = None


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
                protocol=protocol,
                tls=_collect_tls(entry),
            )
        )
    return rules


def _collect_tls(entry: dict) -> TlsConfig | None:
    raw = entry.get("tls")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"'tls' must be a mapping: {entry!r}")
    cert = raw.get("cert")
    key = raw.get("key")
    if not cert or not key:
        raise ConfigError(f"tls block requires 'cert' and 'key': {entry!r}")
    return TlsConfig(cert=cert, key=key, ca=raw.get("ca"))


def _rule_annotation_line(rule: Rule, resolved: dict[str, list[str]]) -> str:
    ports = ", ".join(
        f"{p[0]}-{p[1]}" if isinstance(p, tuple) else str(p) for p in rule.ports
    )
    dest_parts = []
    for d in rule.destinations:
        kind, _ = classify(d)
        if kind == "hostname" and d in resolved:
            dest_parts.append(f"{d} [{', '.join(resolved[d])}]")
        else:
            dest_parts.append(d)
    label = f"{', '.join(dest_parts)}:{ports}"
    if rule.description:
        label += f" - {rule.description}"
    return label


def _build_annotations(rules: list[Rule], resolved: dict[str, list[str]]) -> dict[str, str]:
    lines = [_rule_annotation_line(r, resolved) for r in rules]
    return {"egress.policy/rules": "\n".join(lines)} if lines else {}


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


def _build_k8s_egress_rules(rules: list[Rule], resolved: dict[str, list[str]]) -> list[dict]:
    egress: list[dict] = [
        {"ports": [{"port": 53, "protocol": "UDP"}]},
        {"ports": [{"port": 53, "protocol": "TCP"}]},
    ]
    for rule in rules:
        nets: list[str] = []
        for dest in rule.destinations:
            nets.extend(_nets_for_destination(dest, resolved))
        if not nets:
            continue
        nets = sorted(set(nets))
        ports = []
        for p in rule.ports:
            if isinstance(p, tuple):
                ports.append({"port": p[0], "endPort": p[1], "protocol": "TCP"})
            else:
                ports.append({"port": p, "protocol": "TCP"})
        egress.append({
            "_comment": rule.description or ", ".join(rule.destinations),
            "to": [{"ipBlock": {"cidr": net}} for net in nets],
            "ports": ports,
        })
    return egress


def build_policy(
    app: str,
    env: str,
    rules: list[Rule],
    selector: dict[str, str],
    resolved: dict[str, list[str]],
    fmt: str = "calico",
) -> dict:
    annotations = _build_annotations(rules, resolved)
    if fmt == "kubernetes":
        egress = _build_k8s_egress_rules(rules, resolved)
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": f"{app}-egress", "namespace": f"{app}-{env}", "annotations": annotations},
            "spec": {
                "podSelector": {"matchLabels": selector},
                "policyTypes": ["Egress"],
                "egress": egress,
            },
        }

    # Calico format (default)
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
        "metadata": {"name": f"{app}-egress", "namespace": f"{app}-{env}", "annotations": annotations},
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


# ---------------------------------------------------------------------------
# Envoy config generation
# ---------------------------------------------------------------------------

def _sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", name)


def _ip_entries_for_dest(dest: str, resolved: dict[str, list[str]]) -> list[dict]:
    """Return list of {address, prefix_len} dicts for a destination."""
    kind, value = classify(dest)
    if kind == "wildcard":
        log.warning(
            "Wildcard %r cannot be used in TCP/gRPC filter chains (no runtime IP matching); skipping.",
            value,
        )
        return []
    if kind == "cidr":
        net = ipaddress.ip_network(value, strict=False)
        return [{"address": str(net.network_address), "prefix_len": net.prefixlen}]
    if kind == "ip":
        return [{"address": value, "prefix_len": 32}]
    # hostname
    ips = resolved.get(value, [])
    if not ips:
        log.warning("Hostname %r has no resolved IPs; skipping in TCP filter chain.", value)
    return [{"address": ip, "prefix_len": 32} for ip in ips]


def _prepare_envoy_rules(rules: list[Rule], resolved: dict[str, list[str]]) -> dict:
    """Partition rules into http_rules, tcp_rules, grpc_rules, mtls_clusters for Jinja2."""
    http_rules = []
    tcp_chains: dict[tuple, dict] = {}   # keyed by (port_key, cluster_name)
    grpc_chains: dict[tuple, dict] = {}

    def _port_key(rule: Rule) -> tuple:
        p = rule.ports[0]
        return p if isinstance(p, tuple) else (p,)

    def _port_label(rule: Rule) -> str:
        p = rule.ports[0]
        return f"{p[0]}_{p[1]}" if isinstance(p, tuple) else str(p)

    for rule in rules:
        if rule.protocol in ("http", "https"):
            # Collect all destinations as domains; wildcards are fine for HCM matching
            domains = list(rule.destinations)
            for port in rule.ports:
                port_val = port[0] if isinstance(port, tuple) else port
                name = f"http_{_sanitize('_'.join(rule.destinations))}_{port_val}"
                http_rules.append({
                    "name": name,
                    "domains": domains,
                    "port": port_val,
                    "description": rule.description,
                    "envs": list(rule.envs) if rule.envs else [],
                })
        elif rule.protocol == "grpc":
            for port in rule.ports:
                port_key = port if isinstance(port, tuple) else (port,)
                cluster = (
                    f"mtls_{_sanitize(rule.destinations[0])}_{_sanitize(str(port_key[0]))}"
                    if rule.tls else "original_dst"
                )
                chain_key = (port_key, cluster)
                if chain_key not in grpc_chains:
                    stat = f"{_sanitize(rule.destinations[0])}_{_sanitize(str(port_key[0]))}"
                    grpc_chains[chain_key] = {
                        "name": f"grpc_{stat}",
                        "stat_name": stat,
                        "description": rule.description,
                        "port": port_key[0] if len(port_key) == 1 else None,
                        "port_range": {"start": port_key[0], "end": port_key[1]} if len(port_key) == 2 else None,
                        "cluster_name": cluster,
                        "ip_addresses": [],
                        "tls": rule.tls,
                        "sni": next((d for d in rule.destinations if classify(d)[0] == "hostname"), None),
                        "upstream_port": port_key[0],
                        "is_grpc": True,
                    }
                for dest in rule.destinations:
                    grpc_chains[chain_key]["ip_addresses"].extend(
                        _ip_entries_for_dest(dest, resolved)
                    )
        else:  # tcp
            for port in rule.ports:
                port_key = port if isinstance(port, tuple) else (port,)
                cluster = (
                    f"mtls_{_sanitize(rule.destinations[0])}_{_sanitize(str(port_key[0]))}"
                    if rule.tls else "original_dst"
                )
                chain_key = (port_key, cluster)
                if chain_key not in tcp_chains:
                    stat = f"{_sanitize(rule.destinations[0])}_{_sanitize(str(port_key[0]))}"
                    tcp_chains[chain_key] = {
                        "name": f"tcp_{stat}",
                        "stat_name": stat,
                        "description": rule.description,
                        "port": port_key[0] if len(port_key) == 1 else None,
                        "port_range": {"start": port_key[0], "end": port_key[1]} if len(port_key) == 2 else None,
                        "cluster_name": cluster,
                        "ip_addresses": [],
                        "tls": rule.tls,
                        "sni": next((d for d in rule.destinations if classify(d)[0] == "hostname"), None),
                        "upstream_port": port_key[0],
                        "is_grpc": False,
                    }
                for dest in rule.destinations:
                    tcp_chains[chain_key]["ip_addresses"].extend(
                        _ip_entries_for_dest(dest, resolved)
                    )

    # Dedupe IPs within each chain and sort deterministically
    def _dedup_ips(chain: dict) -> dict:
        seen = set()
        deduped = []
        for entry in chain["ip_addresses"]:
            key = (entry["address"], entry["prefix_len"])
            if key not in seen:
                seen.add(key)
                deduped.append(entry)
        chain["ip_addresses"] = sorted(deduped, key=lambda e: (e["address"], e["prefix_len"]))
        return chain

    tcp_rules = [_dedup_ips(c) for c in tcp_chains.values() if c["ip_addresses"]]
    grpc_rules = [_dedup_ips(c) for c in grpc_chains.values() if c["ip_addresses"]]

    # Sort filter chains deterministically
    def _chain_sort_key(c: dict) -> tuple:
        port = c["port"] or (c["port_range"]["start"] if c["port_range"] else 0)
        first_ip = c["ip_addresses"][0]["address"] if c["ip_addresses"] else ""
        return (port, first_ip)

    tcp_rules.sort(key=_chain_sort_key)
    grpc_rules.sort(key=_chain_sort_key)

    # Collect mTLS clusters (unique by cluster_name)
    mtls_seen: set[str] = set()
    mtls_clusters = []
    for chain in [*tcp_rules, *grpc_rules]:
        if chain["tls"] and chain["cluster_name"] not in mtls_seen:
            if not chain["sni"]:
                log.warning(
                    "mTLS rule %r has no hostname destination; SNI will be empty and peer cert verification may fail.",
                    chain["description"] or chain["cluster_name"],
                )
            mtls_seen.add(chain["cluster_name"])
            mtls_clusters.append(chain)

    return {
        "http_rules": http_rules,
        "tcp_rules": tcp_rules,
        "grpc_rules": grpc_rules,
        "mtls_clusters": mtls_clusters,
    }


def _render_template(template_path: Path, context: dict) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined
    except ImportError as exc:
        raise ConfigError(
            "jinja2 is required for --format envoy. Run: pip install jinja2"
        ) from exc
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    return env.get_template(template_path.name).render(**context)


def _wrap_in_configmap(
    name: str,
    namespace: str,
    data_key: str,
    content: str,
    labels: dict[str, str] | None = None,
) -> str:
    obj: dict = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace},
        "data": {data_key: content},
    }
    if labels:
        obj["metadata"]["labels"] = labels
    return yaml.dump(obj, sort_keys=False, default_flow_style=False, allow_unicode=True)


def build_envoy_config(
    app: str,
    env: str,
    rules: list[Rule],
    resolved: dict[str, list[str]],
    template_dir: Path,
) -> str:
    context = _prepare_envoy_rules(rules, resolved)
    context["target_env"] = env
    context["app"] = app
    return _render_template(template_dir / "envoy.yaml.j2", context)


def _build_no_proxy() -> str:
    """Return NO_PROXY value: only k8s internals. Never include allowlist HTTP destinations."""
    return (
        "localhost,127.0.0.1,"
        "${POD_CIDR:-10.244.0.0/16},"
        "${SERVICE_CIDR:-10.96.0.0/12},"
        ".svc,.svc.cluster.local,.cluster.local"
    )


def build_proxy_env_configmap(app: str, env: str) -> str:
    proxy_url = "http://127.0.0.1:15000"
    no_proxy = _build_no_proxy()
    obj = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"envoy-proxy-env-{env}",
            "namespace": f"{app}-{env}",
            "labels": {"app": app, "env": env, "managed-by": "egress-generator"},
        },
        "data": {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "NO_PROXY": no_proxy,
            "no_proxy": no_proxy,
        },
    }
    return yaml.dump(obj, sort_keys=False, default_flow_style=False, allow_unicode=True)


def build_iptables_init(template_dir: Path) -> str:
    return _render_template(template_dir / "iptables-init.sh.j2", {})


def write_envoy_outputs(
    out_dir: Path | str,
    app: str,
    envs: list[str],
    rules_by_env: dict[str, list[Rule]],
    resolved: dict[str, list[str]],
    template_dir: Path,
) -> None:
    out = Path(out_dir)
    envoy_dir = out / "envoy"
    envoy_dir.mkdir(parents=True, exist_ok=True)

    # Shared iptables-init script (allowlist-agnostic)
    iptables_script = build_iptables_init(template_dir)
    (envoy_dir / "iptables-init.sh").write_text(iptables_script)

    # Shared iptables ConfigMap (namespace-neutral placeholder)
    iptables_cm = _wrap_in_configmap(
        name="envoy-iptables-init",
        namespace=app,
        data_key="iptables-init.sh",
        content=iptables_script,
        labels={"managed-by": "egress-generator"},
    )
    (envoy_dir / "iptables-configmap.yaml").write_text(iptables_cm)

    for env in sorted(envs):
        rules = rules_by_env[env]

        # Envoy config per env
        envoy_yaml_content = build_envoy_config(app, env, rules, resolved, template_dir)
        envoy_cm = _wrap_in_configmap(
            name="envoy-egress-config",
            namespace=f"{app}-{env}",
            data_key="envoy.yaml",
            content=envoy_yaml_content,
            labels={"app": app, "env": env, "managed-by": "egress-generator"},
        )
        (envoy_dir / f"envoy-config-{env}.yaml").write_text(envoy_cm)

        # Proxy env ConfigMap per env
        proxy_cm = build_proxy_env_configmap(app, env)
        (envoy_dir / f"proxy-env-{env}.yaml").write_text(proxy_cm)

    # DNS audit trail (shared with calico/kubernetes formats)
    sorted_resolved = {k: sorted(v) for k, v in sorted(resolved.items())}
    (out / "resolved-ips.json").write_text(
        _json.dumps(sorted_resolved, indent=2, sort_keys=True) + "\n"
    )


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
    p.add_argument(
        "--format",
        choices=["calico", "kubernetes", "envoy"],
        default="calico",
        help=(
            "Output format: calico (crd.projectcalico.org/v1), "
            "kubernetes (networking.k8s.io/v1), or envoy (ConfigMaps + iptables script)."
        ),
    )
    args = p.parse_args(argv)
    try:
        rules = load_allowlist(args.allowlist)
        # selector only needed for calico/kubernetes formats
        selector = _parse_selectors(args.selector, args.app) if args.format != "envoy" else {}
    except ConfigError as exc:
        log.error("%s", exc)
        return 1
    hostnames = [
        d for r in rules for d in r.destinations if classify(d)[0] == "hostname"
    ]
    resolved, failed = resolve_hostnames(hostnames)
    envs = [e.strip() for e in args.envs.split(",") if e.strip()]

    if args.format == "envoy":
        template_dir = Path(__file__).parent / "templates"
        rules_by_env = {env: filter_by_env(rules, env) for env in envs}
        try:
            write_envoy_outputs(
                args.output_dir, args.app, envs, rules_by_env, resolved, template_dir
            )
        except ConfigError as exc:
            log.error("%s", exc)
            return 1
    else:
        try:
            policies = {
                env: build_policy(
                    args.app, env, filter_by_env(rules, env), selector, resolved, args.format
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
