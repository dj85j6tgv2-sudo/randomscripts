# Calico Egress Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Envoy-sidecar egress allowlist with a Python generator that produces per-environment Calico OSS `NetworkPolicy` resources from the existing `egress-allowlist.yaml`.

**Architecture:** A single-module generator (`generator/generate.py`) loads the YAML, normalizes rules, classifies destinations, resolves hostnames via `socket.gethostbyname_ex()`, filters per env, and emits deterministic `projectcalico.org/v3` NetworkPolicy YAMLs plus a `resolved-ips.json` audit file. A GitHub Actions workflow runs it daily and commits diffs. A bash script aids removing the Envoy sidecar from downstream Helm/Kustomize trees. The existing Envoy generator is left untouched.

**Tech Stack:** Python 3.11, `pyyaml`, `pytest`, `ruff`, stdlib `socket`/`ipaddress`; GitHub Actions; `yq` v4 for the bash helper.

---

## File Structure

| Path | Responsibility |
|---|---|
| `generator/generate.py` | The generator (parse → classify → resolve → filter → build → write). Single module, < 200 lines. |
| `generator/test_generate.py` | pytest suite (11 cases). |
| `generator/__init__.py` | Empty; makes `generator` importable for tests. |
| `generator/requirements.txt` | `pyyaml`, `pytest`. |
| `out/networkpolicy-<env>.yaml` | Generated; checked in. |
| `out/resolved-ips.json` | Generated audit; checked in. |
| `.github/workflows/refresh-egress-policies.yml` | Daily refresh workflow. |
| `scripts/remove-envoy-sidecar.sh` | Migration helper. |
| `README.md` | Updated with Calico-generator sections. |

---

## Task 1: Repo scaffolding

**Files:**
- Create: `generator/__init__.py`
- Create: `generator/requirements.txt`
- Modify: `.gitignore` (ensure `__pycache__/` and `.pytest_cache/` are ignored; append if missing)

- [ ] **Step 1: Create package marker**

Write empty file `generator/__init__.py`.

- [ ] **Step 2: Create requirements**

Write `generator/requirements.txt`:
```
pyyaml==6.0.2
pytest==8.3.3
```

- [ ] **Step 3: Ensure gitignore covers caches**

Check `.gitignore`; if `__pycache__/` and `.pytest_cache/` are absent, append:
```
__pycache__/
.pytest_cache/
*.pyc
```

- [ ] **Step 4: Commit**

```bash
git add generator/__init__.py generator/requirements.txt .gitignore
git commit -m "chore: scaffold generator package"
```

---

## Task 2: Rule dataclass + `load_allowlist`

**Files:**
- Create: `generator/generate.py`
- Create: `generator/test_generate.py`

- [ ] **Step 1: Write the failing test**

Create `generator/test_generate.py`:
```python
import textwrap
from pathlib import Path

import pytest

from generator.generate import Rule, load_allowlist, ConfigError


def write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "allowlist.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_load_normalizes_singular_destination(tmp_path):
    path = write_yaml(tmp_path, """
        egress:
          - destination: 10.0.0.1
            port: 443
            protocol: tcp
            envs: [prd]
    """)
    rules = load_allowlist(path)
    assert rules == [
        Rule(
            destinations=("10.0.0.1",),
            ports=(443,),
            envs=frozenset({"prd"}),
            description=None,
        )
    ]


def test_load_collapses_domains_and_destinations(tmp_path):
    path = write_yaml(tmp_path, """
        egress:
          - domains: [a.example.com, b.example.com]
            port: 443
            protocol: http
          - destinations: [10.0.0.1, 10.0.0.2]
            port_range: {start: 30000, end: 30999}
            protocol: tcp
    """)
    rules = load_allowlist(path)
    assert rules[0].destinations == ("a.example.com", "b.example.com")
    assert rules[0].ports == (443,)
    assert rules[0].envs is None
    assert rules[1].destinations == ("10.0.0.1", "10.0.0.2")
    assert rules[1].ports == ((30000, 30999),)


def test_load_rejects_both_destination_and_destinations(tmp_path):
    path = write_yaml(tmp_path, """
        egress:
          - destination: 10.0.0.1
            destinations: [10.0.0.2]
            port: 1
            protocol: tcp
    """)
    with pytest.raises(ConfigError):
        load_allowlist(path)


def test_load_rejects_unknown_protocol(tmp_path):
    path = write_yaml(tmp_path, """
        egress:
          - destination: 10.0.0.1
            port: 1
            protocol: quic
    """)
    with pytest.raises(ConfigError):
        load_allowlist(path)
```

