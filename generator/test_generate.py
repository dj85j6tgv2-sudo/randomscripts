import textwrap
from pathlib import Path

import pytest

from generator.generate import Rule, load_allowlist, ConfigError, classify, resolve_hostnames, filter_by_env, build_policy


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
    assert rules[1].envs is None


def test_load_multiple_ports_list(tmp_path):
    path = write_yaml(tmp_path, """
        egress:
          - destination: 10.0.0.1
            ports: [80, 443]
            protocol: tcp
    """)
    rules = load_allowlist(path)
    assert rules[0].ports == (80, 443)


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
    sel = policy["spec"]["selector"]
    assert 'app == "app"' in sel and 'tier == "api"' in sel and "&&" in sel
