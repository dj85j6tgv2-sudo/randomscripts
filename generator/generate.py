"""Calico NetworkPolicy generator from egress-allowlist.yaml."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

import yaml

Port = Union[int, tuple[int, int]]
VALID_PROTOCOLS = {"tcp", "http", "https", "grpc"}


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
    has_range = "port_range" in entry
    if has_port and has_range:
        raise ConfigError(f"rule sets both port and port_range: {entry!r}")
    if has_port:
        value = entry["port"]
        if not isinstance(value, int):
            raise ConfigError(f"port must be int: {entry!r}")
        return (value,)
    if has_range:
        pr = entry["port_range"]
        if not (isinstance(pr, dict) and isinstance(pr.get("start"), int)
                and isinstance(pr.get("end"), int)):
            raise ConfigError(f"port_range must be {{start,end}} ints: {entry!r}")
        if pr["start"] > pr["end"]:
            raise ConfigError(f"port_range start > end: {entry!r}")
        return ((pr["start"], pr["end"]),)
    raise ConfigError(f"rule has no port/port_range: {entry!r}")


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