- [ ] **Step 2: Run test, verify failure**

Run: `cd /Users/ductuananhnguyen/Documents/workspace/github/randomscripts && python -m pytest generator/test_generate.py -v`
Expected: ImportError / ModuleNotFoundError for `generator.generate`.

- [ ] **Step 3: Write minimal implementation**

Create `generator/generate.py`:
```python
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
```

- [ ] **Step 4: Run tests, verify all pass**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add generator/generate.py generator/test_generate.py
git commit -m "feat(generator): load and normalize egress allowlist rules"
```

---

## Task 3: Destination classification + wildcard rejection

**Files:**
- Modify: `generator/generate.py`
- Modify: `generator/test_generate.py`

- [ ] **Step 1: Append failing tests**

Append to `generator/test_generate.py`:
```python
from generator.generate import classify


def test_classify_ip():
    assert classify("10.0.0.1") == ("ip", "10.0.0.1")


def test_classify_cidr():
    assert classify("10.0.0.0/24") == ("cidr", "10.0.0.0/24")


def test_classify_hostname():
    assert classify("api.example.com") == ("hostname", "api.example.com")


def test_classify_wildcard_is_separate_kind():
    assert classify("*.example.com")[0] == "wildcard"
```

- [ ] **Step 2: Run tests, verify failure**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 4 new tests fail (`ImportError` for `classify`).

- [ ] **Step 3: Implement**

In `generator/generate.py`, add after the imports:
```python
import ipaddress
from typing import Literal

Kind = Literal["ip", "cidr", "hostname", "wildcard"]


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
```

- [ ] **Step 4: Run tests, verify all pass**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add generator/generate.py generator/test_generate.py
git commit -m "feat(generator): classify destinations as ip/cidr/hostname/wildcard"
```

---

## Task 4: Hostname resolution with warnings and failures

**Files:**
- Modify: `generator/generate.py`
- Modify: `generator/test_generate.py`

- [ ] **Step 1: Append failing tests**

Append to `generator/test_generate.py`:
```python
from generator.generate import resolve_hostnames


def test_resolve_hostnames_success(monkeypatch, caplog):
    def fake(host):
        return (host, [], {"a.example.com": ["1.2.3.4", "1.2.3.5"]}[host])
    monkeypatch.setattr("generator.generate.socket.gethostbyname_ex", fake)

    resolved, failed = resolve_hostnames(["a.example.com"])
    assert resolved == {"a.example.com": ["1.2.3.4", "1.2.3.5"]}
    assert failed == []


def test_resolve_hostnames_sorts_ips(monkeypatch):
    def fake(host):
        return (host, [], ["10.0.0.5", "10.0.0.1", "10.0.0.3"])
    monkeypatch.setattr("generator.generate.socket.gethostbyname_ex", fake)

    resolved, _ = resolve_hostnames(["x.example.com"])
    assert resolved["x.example.com"] == ["10.0.0.1", "10.0.0.3", "10.0.0.5"]


def test_resolve_hostnames_failure(monkeypatch, caplog):
    import socket as _socket

    def fake(host):
        raise _socket.gaierror("boom")
    monkeypatch.setattr("generator.generate.socket.gethostbyname_ex", fake)

    resolved, failed = resolve_hostnames(["nope.example.com"])
    assert resolved == {}
    assert failed == ["nope.example.com"]
```

- [ ] **Step 2: Run tests, verify failure**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 3 new failures (missing `resolve_hostnames`).

- [ ] **Step 3: Implement**

In `generator/generate.py`, add near the top:
```python
import logging
import socket

log = logging.getLogger("egress.generate")
```

And add:
```python
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
```

- [ ] **Step 4: Run tests, verify all pass**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add generator/generate.py generator/test_generate.py
git commit -m "feat(generator): resolve hostnames with WARN on failure"
```

---

## Task 5: Env filtering

**Files:**
- Modify: `generator/generate.py`
- Modify: `generator/test_generate.py`

- [ ] **Step 1: Append failing test**

Append to `generator/test_generate.py`:
```python
from generator.generate import filter_by_env


def test_filter_by_env_keeps_rule_with_matching_env():
    r_prd = Rule(("1.1.1.1",), (1,), frozenset({"prd"}), None)
    r_all = Rule(("2.2.2.2",), (2,), None, None)
    r_dev = Rule(("3.3.3.3",), (3,), frozenset({"dev"}), None)

    assert filter_by_env([r_prd, r_all, r_dev], "prd") == [r_prd, r_all]
    assert filter_by_env([r_prd, r_all, r_dev], "stg") == [r_all]
