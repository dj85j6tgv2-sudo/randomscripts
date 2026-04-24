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


from generator.generate import classify


def test_classify_ip():
    assert classify("10.0.0.1") == ("ip", "10.0.0.1")


def test_classify_cidr():
    assert classify("10.0.0.0/24") == ("cidr", "10.0.0.0/24")


def test_classify_hostname():
    assert classify("api.example.com") == ("hostname", "api.example.com")


def test_classify_wildcard_is_separate_kind():
    assert classify("*.example.com")[0] == "wildcard"
