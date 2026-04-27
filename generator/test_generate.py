import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from generator.generate import (
    Rule,
    load_allowlist,
    ConfigError,
    classify,
    resolve_hostnames,
    filter_by_env,
    build_policy,
    write_outputs,
    _dump_policy,
)


def write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "allowlist.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_load_normalizes_singular_destination(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        egress:
          - destination: 10.0.0.1
            port: 443
            protocol: tcp
            envs: [prd]
    """,
    )
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
    path = write_yaml(
        tmp_path,
        """
        egress:
          - domains: [a.example.com, b.example.com]
            port: 443
            protocol: http
          - destinations: [10.0.0.1, 10.0.0.2]
            port_range: {start: 30000, end: 30999}
            protocol: tcp
    """,
    )
    rules = load_allowlist(path)
    assert rules[0].destinations == ("a.example.com", "b.example.com")
    assert rules[0].ports == (443,)
    assert rules[0].envs is None
    assert rules[1].destinations == ("10.0.0.1", "10.0.0.2")
    assert rules[1].ports == ((30000, 30999),)
    assert rules[1].envs is None


def test_load_multiple_ports_list(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        egress:
          - destination: 10.0.0.1
            ports: [80, 443]
            protocol: tcp
    """,
    )
    rules = load_allowlist(path)
    assert rules[0].ports == (80, 443)


def test_load_rejects_both_destination_and_destinations(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        egress:
          - destination: 10.0.0.1
            destinations: [10.0.0.2]
            port: 1
            protocol: tcp
    """,
    )
    with pytest.raises(ConfigError):
        load_allowlist(path)


def test_load_rejects_unknown_protocol(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        egress:
          - destination: 10.0.0.1
            port: 1
            protocol: quic
    """,
    )
    with pytest.raises(ConfigError):
        load_allowlist(path)


def test_classify_ip():
    assert classify("10.0.0.1") == ("ip", "10.0.0.1")


def test_classify_cidr():
    assert classify("10.0.0.0/24") == ("cidr", "10.0.0.0/24")


def test_classify_hostname():
    assert classify("api.example.com") == ("hostname", "api.example.com")


def test_classify_wildcard_is_separate_kind():
    assert classify("*.example.com")[0] == "wildcard"


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


def test_filter_by_env_keeps_rule_with_matching_env():
    r_prd = Rule(("1.1.1.1",), (1,), frozenset({"prd"}), None)
    r_all = Rule(("2.2.2.2",), (2,), None, None)
    r_dev = Rule(("3.3.3.3",), (3,), frozenset({"dev"}), None)

    assert filter_by_env([r_prd, r_all, r_dev], "prd") == [r_prd, r_all]
    assert filter_by_env([r_prd, r_all, r_dev], "stg") == [r_all]


def test_build_policy_shape_and_dns_and_deny():
    rules = [Rule(("10.0.0.1",), (443,), None, "api")]
    policy = build_policy(
        app="myapp",
        env="prd",
        rules=rules,
        selector={"app": "myapp"},
        resolved={},
    )
    assert policy["apiVersion"] == "crd.projectcalico.org/v1"
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
    assert egress[2]["action"] == "Allow"
    assert egress[2]["protocol"] == "TCP"
    assert egress[2]["destination"] == {"nets": ["10.0.0.1/32"], "ports": [443]}
    assert egress[2]["_comment"] == "api"
    # Last: deny
    assert egress[-1]["action"] == "Deny"
    assert egress[-1]["_comment"] == "default deny"


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
        "1.2.3.4/32",
        "1.2.3.5/32",
    ]


def test_build_policy_skips_rule_with_only_unresolved_hostname():
    rules = [Rule(("unresolved.example.com",), (443,), None, None)]
    policy = build_policy("app", "prd", rules, {"app": "app"}, {})
    # Only DNS allows + deny remain
    actions = [r["action"] for r in policy["spec"]["egress"]]
    assert actions == ["Allow", "Allow", "Deny"]


def test_build_policy_multi_selector_joined_with_and():
    policy = build_policy(
        "app",
        "prd",
        [],
        selector={"app": "app", "tier": "api"},
        resolved={},
    )
    sel = policy["spec"]["selector"]
    assert 'app == "app"' in sel and 'tier == "api"' in sel and "&&" in sel


def test_build_policy_wildcard_destination_skipped():
    """Wildcard destinations are now skipped with a warning instead of raising."""
    rules = [Rule(("*.example.com",), (443,), None, None)]
    policy = build_policy("app", "prd", rules, {"app": "app"}, {})
    # Only DNS allows + deny remain (wildcard rule omitted)
    actions = [r["action"] for r in policy["spec"]["egress"]]
    assert actions == ["Allow", "Allow", "Deny"]


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
    policies = {
        "prd": build_policy(
            "app", "prd", [Rule(("10.0.0.1",), (443,), None, None)], {"app": "app"}, {}
        )
    }
    resolved = {"z.example.com": ["9.9.9.9"], "a.example.com": ["1.1.1.1"]}

    write_outputs(tmp_path, policies, resolved)
    first_yaml = (tmp_path / "networkpolicy-prd.yaml").read_bytes()
    first_json = (tmp_path / "resolved-ips.json").read_bytes()

    write_outputs(tmp_path, policies, resolved)
    assert (tmp_path / "networkpolicy-prd.yaml").read_bytes() == first_yaml
    assert (tmp_path / "resolved-ips.json").read_bytes() == first_json