```

- [ ] **Step 2: Run test, verify failure**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 1 new failure.

- [ ] **Step 3: Implement**

Append to `generator/generate.py`:
```python
def filter_by_env(rules: list[Rule], env: str) -> list[Rule]:
    return [r for r in rules if r.envs is None or env in r.envs]
```

- [ ] **Step 4: Run tests, verify all pass**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add generator/generate.py generator/test_generate.py
git commit -m "feat(generator): filter rules by environment"
```

---

## Task 6: Build NetworkPolicy document

**Files:**
- Modify: `generator/generate.py`
- Modify: `generator/test_generate.py`

- [ ] **Step 1: Append failing tests**

Append to `generator/test_generate.py`:
```python
from generator.generate import build_policy


def test_build_policy_shape_and_dns_and_deny():
    rules = [Rule(("10.0.0.1",), (443,), None, "api")]
    policy = build_policy(
        app="myapp", env="prd", rules=rules,
        selector={"app": "myapp"}, resolved={},
    )
    assert policy["apiVersion"] == "projectcalico.org/v3"
    assert policy["kind"] == "NetworkPolicy"
    assert policy["metadata"]["name"] == "myapp-egress"
    assert policy["metadata"]["namespace"] == "myapp-prd"
    assert policy["spec"]["selector"] == 'app == "myapp"'
    assert policy["spec"]["types"] == ["Egress"]

    egress = policy["spec"]["egress"]
    # First two: DNS UDP/53 and TCP/53
    assert egress[0]["action"] == "Allow"
    assert egress[0]["protocol"] == "UDP"
    assert egress[0]["destination"]["ports"] == [53]
    assert egress[1]["protocol"] == "TCP"
    assert egress[1]["destination"]["ports"] == [53]
    # Middle: our rule
    assert egress[2] == {
        "action": "Allow",
        "protocol": "TCP",
        "destination": {"nets": ["10.0.0.1/32"], "ports": [443]},
    }
    # Last: deny
    assert egress[-1] == {"action": "Deny"}


def test_build_policy_cidr_preserved():
    rules = [Rule(("10.20.30.0/24",), (9092,), None, None)]
    policy = build_policy("app", "prd", rules, {"app": "app"}, {})
    assert policy["spec"]["egress"][2]["destination"]["nets"] == ["10.20.30.0/24"]


def test_build_policy_port_range_string():
    rules = [Rule(("10.0.0.1",), ((30000, 30999),), None, None)]
    policy = build_policy("app", "dev", rules, {"app": "app"}, {})
    assert policy["spec"]["egress"][2]["destination"]["ports"] == ["30000:30999"]


def test_build_policy_hostname_expands_to_sorted_32s():
    rules = [Rule(("api.example.com",), (443,), None, None)]
    resolved = {"api.example.com": ["1.2.3.5", "1.2.3.4"]}
    policy = build_policy("app", "prd", rules, {"app": "app"}, resolved)
    assert policy["spec"]["egress"][2]["destination"]["nets"] == [
        "1.2.3.4/32", "1.2.3.5/32",
    ]


def test_build_policy_skips_rule_with_only_unresolved_hostname():
    rules = [Rule(("unresolved.example.com",), (443,), None, None)]
    policy = build_policy("app", "prd", rules, {"app": "app"}, {})
    # Only DNS allows + deny remain
    actions = [r["action"] for r in policy["spec"]["egress"]]
    assert actions == ["Allow", "Allow", "Deny"]


def test_build_policy_multi_selector_joined_with_and():
    policy = build_policy(
        "app", "prd", [], selector={"app": "app", "tier": "api"}, resolved={},
    )
    # Calico selector syntax: k == "v" && k2 == "v2"
    sel = policy["spec"]["selector"]
    assert 'app == "app"' in sel and 'tier == "api"' in sel and "&&" in sel
```

- [ ] **Step 2: Run tests, verify failure**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 6 new failures.

- [ ] **Step 3: Implement**

Append to `generator/generate.py`:
```python
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add generator/generate.py generator/test_generate.py
git commit -m "feat(generator): build Calico NetworkPolicy with DNS/deny"
```

---

## Task 7: Wildcard rejection surfaces as ConfigError at build time

**Files:**
- Modify: `generator/test_generate.py`

- [ ] **Step 1: Append failing test**

