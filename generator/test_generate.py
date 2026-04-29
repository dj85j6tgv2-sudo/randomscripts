import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from generator.generate import (
    Rule,
    TlsConfig,
    load_allowlist,
    ConfigError,
    classify,
    resolve_hostnames,
    filter_by_env,
    build_policy,
    write_outputs,
    _dump_policy,
    _prepare_envoy_rules,
    _build_no_proxy,
    build_envoy_config,
    build_proxy_env_configmap,
    build_iptables_init,
    write_envoy_outputs,
    main,
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


# ---------------------------------------------------------------------------
# Envoy format tests
# ---------------------------------------------------------------------------

def test_rule_has_protocol_and_tls_fields():
    r = Rule(("10.0.0.1",), (443,), None, None)
    assert r.protocol == "tcp"
    assert r.tls is None


def test_rule_accepts_explicit_protocol():
    r = Rule(("api.example.com",), (443,), None, None, protocol="http")
    assert r.protocol == "http"


def test_tls_config_stores_cert_key_ca():
    t = TlsConfig(cert="/etc/envoy/certs/client.crt", key="/etc/envoy/certs/client.key", ca="/etc/envoy/certs/ca.crt")
    assert t.cert == "/etc/envoy/certs/client.crt"
    assert t.key == "/etc/envoy/certs/client.key"
    assert t.ca == "/etc/envoy/certs/ca.crt"


def test_tls_config_ca_optional():
    t = TlsConfig(cert="/c.crt", key="/k.key")
    assert t.ca is None


def test_load_allowlist_parses_protocol(tmp_path):
    path = write_yaml(tmp_path, """
        egress:
          - destination: api.example.com
            port: 443
            protocol: http
    """)
    rules = load_allowlist(path)
    assert rules[0].protocol == "http"


def test_load_allowlist_parses_grpc_protocol(tmp_path):
    path = write_yaml(tmp_path, """
        egress:
          - destination: grpc.example.com
            port: 4266
            protocol: grpc
    """)
    rules = load_allowlist(path)
    assert rules[0].protocol == "grpc"


def test_load_allowlist_parses_tls(tmp_path):
    path = write_yaml(tmp_path, """
        egress:
          - destination: payment.example.com
            port: 8443
            protocol: tcp
            tls:
              cert: /etc/envoy/certs/client.crt
              key: /etc/envoy/certs/client.key
              ca: /etc/envoy/certs/ca.crt
    """)
    rules = load_allowlist(path)
    assert rules[0].tls is not None
    assert rules[0].tls.cert == "/etc/envoy/certs/client.crt"
    assert rules[0].tls.key == "/etc/envoy/certs/client.key"
    assert rules[0].tls.ca == "/etc/envoy/certs/ca.crt"


def test_load_allowlist_tls_missing_key_raises(tmp_path):
    path = write_yaml(tmp_path, """
        egress:
          - destination: payment.example.com
            port: 8443
            protocol: tcp
            tls:
              cert: /etc/envoy/certs/client.crt
    """)
    with pytest.raises(ConfigError, match="tls block requires"):
        load_allowlist(path)


def test_prepare_envoy_rules_http_no_ip_resolution():
    rules = [Rule(("api.example.com",), (443,), None, "External API", protocol="http")]
    ctx = _prepare_envoy_rules(rules, {})
    assert len(ctx["http_rules"]) == 1
    assert ctx["http_rules"][0]["domains"] == ["api.example.com"]
    assert ctx["http_rules"][0]["port"] == 443
    assert ctx["tcp_rules"] == []
    assert ctx["grpc_rules"] == []


def test_prepare_envoy_rules_http_wildcard_passes_through():
    rules = [Rule(("*.example.com",), (443,), None, None, protocol="http")]
    ctx = _prepare_envoy_rules(rules, {})
    assert ctx["http_rules"][0]["domains"] == ["*.example.com"]


def test_prepare_envoy_rules_tcp_resolved_to_ips():
    rules = [Rule(("redis.internal",), (6379,), None, "Redis", protocol="tcp")]
    resolved = {"redis.internal": ["10.0.0.5"]}
    ctx = _prepare_envoy_rules(rules, resolved)
    assert len(ctx["tcp_rules"]) == 1
    chain = ctx["tcp_rules"][0]
    assert chain["ip_addresses"] == [{"address": "10.0.0.5", "prefix_len": 32}]
    assert chain["port"] == 6379
    assert chain["cluster_name"] == "original_dst"


def test_prepare_envoy_rules_tcp_cidr():
    rules = [Rule(("10.20.30.0/24",), (9092,), None, "Kafka", protocol="tcp")]
    ctx = _prepare_envoy_rules(rules, {})
    chain = ctx["tcp_rules"][0]
    assert chain["ip_addresses"] == [{"address": "10.20.30.0", "prefix_len": 24}]


def test_prepare_envoy_rules_tcp_direct_ip():
    rules = [Rule(("10.0.0.1",), (443,), None, None, protocol="tcp")]
    ctx = _prepare_envoy_rules(rules, {})
    assert ctx["tcp_rules"][0]["ip_addresses"] == [{"address": "10.0.0.1", "prefix_len": 32}]


def test_prepare_envoy_rules_grpc_emitted_in_grpc_rules():
    rules = [Rule(("grpc.internal",), (4266,), None, "gRPC API", protocol="grpc")]
    resolved = {"grpc.internal": ["10.1.2.3"]}
    ctx = _prepare_envoy_rules(rules, resolved)
    assert ctx["grpc_rules"] != []
    assert ctx["tcp_rules"] == []
    chain = ctx["grpc_rules"][0]
    assert chain["is_grpc"] is True
    assert chain["ip_addresses"] == [{"address": "10.1.2.3", "prefix_len": 32}]


def test_prepare_envoy_rules_mtls_cluster_emitted():
    tls = TlsConfig(cert="/c.crt", key="/k.key", ca="/ca.crt")
    rules = [Rule(("payment.example.com",), (8443,), None, "Payment GW", protocol="tcp", tls=tls)]
    resolved = {"payment.example.com": ["1.2.3.4"]}
    ctx = _prepare_envoy_rules(rules, resolved)
    assert ctx["mtls_clusters"] != []
    cluster = ctx["mtls_clusters"][0]
    assert cluster["tls"] == tls
    assert cluster["cluster_name"].startswith("mtls_")
    assert cluster["sni"] == "payment.example.com"


def test_prepare_envoy_rules_wildcard_in_tcp_skipped_with_warning(caplog):
    import logging
    rules = [Rule(("*.internal",), (6379,), None, None, protocol="tcp")]
    with caplog.at_level(logging.WARNING, logger="egress.generate"):
        ctx = _prepare_envoy_rules(rules, {})
    assert ctx["tcp_rules"] == []
    assert any("Wildcard" in r.message for r in caplog.records)


def test_prepare_envoy_rules_port_range_preserved():
    rules = [Rule(("10.0.0.1",), ((30000, 30999),), None, None, protocol="tcp")]
    ctx = _prepare_envoy_rules(rules, {})
    chain = ctx["tcp_rules"][0]
    assert chain["port"] is None
    assert chain["port_range"] == {"start": 30000, "end": 30999}


def test_prepare_envoy_rules_multi_port_expands_chains():
    rules = [Rule(("10.0.0.1",), (80, 443), None, None, protocol="tcp")]
    ctx = _prepare_envoy_rules(rules, {})
    assert len(ctx["tcp_rules"]) == 2
    ports = {c["port"] for c in ctx["tcp_rules"]}
    assert ports == {80, 443}


def test_prepare_envoy_rules_dedupes_ips_across_merged_rules():
    rules = [
        Rule(("10.0.0.1",), (443,), None, "Rule A", protocol="tcp"),
        Rule(("10.0.0.1",), (443,), None, "Rule B", protocol="tcp"),  # same IP+port
    ]
    ctx = _prepare_envoy_rules(rules, {})
    # Should be merged into one chain, IP deduplicated
    assert len(ctx["tcp_rules"]) == 1
    assert len(ctx["tcp_rules"][0]["ip_addresses"]) == 1


def test_no_proxy_does_not_include_http_destinations():
    no_proxy = _build_no_proxy()
    # Should only have k8s internals, never specific hostnames/IPs
    assert "example.com" not in no_proxy
    assert "api.github.com" not in no_proxy
    assert "localhost" in no_proxy
    assert ".svc.cluster.local" in no_proxy


def test_no_proxy_includes_k8s_internals():
    no_proxy = _build_no_proxy()
    assert "127.0.0.1" in no_proxy
    assert ".cluster.local" in no_proxy


def test_iptables_init_is_constant_across_calls():
    template_dir = Path(__file__).resolve().parent / "templates"
    s1 = build_iptables_init(template_dir)
    s2 = build_iptables_init(template_dir)
    assert s1 == s2


def test_iptables_init_redirects_all_tcp_to_15001():
    template_dir = Path(__file__).resolve().parent / "templates"
    script = build_iptables_init(template_dir)
    assert "REDIRECT --to-ports 15001" in script
    assert "--uid-owner 1337" in script
    assert "127.0.0.0/8" in script


def test_iptables_init_skips_port_15000():
    template_dir = Path(__file__).resolve().parent / "templates"
    script = build_iptables_init(template_dir)
    assert "--dport 15000" in script
    assert "RETURN" in script


def test_write_envoy_outputs_creates_exactly_expected_files(tmp_path):
    rules = [
        Rule(("api.example.com",), (443,), None, "API", protocol="http"),
        Rule(("10.0.0.5",), (6379,), None, "Redis", protocol="tcp"),
    ]
    template_dir = Path(__file__).resolve().parent / "templates"
    write_envoy_outputs(
        tmp_path, "myapp", ["dev", "prd"],
        {"dev": rules, "prd": rules},
        {"api.example.com": ["1.2.3.4"]},
        template_dir,
    )
    envoy_dir = tmp_path / "envoy"
    assert (envoy_dir / "iptables-init.sh").exists()
    assert (envoy_dir / "iptables-configmap.yaml").exists()
    for env in ("dev", "prd"):
        assert (envoy_dir / f"envoy-config-{env}.yaml").exists()
        assert (envoy_dir / f"proxy-env-{env}.yaml").exists()
    # No deployment patches or kustomization
    assert not list(envoy_dir.glob("deployment-patch*"))
    assert not list(envoy_dir.glob("kustomization*"))
    # DNS audit trail at root
    assert (tmp_path / "resolved-ips.json").exists()


def test_write_envoy_outputs_configmap_is_valid_yaml(tmp_path):
    import yaml as _yaml
    rules = [Rule(("10.0.0.1",), (443,), None, None, protocol="tcp")]
    template_dir = Path(__file__).resolve().parent / "templates"
    write_envoy_outputs(
        tmp_path, "myapp", ["dev"],
        {"dev": rules},
        {},
        template_dir,
    )
    content = (tmp_path / "envoy" / "envoy-config-dev.yaml").read_text()
    obj = _yaml.safe_load(content)
    assert obj["kind"] == "ConfigMap"
    assert "envoy.yaml" in obj["data"]


def test_write_envoy_outputs_proxy_env_configmap_present(tmp_path):
    import yaml as _yaml
    template_dir = Path(__file__).resolve().parent / "templates"
    write_envoy_outputs(
        tmp_path, "myapp", ["dev"],
        {"dev": []},
        {},
        template_dir,
    )
    content = (tmp_path / "envoy" / "proxy-env-dev.yaml").read_text()
    obj = _yaml.safe_load(content)
    assert obj["kind"] == "ConfigMap"
    assert "HTTP_PROXY" in obj["data"]
    assert "HTTPS_PROXY" in obj["data"]
    assert "NO_PROXY" in obj["data"]
    assert "example.com" not in obj["data"]["NO_PROXY"]


def test_write_envoy_outputs_is_deterministic(tmp_path):
    rules = [
        Rule(("api.example.com",), (443,), None, "API", protocol="http"),
        Rule(("10.0.0.5",), (6379,), None, "Redis", protocol="tcp"),
    ]
    template_dir = Path(__file__).resolve().parent / "templates"
    resolved = {"api.example.com": ["1.2.3.4"]}

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    for out in (out1, out2):
        write_envoy_outputs(
            out, "myapp", ["prd"], {"prd": rules}, resolved, template_dir
        )
    for fname in (
        "envoy/envoy-config-prd.yaml",
        "envoy/proxy-env-prd.yaml",
        "envoy/iptables-init.sh",
        "envoy/iptables-configmap.yaml",
        "resolved-ips.json",
    ):
        assert (out1 / fname).read_bytes() == (out2 / fname).read_bytes(), fname


def test_cli_envoy_format_end_to_end(tmp_path, monkeypatch):
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(textwrap.dedent("""
        egress:
          - destination: api.example.com
            port: 443
            protocol: http
            envs: [dev, prd]
          - destination: redis.internal
            port: 6379
            protocol: tcp
            envs: [dev, prd]
          - destination: grpc.internal
            port: 4266
            protocol: grpc
            envs: [prd]
    """))
    out_dir = tmp_path / "out"

    def fake_resolve(host):
        data = {
            "api.example.com": ["9.9.9.9"],
            "redis.internal": ["10.0.0.5"],
            "grpc.internal": ["10.0.0.6"],
        }
        return (host, [], data[host])

    monkeypatch.setattr("generator.generate.socket.gethostbyname_ex", fake_resolve)

    rc = main([
        "--allowlist", str(allowlist),
        "--app", "myapp",
        "--output-dir", str(out_dir),
        "--envs", "dev,prd",
        "--format", "envoy",
    ])
    assert rc == 0
    envoy_dir = out_dir / "envoy"
    for env in ("dev", "prd"):
        assert (envoy_dir / f"envoy-config-{env}.yaml").exists()
        assert (envoy_dir / f"proxy-env-{env}.yaml").exists()
    assert (envoy_dir / "iptables-init.sh").exists()
    assert (envoy_dir / "iptables-configmap.yaml").exists()
    assert (out_dir / "resolved-ips.json").exists()


def test_cli_envoy_format_does_not_write_networkpolicy(tmp_path, monkeypatch):
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(textwrap.dedent("""
        egress:
          - destination: 10.0.0.1
            port: 443
            protocol: tcp
    """))
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        "generator.generate.socket.gethostbyname_ex",
        lambda h: (h, [], []),
    )

    rc = main([
        "--allowlist", str(allowlist),
        "--app", "myapp",
        "--output-dir", str(out_dir),
        "--format", "envoy",
    ])
    assert rc == 0
    assert not list(out_dir.glob("networkpolicy-*.yaml"))
