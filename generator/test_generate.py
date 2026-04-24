import textwrap
from pathlib import Path

import pytest

from generator.generate import Rule, load_allowlist, ConfigError, classify, resolve_hostnames, filter_by_env


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