Append to `generator/test_generate.py`:
```python
def test_build_policy_wildcard_destination_raises():
    rules = [Rule(("*.example.com",), (443,), None, None)]
    with pytest.raises(ConfigError):
        build_policy("app", "prd", rules, {"app": "app"}, {})
```

- [ ] **Step 2: Run test, verify pass (already raises from classify)**

Run: `python -m pytest generator/test_generate.py::test_build_policy_wildcard_destination_raises -v`
Expected: PASS (behavior already implemented in Task 6 via `_nets_for_destination`).

- [ ] **Step 3: Commit**

```bash
git add generator/test_generate.py
git commit -m "test(generator): wildcard destinations rejected at build time"
```

---

## Task 8: Write outputs (YAML + resolved-ips.json)

**Files:**
- Modify: `generator/generate.py`
- Modify: `generator/test_generate.py`

- [ ] **Step 1: Append failing tests**

Append to `generator/test_generate.py`:
```python
import json
from generator.generate import write_outputs


def test_write_outputs_creates_files(tmp_path):
    policies = {
        "dev": build_policy("app", "dev", [], {"app": "app"}, {}),
        "prd": build_policy("app", "prd", [], {"app": "app"}, {}),
    }
    resolved = {"api.example.com": ["1.2.3.4"]}
    write_outputs(tmp_path, policies, resolved)

    for env in ("dev", "prd"):
        path = tmp_path / f"networkpolicy-{env}.yaml"
        assert path.exists()
        text = path.read_text()
        assert "kind: NetworkPolicy" in text
        assert f"namespace: app-{env}" in text

    data = json.loads((tmp_path / "resolved-ips.json").read_text())
    assert data == {"api.example.com": ["1.2.3.4"]}


def test_write_outputs_is_deterministic(tmp_path):
    policies = {"prd": build_policy("app", "prd",
                                    [Rule(("10.0.0.1",), (443,), None, None)],
                                    {"app": "app"}, {})}
    resolved = {"z.example.com": ["9.9.9.9"], "a.example.com": ["1.1.1.1"]}

    write_outputs(tmp_path, policies, resolved)
    first_yaml = (tmp_path / "networkpolicy-prd.yaml").read_bytes()
    first_json = (tmp_path / "resolved-ips.json").read_bytes()

    write_outputs(tmp_path, policies, resolved)
    assert (tmp_path / "networkpolicy-prd.yaml").read_bytes() == first_yaml
    assert (tmp_path / "resolved-ips.json").read_bytes() == first_json
```

- [ ] **Step 2: Run tests, verify failure**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 2 new failures.

- [ ] **Step 3: Implement**

Append to `generator/generate.py`:
```python
import json as _json


def write_outputs(out_dir: Path | str, policies: dict[str, dict],
                  resolved: dict[str, list[str]]) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for env, policy in sorted(policies.items()):
        (out / f"networkpolicy-{env}.yaml").write_text(
            yaml.safe_dump(policy, sort_keys=False, default_flow_style=False)
        )
    sorted_resolved = {k: sorted(v) for k, v in sorted(resolved.items())}
    (out / "resolved-ips.json").write_text(
        _json.dumps(sorted_resolved, indent=2, sort_keys=True) + "\n"
    )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 21 passed.

- [ ] **Step 5: Commit**

```bash
git add generator/generate.py generator/test_generate.py
git commit -m "feat(generator): write NetworkPolicy YAMLs and resolved-ips.json"
```

---

## Task 9: CLI entrypoint

**Files:**
- Modify: `generator/generate.py`
- Modify: `generator/test_generate.py`

- [ ] **Step 1: Append failing CLI test**

Append to `generator/test_generate.py`:
```python
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]