REPO = Path(__file__).resolve().parents[1]


def test_cli_end_to_end(tmp_path, monkeypatch):
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(
        textwrap.dedent("""
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
    """)
    )
    out_dir = tmp_path / "out"

    def fake_resolve(host):
        data = {"api.example.com": ["9.9.9.9", "8.8.8.8"]}
        return (host, [], data[host])

    monkeypatch.setattr("generator.generate.socket.gethostbyname_ex", fake_resolve)

    from generator.generate import main

    rc = main(
        [
            "--allowlist",
            str(allowlist),
            "--app",
            "myapp",
            "--output-dir",
            str(out_dir),
            "--envs",
            "dev,stg,prd",
        ]
    )
    assert rc == 0
    for env_name in ("dev", "stg", "prd"):
        assert (out_dir / f"networkpolicy-{env_name}.yaml").exists()
    assert (out_dir / "resolved-ips.json").exists()


def test_cli_exit_code_2_on_dns_failure(tmp_path):
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(
        textwrap.dedent("""
        egress:
          - destination: this-will-not-resolve.invalid.
            port: 443
            protocol: tcp
    """)
    )
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "generator.generate",
            "--allowlist",
            str(allowlist),
            "--app",
            "myapp",
            "--output-dir",
            str(out_dir),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "this-will-not-resolve.invalid." in result.stderr


def test_generate_is_byte_deterministic(tmp_path, monkeypatch):
    def fake(host):
        return (host, [], ["9.9.9.9", "8.8.8.8"])

    monkeypatch.setattr("generator.generate.socket.gethostbyname_ex", fake)

    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(
        textwrap.dedent("""
        egress:
          - destinations: [b.example.com, a.example.com]
            port: 443
            protocol: http
          - destination: 10.0.0.1
            port: 443
            protocol: tcp
    """)
    )

    from generator.generate import main

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    assert (
        main(["--allowlist", str(allowlist), "--app", "app", "--output-dir", str(out1)])
        == 0
    )
    assert (
        main(["--allowlist", str(allowlist), "--app", "app", "--output-dir", str(out2)])
        == 0
    )

    for name in (
        "networkpolicy-dev.yaml",
        "networkpolicy-stg.yaml",
        "networkpolicy-prd.yaml",
        "resolved-ips.json",
    ):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name


def test_dump_policy_includes_comments(tmp_path):
    rules = [Rule(("10.0.0.1",), (443,), None, "GitHub APIs")]
    policy = build_policy("app", "prd", rules, {"app": "app"}, {})
    output = _dump_policy(policy)
    assert "# DNS (CoreDNS)" in output
    assert "# GitHub APIs" in output
    assert "# default deny" in output
    # _comment must not appear as a YAML key
    assert "_comment" not in output.replace("# ", "")


def test_build_policy_kubernetes_format_shape(monkeypatch):
    rules = [Rule(("10.0.0.1",), (443,), None, "api")]
    policy = build_policy(
        app="myapp", env="prd", rules=rules,
        selector={"app": "myapp"}, resolved={}, fmt="kubernetes",
    )
    assert policy["apiVersion"] == "networking.k8s.io/v1"
    assert policy["spec"]["podSelector"] == {"matchLabels": {"app": "myapp"}}
    assert policy["spec"]["policyTypes"] == ["Egress"]
    egress = policy["spec"]["egress"]
    # DNS rules (no "to" field)
    assert egress[0] == {"ports": [{"port": 53, "protocol": "UDP"}]}
    assert egress[1] == {"ports": [{"port": 53, "protocol": "TCP"}]}
    # Our rule
    assert egress[2]["to"] == [{"ipBlock": {"cidr": "10.0.0.1/32"}}]
    assert egress[2]["ports"] == [{"port": 443, "protocol": "TCP"}]
    # No Deny rule
    assert all(r.get("action") != "Deny" for r in egress)


def test_build_policy_kubernetes_port_range(monkeypatch):
    rules = [Rule(("10.0.0.1",), ((30000, 30999),), None, None)]
    policy = build_policy("app", "prd", rules, {"app": "app"}, {}, fmt="kubernetes")
    egress = policy["spec"]["egress"]
    assert egress[2]["ports"] == [{"port": 30000, "endPort": 30999, "protocol": "TCP"}]


def test_build_policy_default_format_is_calico():
    rules = [Rule(("10.0.0.1",), (443,), None, None)]
    policy = build_policy("app", "prd", rules, {"app": "app"}, {})
    assert policy["apiVersion"] == "crd.projectcalico.org/v1"


def test_build_policy_annotations_visible_in_metadata():
    rules = [
        Rule(("10.0.0.1",), (443,), None, "GitHub APIs"),
        Rule(("172.16.0.0/16",), ((30000, 30999),), None, None),
        Rule(("api.example.com",), (443,), None, "External API"),
    ]
    resolved = {"api.example.com": ["1.2.3.4", "1.2.3.5"]}
    for fmt in ("calico", "kubernetes"):
        policy = build_policy("app", "prd", rules, {"app": "app"}, resolved, fmt=fmt)
        ann = policy["metadata"]["annotations"]["egress.policy/rules"]
        assert "10.0.0.1:443 - GitHub APIs" in ann
        assert "172.16.0.0/16:30000-30999" in ann
        assert "api.example.com [1.2.3.4, 1.2.3.5]:443 - External API" in ann