def test_cli_end_to_end(tmp_path, monkeypatch):
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(textwrap.dedent("""
        egress:
          - destination: 10.0.0.1
            port: 443
            protocol: tcp
            envs: [prd]
          - destination: 172.16.0.0/16
            port_range: {start: 30000, end: 30999}
            protocol: tcp
          - destinations: [api.example.com]
            port: 443
            protocol: http
            envs: [dev, stg, prd]
    """))
    out_dir = tmp_path / "out"

    fake_script = tmp_path / "fake_resolver.py"
    fake_script.write_text(
        'import socket\n'
        'def _fake(h):\n'
        '    return (h, [], {"api.example.com": ["9.9.9.9", "8.8.8.8"]}[h])\n'
        'socket.gethostbyname_ex = _fake\n'
    )
    env = {**__import__("os").environ, "PYTHONSTARTUP": str(fake_script)}
    result = subprocess.run(
        [sys.executable, "-m", "generator.generate",
         "--allowlist", str(allowlist),
         "--app", "myapp",
         "--output-dir", str(out_dir),
         "--envs", "dev,stg,prd"],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    for env_name in ("dev", "stg", "prd"):
        assert (out_dir / f"networkpolicy-{env_name}.yaml").exists()
    assert (out_dir / "resolved-ips.json").exists()


def test_cli_exit_code_2_on_dns_failure(tmp_path):
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(textwrap.dedent("""
        egress:
          - destination: this-will-not-resolve.invalid.
            port: 443
            protocol: tcp
    """))
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "generator.generate",
         "--allowlist", str(allowlist),
         "--app", "myapp",
         "--output-dir", str(out_dir)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "this-will-not-resolve.invalid." in result.stderr
```

- [ ] **Step 2: Run test, verify failure**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 2 new failures (no `__main__` yet).

- [ ] **Step 3: Implement CLI**

Append to `generator/generate.py`:
```python
import argparse
import sys


def _parse_selectors(items: list[str] | None, app: str) -> dict[str, str]:
    if not items:
        return {"app": app}
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ConfigError(f"--selector must be key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr,
    )
    p = argparse.ArgumentParser(prog="generate")
    p.add_argument("--allowlist", required=True, type=Path)
    p.add_argument("--app", required=True)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--selector", action="append",
                   help="key=value (repeatable). Default: app=<--app>.")
    p.add_argument("--envs", default="dev,stg,prd",
                   help="Comma-separated list of environments to emit.")
    args = p.parse_args(argv)

    try:
        rules = load_allowlist(args.allowlist)
        selector = _parse_selectors(args.selector, args.app)
    except ConfigError as exc:
        log.error("%s", exc)
        return 1

    hostnames = [
        d for r in rules for d in r.destinations
        if classify(d)[0] == "hostname"
    ]
    resolved, failed = resolve_hostnames(hostnames)

    envs = [e.strip() for e in args.envs.split(",") if e.strip()]
    policies: dict[str, dict] = {}
    try:
        for env in envs:
            policies[env] = build_policy(
                args.app, env, filter_by_env(rules, env), selector, resolved,
            )
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest generator/test_generate.py -v`
Expected: 23 passed.

Note: the `test_cli_exit_code_2_on_dns_failure` test relies on the real resolver failing for `.invalid.` — this is guaranteed by RFC 6761. If the environment blocks all DNS, the test still fails resolution and exits 2.

- [ ] **Step 5: Commit**

```bash
git add generator/generate.py generator/test_generate.py
git commit -m "feat(generator): CLI entrypoint with env/selector flags"
```

---

## Task 10: Synthetic allowlist end-to-end + determinism validation

**Files:**
- Modify: `generator/test_generate.py` (add a byte-equality determinism test across two runs)

- [ ] **Step 1: Append test**

Append to `generator/test_generate.py`:
```python
def test_generate_is_byte_deterministic(tmp_path, monkeypatch):
    def fake(host):
        return (host, [], ["9.9.9.9", "8.8.8.8"])
    monkeypatch.setattr("generator.generate.socket.gethostbyname_ex", fake)

    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(textwrap.dedent("""
        egress:
          - destinations: [b.example.com, a.example.com]
            port: 443
            protocol: http
          - destination: 10.0.0.1
            port: 443
            protocol: tcp
    """))

    from generator.generate import main
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    assert main(["--allowlist", str(allowlist),
                 "--app", "app", "--output-dir", str(out1)]) == 0
    assert main(["--allowlist", str(allowlist),
                 "--app", "app", "--output-dir", str(out2)]) == 0

    for name in ("networkpolicy-dev.yaml", "networkpolicy-stg.yaml",
                 "networkpolicy-prd.yaml", "resolved-ips.json"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name
```

- [ ] **Step 2: Run test, verify pass**

Run: `python -m pytest generator/test_generate.py::test_generate_is_byte_deterministic -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add generator/test_generate.py
git commit -m "test(generator): byte-level determinism across two runs"
```

---

## Task 11: Generate against the real `egress-allowlist.yaml` (manual validation)

**Files:**
- Create: `out/networkpolicy-dev.yaml`
- Create: `out/networkpolicy-stg.yaml`
- Create: `out/networkpolicy-prd.yaml`
- Create: `out/resolved-ips.json`

Note: The real allowlist contains wildcard domains (`*.monitoring.internal`, commented-out `*.github.com`). Per spec, wildcards are a hard error. Before committing generated output, edit the live `egress-allowlist.yaml` to either remove the wildcard entries or replace them with concrete hostnames.

- [ ] **Step 1: Attempt generation**

Run:
```bash
python -m generator.generate \
  --allowlist egress-allowlist.yaml \
  --app python-app \
  --output-dir out/
```

Expected: either wildcard `ConfigError` (exit 1) or unresolved hostnames (exit 2), because many entries (`*.corp`, `*.internal`, `*.svc.cluster.local`) won't resolve from a dev machine.

- [ ] **Step 2: Decision point — do not commit bad output**

If exit 1 (wildcard): stop and surface to user; they must edit `egress-allowlist.yaml` or decide how to handle wildcards. Leave `out/` uncommitted from this task.

If exit 2 (DNS failures only): the YAMLs are still valid, but many rules will be absent because their hostnames didn't resolve. This is expected when running outside the cluster's network. The CI workflow (Task 12) is the correct place to produce a committed snapshot — it runs inside a runner with the right DNS view.

- [ ] **Step 3: (Conditional) commit if clean**

If the generator exits 0 from this environment, commit:
```bash
git add out/
git commit -m "chore: initial Calico NetworkPolicy snapshot"
```

Otherwise skip the commit and document in the PR that the first clean snapshot will come from the CI run.

---

## Task 12: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/refresh-egress-policies.yml`

- [ ] **Step 1: Write workflow**

Create `.github/workflows/refresh-egress-policies.yml`:
```yaml
name: refresh-egress-policies

on:
  schedule:
    - cron: "0 3 * * *"
  push:
    branches: [main]
    paths:
      - "egress-allowlist.yaml"
      - "generator/**"
      - ".github/workflows/refresh-egress-policies.yml"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r generator/requirements.txt

      - name: Run tests
        run: python -m pytest generator/ -v

      - name: Generate NetworkPolicies
        id: generate
        run: |
          set +e
          python -m generator.generate \
            --allowlist egress-allowlist.yaml \
            --app python-app \
            --output-dir out/ \
            2> generate.log
          echo "rc=$?" >> "$GITHUB_OUTPUT"
          cat generate.log

      - name: Surface unresolved hostnames
        if: steps.generate.outputs.rc == '2'
        run: |
          {
            echo "## Unresolved hostnames"
            echo ""
            echo '```'
            grep -E "Unresolved hostnames|could not be resolved" generate.log || true
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"

      - name: Fail on wildcard / config error
        if: steps.generate.outputs.rc == '1'
        run: |
          echo "::error::Generator config error. See logs."
          exit 1

      - name: Commit refreshed policies
        if: steps.generate.outputs.rc == '0' || steps.generate.outputs.rc == '2'
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          if git diff --quiet out/; then
            echo "No changes."
          else
            git add out/
            git commit -m "chore: refresh egress IPs"
            git push
          fi

      # TODO: apply to clusters
      # - name: kubectl apply dev
      #   run: kubectl --context=dev apply -f out/networkpolicy-dev.yaml
      # - name: kubectl apply stg
      #   run: kubectl --context=stg apply -f out/networkpolicy-stg.yaml
      # - name: kubectl apply prd
      #   run: kubectl --context=prd apply -f out/networkpolicy-prd.yaml

      - name: Fail job if DNS resolution had warnings
        if: steps.generate.outputs.rc == '2'
        run: exit 1
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/refresh-egress-policies.yml
git commit -m "ci: add daily refresh-egress-policies workflow"
```

---

## Task 13: Migration helper script

**Files:**
- Create: `scripts/remove-envoy-sidecar.sh`

- [ ] **Step 1: Write the script**

Create `scripts/remove-envoy-sidecar.sh`:
```bash
#!/usr/bin/env bash
# Conservatively remove the Envoy egress sidecar from a Helm chart or Kustomize tree.
# Writes changes in place and prints a unified diff to stdout for human review.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <dir>" >&2
  exit 64
fi

TARGET_DIR="$1"
if [[ ! -d "$TARGET_DIR" ]]; then
  echo "error: $TARGET_DIR is not a directory" >&2
  exit 64
fi

if ! command -v yq >/dev/null 2>&1; then
  echo "error: yq (v4+) is required" >&2
  exit 127
fi

shopt -s globstar nullglob
changed_any=0

for f in "$TARGET_DIR"/**/*.yaml "$TARGET_DIR"/**/*.yml; do
  # Only touch files that look like pod-spec carriers.
  if ! grep -qE '^(kind: (Deployment|StatefulSet|DaemonSet|Pod|Job|CronJob)\b|^\s*template:)' "$f"; then
    continue
  fi

  before="$(cat "$f")"
  tmp="$(mktemp)"
  cp "$f" "$tmp"

  # Remove envoy sidecar containers (by name).
  yq -i '
    (.spec.template.spec.containers, .spec.containers)?
      |= (select(. != null) | map(select(.name != "envoy" and .name != "envoy-sidecar")))
  ' "$tmp" 2>/dev/null || { echo "WARN: yq failed on $f, leaving unchanged" >&2; rm "$tmp"; continue; }

  # Remove iptables init container (by name).
  yq -i '
    (.spec.template.spec.initContainers, .spec.initContainers)?
      |= (select(. != null) | map(select(.name != "iptables-init" and .name != "envoy-iptables")))
  ' "$tmp" 2>/dev/null || true

  # Remove envoy-config volume mount references and the volume itself.
  yq -i '
    (.. | select(has("volumeMounts")?)).volumeMounts |=
      map(select(.name != "envoy-config" and .name != "envoy-certs"))
  ' "$tmp" 2>/dev/null || true
  yq -i '
    (.spec.template.spec.volumes, .spec.volumes)?
      |= (select(. != null) | map(select(.name != "envoy-config" and .name != "envoy-certs")))
  ' "$tmp" 2>/dev/null || true

  # Remove NET_ADMIN from any remaining init containers' capabilities.add list.
  yq -i '
    (.. | select(has("capabilities")?)).capabilities.add? |=
      (select(. != null) | map(select(. != "NET_ADMIN")))
  ' "$tmp" 2>/dev/null || true

  after="$(cat "$tmp")"
  if [[ "$before" != "$after" ]]; then
    mv "$tmp" "$f"
    echo "--- a/$f"
    echo "+++ b/$f"
    diff -u <(printf '%s\n' "$before") <(printf '%s\n' "$after") || true
    changed_any=1
  else
    rm "$tmp"
  fi
done

if [[ "$changed_any" -eq 0 ]]; then
  echo "No Envoy-related entries identified. If you expected changes, verify structure and names." >&2
fi
```

- [ ] **Step 2: Make executable**

Run: `chmod +x scripts/remove-envoy-sidecar.sh`

- [ ] **Step 3: Smoke-test on a fixture**

```bash
mkdir -p /tmp/envoy-fixture
cat > /tmp/envoy-fixture/deploy.yaml <<'EOF'
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      initContainers:
        - name: iptables-init
          image: iptables:latest
          securityContext:
            capabilities:
              add: [NET_ADMIN]
      containers:
        - name: app
          image: myapp:1.0
        - name: envoy
          image: envoyproxy/envoy:v1.29
      volumes:
        - name: envoy-config
          configMap:
            name: envoy-config
EOF
./scripts/remove-envoy-sidecar.sh /tmp/envoy-fixture
grep -q "name: envoy" /tmp/envoy-fixture/deploy.yaml && { echo "FAIL: envoy still present"; exit 1; }
grep -q "iptables-init" /tmp/envoy-fixture/deploy.yaml && { echo "FAIL: iptables init still present"; exit 1; }
echo "OK"
```

Expected final output: `OK`.

- [ ] **Step 4: Commit**

```bash
git add scripts/remove-envoy-sidecar.sh
git commit -m "feat(scripts): add remove-envoy-sidecar.sh migration helper"
```

---

## Task 14: README update

**Files:**
- Modify: `README.md` (append a new "Calico NetworkPolicy Egress Generator" section; leave existing Envoy doc in place)

- [ ] **Step 1: Append section**

Append to `README.md`:
````markdown

---

## Calico NetworkPolicy Egress Generator

**What this is.** A Python generator that turns `egress-allowlist.yaml` (the source of truth) into `projectcalico.org/v3` `NetworkPolicy` resources, one per environment, enforced by Calico OSS at L3/L4. This is replacing the Envoy sidecar + iptables init container model, which kept breaking for protocol-specific reasons (gRPC codec, mTLS cert stripping, ClickHouse streaming).

### How to add a destination

1. Edit `egress-allowlist.yaml`. Add a rule following one of the formats below.
2. Open a PR. CI runs the generator on merge and commits refreshed `out/*.yaml` + `out/resolved-ips.json`.
3. The `kubectl apply` step is currently a TODO in the workflow — apply manually or wire it up per cluster.

### Supported destination types

- **IP** (single address) — emitted as `/32`.
- **CIDR** (e.g. `10.20.30.0/24`) — preserved as-is.
- **Hostname** — resolved daily by CI via `socket.gethostbyname_ex()` and pinned as one or more `/32` rules. Results recorded in `out/resolved-ips.json`.
- **Port** (single int) or **port range** (`{start, end}` → `"start:end"`).
- **Protocol**: `http`, `https`, `tcp`, `grpc` — all map to TCP in Calico.

### Limitations (by design)

- **No FQDN-based allowlisting.** Calico OSS has no FQDN rules. We pin IPs daily.
- **No wildcards.** The generator fails with an error if it sees `*.example.com`. Enumerate explicit hostnames or use a CIDR.
- **No Layer 7.** No Host-header matching, no path routing, no mTLS termination. This is the whole point of moving off Envoy.
- **No GlobalNetworkPolicy / DNS policies / FQDN rules** (Calico Enterprise only).

### Debugging "app can't reach destination X"

1. Is `X` in `egress-allowlist.yaml` with the correct env?
2. For hostnames: does `out/resolved-ips.json` show the IP your app actually tried to reach? (Check `kubectl logs` / `tcpdump`.)
3. Do the app's pod labels match the NetworkPolicy selector? Default is `app=<app-name>`; override with `--selector key=value` at generation time.
4. Is the policy applied in the right namespace (`<app>-<env>`)?

### Cross-cluster notes

Some entries in the historical allowlist use `*.svc.cluster.local` names from other clusters (e.g. Dagster runs in its own cluster). From this cluster's perspective those are *external* targets. If `socket.gethostbyname_ex()` from the CI runner can't resolve them, replace with the target cluster's LoadBalancer/NodePort IP.

### Migration status

| App | Status | Notes |
|---|---|---|
| _tbd_ | not started | — |

### Local use

```bash
pip install -r generator/requirements.txt
python -m generator.generate \
  --allowlist egress-allowlist.yaml \
  --app my-app \
  --selector app=my-app \
  --output-dir out/ \
  --envs dev,stg,prd
```

Exit codes: `0` clean · `1` config error (wildcard, unknown protocol, etc.) · `2` one or more hostnames failed to resolve.
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add Calico NetworkPolicy generator section"
```

---

## Task 15: Final validation

- [ ] **Step 1: Full test suite**

Run: `python -m pytest generator/ -v`
Expected: all tests pass.

- [ ] **Step 2: Ruff format + lint**

Run: `pip install ruff && ruff format generator/ && ruff check generator/`
Fix any complaints; re-commit if formatting changes:
```bash
git add generator/ && git commit -m "style: ruff format generator"
```

- [ ] **Step 3: Line count**

Run: `wc -l generator/generate.py`
Expected: < 200. If over, stop and surface to user before trimming blindly.

- [ ] **Step 4: kubectl dry-run (if available)**

```bash
# Requires a dummy allowlist without unresolvable hostnames
python -m generator.generate --allowlist test-fixtures/synthetic.yaml --app demo --output-dir /tmp/demo-out/ || true
kubectl apply --dry-run=client -f /tmp/demo-out/networkpolicy-dev.yaml 2>&1 | head
```

If `kubectl` isn't available, skip.

- [ ] **Step 5: Announce done**

Summarize:
- All 11 spec test cases covered + 3 integration/CLI tests.
- Byte-deterministic output.
- CI workflow wired; `kubectl apply` left as TODO comment per spec.
- Migration helper smoke-tested on a fixture.
- README updated.
- Existing Envoy generator untouched.

---

## Self-review notes

- Spec §1 (generator requirements): covered across Tasks 2–9. Ports-list variant: spec mentions `ports` (list), but the actual YAML never uses it; `_collect_ports` handles only `port`/`port_range`. If a future allowlist introduces `ports:`, extend `_collect_ports` — currently it will raise `ConfigError`.
- Spec §2 (`resolved-ips.json`): Task 8.
- Spec §3 (tests): covered across Tasks 2–10 and 7. All 11 listed cases present; wildcard test is Task 7.
- Spec §4 (GH Actions): Task 12.
- Spec §5 (README): Task 14.
- Spec §6 (migration helper): Task 13.
- Validation steps: Task 10 (determinism), Task 11 (real allowlist dry-run), Task 15 (kubectl dry-run).
- Out-of-scope items (Squid, iptables, L7 features, Enterprise features): none appear in any task.
