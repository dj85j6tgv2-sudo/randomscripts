# Egress gRPC, mTLS, and port_range Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add gRPC protocol support, mTLS upstream authentication, and fix port_range matching in the Envoy egress control plane.

**Architecture:** Three changes to the existing generator + template system. The Python generator (`generate-envoy-config.py`) gains a `process_grpc_rules()` function and mTLS cluster generation. The Jinja2 template (`envoy.yaml.j2`) gains gRPC filter chain blocks, mTLS cluster blocks, and replaces the RBAC port_range workaround with native `filter_chain_match.port_range`. The allowlist schema adds `protocol: grpc` and an optional `tls` block.

**Tech Stack:** Python 3, PyYAML, Jinja2, Envoy v3 API

**Spec:** `docs/superpowers/specs/2026-03-31-egress-grpc-mtls-portrange-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `egress/generate-envoy-config.py` | Modify | Add `process_grpc_rules()`, mTLS cluster extraction, validation |
| `egress/envoy.yaml.j2` | Modify | Add gRPC filter chains, mTLS clusters, fix port_range |
| `egress/egress-allowlist.yaml` | Modify | Add example gRPC and mTLS rules |
| `egress/test-egress.sh` | Modify | Add gRPC and port_range verification tests |
| `egress/EGRESS-ALLOWLIST-GUIDE.md` | Modify | Document new protocol and tls fields |

---

### Task 1: Fix port_range — Replace RBAC with native filter_chain_match.port_range

This is the simplest change and fixes a real bug. Do it first.

**Files:**
- Modify: `egress/envoy.yaml.j2:146-200`
- Modify: `egress/generate-envoy-config.py:1-15` (docstring)

- [ ] **Step 1: Update the Jinja2 template to use native port_range**

In `egress/envoy.yaml.j2`, replace the TCP filter chain loop (lines 146-200) with:

```jinja2
{% for rule in tcp_rules %}
        # {{ rule.description | default('TCP rule') }} [envs: {{ rule.envs | join(', ') }}]
        - name: "{{ rule.name }}"
          filter_chain_match:
{% if rule.port_range %}
            port_range:
              start: {{ rule.port_range.start }}
              end: {{ rule.port_range.end }}
{% else %}
            destination_port: {{ rule.port }}
{% endif %}
            prefix_ranges:
{% for ip in rule.ip_addresses %}
              - address_prefix: "{{ ip.address }}"
                prefix_len: {{ ip.prefix_len }}
{% endfor %}
          filters:
            - name: envoy.filters.network.tcp_proxy
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.tcp_proxy.v3.TcpProxy
                stat_prefix: "tcp_{{ rule.stat_name }}"
                cluster: {{ rule.cluster_name | default('original_dst') }}
                access_log:
                  - name: envoy.access_loggers.file
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.access_loggers.file.v3.FileAccessLog
                      path: /dev/stdout
                      log_format:
                        json_format:
                          timestamp: "%START_TIME%"
                          listener: "transparent_tcp"
                          environment: "{{ target_env }}"
                          decision: "ALLOWED"
                          rule: "{{ rule.description | default('TCP rule') }}"
                          destination: "%UPSTREAM_HOST%"
                          bytes_rx: "%BYTES_RECEIVED%"
                          bytes_tx: "%BYTES_SENT%"
                          duration_ms: "%DURATION%"

{% endfor %}
```

Key changes from original:
- Removed the RBAC filter block entirely (lines 163-177)
- Removed the comment about RBAC workaround (lines 148-151)
- Added `port_range: {start, end}` in `filter_chain_match` when `rule.port_range` exists
- Added `cluster: {{ rule.cluster_name | default('original_dst') }}` (prep for mTLS in Task 3)

- [ ] **Step 2: Update the docstring in generate-envoy-config.py**

In `egress/generate-envoy-config.py`, replace lines 12-14:

Old:
```python
- Port ranges: filter_chain_match does not support destination_port_range; port ranges
  are enforced via an RBAC network filter (envoy.filters.network.rbac) placed before
  tcp_proxy, using destination_port_range with an exclusive end (allowlist end + 1)
```

New:
```python
- Port ranges: uses native filter_chain_match.port_range (start/end inclusive)
```

- [ ] **Step 3: Regenerate configs and verify RBAC is gone**

Run:
```bash
cd egress && python generate-envoy-config.py --env dev -a egress-allowlist.yaml -t envoy.yaml.j2 -o envoy-dev.yaml
```

Then verify:
```bash
grep -c "rbac" egress/envoy-dev.yaml  # Expected: 0
grep "port_range" egress/envoy-dev.yaml  # Expected: shows port_range block
```

- [ ] **Step 4: Regenerate all environments**

Run:
```bash
cd egress && bash generate-all-envs.sh
```

Verify all three files have `port_range` and no `rbac`:
```bash
for f in egress/envoy-dev.yaml egress/envoy-stg.yaml egress/envoy-prd.yaml; do
  echo "=== $f ==="
  grep -c "rbac" "$f" || echo "rbac count: 0"
  grep -c "port_range" "$f"
done
```

- [ ] **Step 5: Commit**

```bash
git add egress/envoy.yaml.j2 egress/generate-envoy-config.py egress/envoy-dev.yaml egress/envoy-stg.yaml egress/envoy-prd.yaml
git commit -m "fix(egress): replace RBAC workaround with native filter_chain_match.port_range"
```

---

### Task 2: Add gRPC protocol support to the Python generator

**Files:**
- Modify: `egress/generate-envoy-config.py:288-454`

- [ ] **Step 1: Add process_grpc_rules() function**

Add after `process_tcp_rules()` (after line 429) in `egress/generate-envoy-config.py`:

```python
def process_grpc_rules(
    rules: List[Dict], target_env: str, dns_cache: Dict[str, List[str]]
) -> List[Dict]:
    """
    Process gRPC rules for the target environment.

    gRPC rules are similar to TCP rules but generate HTTP/2 filter chains
    instead of raw tcp_proxy. Hostnames are resolved to IPs at generation time.

    Args:
        rules: List of all egress rules
        target_env: Target environment (dev/stg/prd)
        dns_cache: DNS resolution cache

    Returns:
        List of processed gRPC rules
    """
    grpc_rules = []

    for rule in rules:
        protocol = rule.get("protocol", "tcp").lower()
        if protocol != "grpc":
            continue

        if not rule_applies_to_env(rule, target_env):
            continue

        description = rule.get("description", "")
        envs = rule.get("envs", ["dev", "stg", "prd"])

        # Get destinations (single or multiple)
        destinations = []
        if "destination" in rule:
            destinations = [rule["destination"]]
        elif "destinations" in rule:
            destinations = rule["destinations"]
        else:
            print(
                f"WARNING: gRPC rule missing 'destination' or 'destinations', skipping",
                file=sys.stderr,
            )
            continue

        # Get port or port_range
        port = None
        port_range = None
        if "port" in rule:
            port = int(rule["port"])
        elif "port_range" in rule:
            port_range = {
                "start": int(rule["port_range"]["start"]),
                "end": int(rule["port_range"]["end"]),
            }
        else:
            print(
                f"WARNING: gRPC rule missing 'port' or 'port_range', skipping",
                file=sys.stderr,
            )
            continue

        # Resolve all destinations to IPs
        ip_addresses = []
        resolved_destinations = []
        original_hostnames = []

        for destination in destinations:
            if is_ip_or_cidr(destination):
                ip_address, prefix_len = parse_cidr(destination)
                ip_addresses.append(
                    {
                        "address": ip_address,
                        "prefix_len": prefix_len,
                    }
                )
                resolved_destinations.append(destination)
            else:
                original_hostnames.append(destination)
                if destination in dns_cache:
                    ips = dns_cache[destination]
                else:
                    ips = resolve_hostname(destination)
                    dns_cache[destination] = ips

                if not ips:
                    print(
                        f"ERROR: Cannot resolve {destination} for {target_env}, skipping",
                        file=sys.stderr,
                    )
                    continue

                for ip in ips:
                    ip_addresses.append(
                        {
                            "address": ip,
                            "prefix_len": 32,
                        }
                    )
                    resolved_destinations.append(f"{destination}->{ip}")

        if not ip_addresses:
            print(f"WARNING: No valid IPs for gRPC rule, skipping", file=sys.stderr)
            continue

        # Create rule name
        if port:
            port_str = str(port)
        else:
            port_str = f"{port_range['start']}_{port_range['end']}"

        first_dest = destinations[0]
        rule_name = sanitize_name(f"allow_{first_dest}_{port_str}")
        stat_name = sanitize_name(f"{first_dest}_{port_str}")

        grpc_rule = {
            "name": rule_name,
            "description": description,
            "destinations": destinations,
            "ip_addresses": ip_addresses,
            "stat_name": stat_name,
            "envs": envs,
            "original_hostnames": original_hostnames,
        }

        if port:
            grpc_rule["port"] = port
        else:
            grpc_rule["port_range"] = port_range

        # Handle mTLS if tls block present
        tls_config = rule.get("tls")
        if tls_config:
            if "cert" not in tls_config or "key" not in tls_config:
                print(
                    f"ERROR: gRPC rule with tls block missing 'cert' or 'key', skipping",
                    file=sys.stderr,
                )
                continue
            cluster_name = sanitize_name(f"mtls_{first_dest}_{port_str}")
            grpc_rule["tls"] = tls_config
            grpc_rule["cluster_name"] = cluster_name
            grpc_rule["sni"] = original_hostnames[0] if original_hostnames else first_dest
            grpc_rule["upstream_port"] = port if port else port_range["start"]

        grpc_rules.append(grpc_rule)

        # Logging
        dest_summary = ", ".join(resolved_destinations)
        if port:
            print(
                f"[gRPC] {target_env.upper()}: {dest_summary}:{port} ({len(ip_addresses)} IPs)",
                file=sys.stderr,
            )
        else:
            print(
                f"[gRPC] {target_env.upper()}: {dest_summary}:{port_range['start']}-{port_range['end']} ({len(ip_addresses)} IPs)",
                file=sys.stderr,
            )

    return grpc_rules
```

- [ ] **Step 2: Update process_allowlist() to include gRPC rules**

In `egress/generate-envoy-config.py`, update `process_allowlist()` (around line 432):

Old:
```python
def process_allowlist(
    allowlist_path: str, target_env: str
) -> Tuple[List[Dict], List[Dict]]:
    """
    Process allowlist and return (http_rules, tcp_rules) for target environment.

    Args:
        allowlist_path: Path to the allowlist YAML file
        target_env: Target environment (dev/stg/prd)

    Returns:
        Tuple of (http_rules, tcp_rules) lists
    """
    with open(allowlist_path, "r") as f:
        config = yaml.safe_load(f)

    rules = config.get("egress", [])
    dns_cache = {}

    http_rules = process_http_rules(rules, target_env)
    tcp_rules = process_tcp_rules(rules, target_env, dns_cache)

    return http_rules, tcp_rules
```

New:
```python
def process_allowlist(
    allowlist_path: str, target_env: str
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Process allowlist and return (http_rules, tcp_rules, grpc_rules) for target environment.

    Args:
        allowlist_path: Path to the allowlist YAML file
        target_env: Target environment (dev/stg/prd)

    Returns:
        Tuple of (http_rules, tcp_rules, grpc_rules) lists
    """
    with open(allowlist_path, "r") as f:
        config = yaml.safe_load(f)

    rules = config.get("egress", [])
    dns_cache = {}

    http_rules = process_http_rules(rules, target_env)
    tcp_rules = process_tcp_rules(rules, target_env, dns_cache)
    grpc_rules = process_grpc_rules(rules, target_env, dns_cache)

    return http_rules, tcp_rules, grpc_rules
```

- [ ] **Step 3: Update generate_envoy_config() to pass grpc_rules to template**

In `egress/generate-envoy-config.py`, update `generate_envoy_config()`:

Change line 493:
```python
    http_rules, tcp_rules = process_allowlist(allowlist_path, target_env)
```
To:
```python
    http_rules, tcp_rules, grpc_rules = process_allowlist(allowlist_path, target_env)
```

Update the summary print block (around lines 495-501). After the TCP rules print:
```python
    print(f"  gRPC rules: {len(grpc_rules)}", file=sys.stderr)
```

And after `total_ips`:
```python
    total_grpc_ips = sum(len(rule["ip_addresses"]) for rule in grpc_rules)
    print(f"  Total IPs:  {total_ips + total_grpc_ips}", file=sys.stderr)
```

Update the `template.render()` call (line 522-526):
```python
        output = template.render(
            http_rules=http_rules,
            tcp_rules=tcp_rules,
            grpc_rules=grpc_rules,
            target_env=target_env,
        )
```

- [ ] **Step 4: Verify the script still runs without gRPC rules**

Run:
```bash
cd egress && python generate-envoy-config.py --env dev -a egress-allowlist.yaml -t envoy.yaml.j2 -o envoy-dev.yaml
```

Expected: succeeds with `gRPC rules: 0` in output. No errors.

- [ ] **Step 5: Commit**

```bash
git add egress/generate-envoy-config.py
git commit -m "feat(egress): add gRPC rule processing to Python generator"
```

---

### Task 3: Add mTLS support to the Python generator

**Files:**
- Modify: `egress/generate-envoy-config.py:288-429` (process_tcp_rules)
- Modify: `egress/generate-envoy-config.py:432-454` (process_allowlist)
- Modify: `egress/generate-envoy-config.py:457-543` (generate_envoy_config)

- [ ] **Step 1: Add mTLS handling to process_tcp_rules()**

In `egress/generate-envoy-config.py`, in `process_tcp_rules()`, add after the `tcp_rule` dict is built (after line 411 `tcp_rule["port_range"] = port_range`), before `tcp_rules.append(tcp_rule)` (line 413):

```python
        # Handle mTLS if tls block present
        tls_config = rule.get("tls")
        if tls_config:
            if "cert" not in tls_config or "key" not in tls_config:
                print(
                    f"ERROR: TCP rule with tls block missing 'cert' or 'key', skipping",
                    file=sys.stderr,
                )
                continue
            cluster_name = sanitize_name(f"mtls_{first_dest}_{port_str}")
            tcp_rule["tls"] = tls_config
            tcp_rule["cluster_name"] = cluster_name
            # Store original hostname for SNI and STRICT_DNS address
            original_hostnames = [d for d in destinations if not is_ip_or_cidr(d)]
            tcp_rule["sni"] = original_hostnames[0] if original_hostnames else first_dest
            tcp_rule["upstream_port"] = port if port else port_range["start"]
```

- [ ] **Step 2: Add mTLS cluster extraction to process_allowlist()**

Update `process_allowlist()` to extract mTLS clusters from both tcp and grpc rules:

```python
def process_allowlist(
    allowlist_path: str, target_env: str
) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
    """
    Process allowlist and return (http_rules, tcp_rules, grpc_rules, mtls_clusters)
    for target environment.

    Args:
        allowlist_path: Path to the allowlist YAML file
        target_env: Target environment (dev/stg/prd)

    Returns:
        Tuple of (http_rules, tcp_rules, grpc_rules, mtls_clusters) lists
    """
    with open(allowlist_path, "r") as f:
        config = yaml.safe_load(f)

    rules = config.get("egress", [])
    dns_cache = {}

    http_rules = process_http_rules(rules, target_env)
    tcp_rules = process_tcp_rules(rules, target_env, dns_cache)
    grpc_rules = process_grpc_rules(rules, target_env, dns_cache)

    # Extract mTLS clusters from tcp and grpc rules
    mtls_clusters = []
    for rule in tcp_rules + grpc_rules:
        if "tls" in rule:
            mtls_clusters.append(rule)

    return http_rules, tcp_rules, grpc_rules, mtls_clusters
```

- [ ] **Step 3: Update generate_envoy_config() for mTLS clusters**

Update the unpacking:
```python
    http_rules, tcp_rules, grpc_rules, mtls_clusters = process_allowlist(allowlist_path, target_env)
```

Add to summary:
```python
    print(f"  mTLS clusters: {len(mtls_clusters)}", file=sys.stderr)
```

Update `template.render()`:
```python
        output = template.render(
            http_rules=http_rules,
            tcp_rules=tcp_rules,
            grpc_rules=grpc_rules,
            mtls_clusters=mtls_clusters,
            target_env=target_env,
        )
```

- [ ] **Step 4: Add validation for tls on http rules**

In `process_http_rules()`, after the `protocol != "http"` check (around line 241), add validation:

```python
        # Validate: tls block not allowed on HTTP rules
        if "tls" in rule:
            print(
                f"WARNING: 'tls' block on HTTP rule is not supported (HTTP uses CONNECT tunnel), ignoring tls config",
                file=sys.stderr,
            )
```

- [ ] **Step 5: Commit**

```bash
git add egress/generate-envoy-config.py
git commit -m "feat(egress): add mTLS cluster extraction to Python generator"
```

---

### Task 4: Add gRPC filter chains to the Jinja2 template

**Files:**
- Modify: `egress/envoy.yaml.j2:142-201`

- [ ] **Step 1: Add gRPC filter chain loop after TCP rules**

In `egress/envoy.yaml.j2`, after the TCP rules `{% endfor %}` (the one before the DEFAULT DENY comment), add the gRPC filter chain block:

```jinja2
        # ─────────────────────────────────────────
        # ALLOWED gRPC DESTINATIONS ({{ target_env | upper }})
        # ─────────────────────────────────────────
{% for rule in grpc_rules %}
        # {{ rule.description | default('gRPC rule') }} [envs: {{ rule.envs | join(', ') }}]
        - name: "{{ rule.name }}"
          filter_chain_match:
{% if rule.port_range %}
            port_range:
              start: {{ rule.port_range.start }}
              end: {{ rule.port_range.end }}
{% else %}
            destination_port: {{ rule.port }}
{% endif %}
            prefix_ranges:
{% for ip in rule.ip_addresses %}
              - address_prefix: "{{ ip.address }}"
                prefix_len: {{ ip.prefix_len }}
{% endfor %}
          filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                codec_type: HTTP2
                stat_prefix: "grpc_{{ rule.stat_name }}"
                access_log:
                  - name: envoy.access_loggers.file
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.access_loggers.file.v3.FileAccessLog
                      path: /dev/stdout
                      log_format:
                        json_format:
                          timestamp: "%START_TIME%"
                          listener: "grpc"
                          environment: "{{ target_env }}"
                          decision: "ALLOWED"
                          rule: "{{ rule.description | default('gRPC rule') }}"
                          destination: "%UPSTREAM_HOST%"
                          grpc_status: "%GRPC_STATUS%"
                          bytes_rx: "%BYTES_RECEIVED%"
                          bytes_tx: "%BYTES_SENT%"
                          duration_ms: "%DURATION%"
                route_config:
                  name: "grpc_{{ rule.stat_name }}"
                  virtual_hosts:
                    - name: "grpc_{{ rule.stat_name }}"
                      domains: ["*"]
                      routes:
                        - match:
                            prefix: "/"
                          route:
                            cluster: {{ rule.cluster_name | default('original_dst') }}
                            timeout: 0s
                http_filters:
                  - name: envoy.filters.http.router
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
                http2_protocol_options:
                  max_concurrent_streams: 100

{% endfor %}
```

- [ ] **Step 2: Verify template renders without gRPC rules**

Run:
```bash
cd egress && python generate-envoy-config.py --env dev -a egress-allowlist.yaml -t envoy.yaml.j2 -o envoy-dev.yaml
```

Expected: succeeds, no gRPC filter chains in output (no gRPC rules in allowlist yet).

- [ ] **Step 3: Commit**

```bash
git add egress/envoy.yaml.j2
git commit -m "feat(egress): add gRPC HTTP/2 filter chain block to Jinja2 template"
```

---

### Task 5: Add mTLS cluster definitions to the Jinja2 template

**Files:**
- Modify: `egress/envoy.yaml.j2:225-256` (clusters section)

- [ ] **Step 1: Add mTLS cluster loop after existing clusters**

In `egress/envoy.yaml.j2`, after the `blackhole` cluster definition (after line 255), add:

```jinja2

    # ─────────────────────────────────────────
    # mTLS CLUSTERS ({{ target_env | upper }})
    # ─────────────────────────────────────────
{% for rule in mtls_clusters %}
    # {{ rule.description | default('mTLS cluster') }}
    - name: "{{ rule.cluster_name }}"
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      connect_timeout: 10s
{% if rule.get('original_hostnames') %}
      typed_extension_protocol_options:
        envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
          "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
          explicit_http_config:
            http2_protocol_options: {}
{% endif %}
      load_assignment:
        cluster_name: "{{ rule.cluster_name }}"
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {{ rule.sni }}
                      port_value: {{ rule.upstream_port }}
      transport_socket:
        name: envoy.transport_sockets.tls
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
          sni: {{ rule.sni }}
          common_tls_context:
            tls_certificates:
              - certificate_chain:
                  filename: {{ rule.tls.cert }}
                private_key:
                  filename: {{ rule.tls.key }}
{% if rule.tls.ca %}
            validation_context:
              trusted_ca:
                filename: {{ rule.tls.ca }}
{% endif %}

{% endfor %}
```

Wait — there's an issue with the gRPC detection. The `rule.get('original_hostnames')` check is meant to detect gRPC rules (which need H2 upstream), but this is fragile. Let me fix the condition. gRPC rules that have mTLS need `http2_protocol_options`. TCP rules with mTLS do not. The distinguisher is whether the rule came from `grpc_rules` vs `tcp_rules`.

Better approach: in the Python generator, set `rule.is_grpc = True` on gRPC rules so the template can check it.

- [ ] **Step 2: Update process_grpc_rules() to set is_grpc flag**

In `egress/generate-envoy-config.py`, in `process_grpc_rules()`, add to the `grpc_rule` dict:

```python
        grpc_rule = {
            "name": rule_name,
            "description": description,
            "destinations": destinations,
            "ip_addresses": ip_addresses,
            "stat_name": stat_name,
            "envs": envs,
            "original_hostnames": original_hostnames,
            "is_grpc": True,
        }
```

- [ ] **Step 3: Update the mTLS cluster template to use is_grpc**

Replace the `{% if rule.get('original_hostnames') %}` block with:

```jinja2
{% if rule.is_grpc | default(false) %}
      typed_extension_protocol_options:
        envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
          "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
          explicit_http_config:
            http2_protocol_options: {}
{% endif %}
```

- [ ] **Step 4: Verify template renders without mTLS rules**

Run:
```bash
cd egress && python generate-envoy-config.py --env dev -a egress-allowlist.yaml -t envoy.yaml.j2 -o envoy-dev.yaml
```

Expected: succeeds, no mTLS clusters in output.

- [ ] **Step 5: Commit**

```bash
git add egress/envoy.yaml.j2 egress/generate-envoy-config.py
git commit -m "feat(egress): add mTLS cluster definitions to Jinja2 template"
```

---

### Task 6: Add example gRPC and mTLS rules to the allowlist

**Files:**
- Modify: `egress/egress-allowlist.yaml`

- [ ] **Step 1: Add gRPC rules section**

In `egress/egress-allowlist.yaml`, after the TCP IP/CIDR section (after line 187), add:

```yaml

  # ─────────────────────────────────────────────────────────────────────────
  # gRPC destinations (matched by IP, uses HTTP/2 codec)
  # ─────────────────────────────────────────────────────────────────────────

  # Dagster code server - gRPC (scheduler to code-server communication)
  - destination: dagster-code-server.dagster.svc.cluster.local
    port: 4266
    protocol: grpc
    envs: [dev, stg]
    description: "Dagster code server gRPC - DEV/STG"

  - destinations:
      - dagster-code-server-1.dagster.svc.cluster.local
      - dagster-code-server-2.dagster.svc.cluster.local
    port: 4266
    protocol: grpc
    envs: [prd]
    description: "Dagster code servers gRPC - PRD"

  # Jaeger collector - gRPC (tracing)
  - destination: jaeger-collector.tracing.internal
    port: 14250
    protocol: grpc
    envs: [dev, stg, prd]
    description: "Jaeger tracing collector gRPC"
```

- [ ] **Step 2: Add mTLS rules section**

After the gRPC section, add:

```yaml

  # ─────────────────────────────────────────────────────────────────────────
  # mTLS destinations (client certificate authentication)
  # ─────────────────────────────────────────────────────────────────────────

  # Payment gateway - mTLS required
  - destination: payment-gateway.partner.com
    port: 8443
    protocol: tcp
    tls:
      cert: /etc/envoy/certs/payment/client.crt
      key: /etc/envoy/certs/payment/client.key
      ca: /etc/envoy/certs/payment/ca.crt
    envs: [stg, prd]
    description: "Payment gateway mTLS"

  # Secure gRPC endpoint - mTLS + gRPC
  - destination: secure-api.partner.com
    port: 50051
    protocol: grpc
    tls:
      cert: /etc/envoy/certs/partner/client.crt
      key: /etc/envoy/certs/partner/client.key
    envs: [prd]
    description: "Secure partner API gRPC mTLS"
```

- [ ] **Step 3: Update the header comment to include grpc and tls**

In `egress/egress-allowlist.yaml`, update lines 6-17 to include the new fields:

```yaml
# Format:
#   - destination/destinations: hostname or IP/CIDR (can be a list for TCP/gRPC)
#     port/port_range: port number or range (start/end)
#     protocol: http | tcp | grpc
#     envs: [dev, stg, prd] - which environments this rule applies to
#     tls:                   - optional, for mTLS (tcp/grpc only)
#       cert: /path/to/client.crt
#       key: /path/to/client.key
#       ca: /path/to/ca.crt   (optional)
#     description: optional description
#
# Rules:
#   - protocol: http  → Uses HTTP_PROXY, matched by hostname
#   - protocol: tcp   → Matched by IP (hostnames resolved at deploy time)
#                       Supports port_range (start/end) and multiple destinations
#   - protocol: grpc  → Like TCP but uses HTTP/2 codec for gRPC traffic
#   - envs: List of environments where this rule applies. If omitted, applies to ALL environments.
#   - tls: mTLS config for tcp/grpc rules. Requires cert and key paths. ca is optional.
```

- [ ] **Step 4: Regenerate and verify gRPC and mTLS configs appear**

Run:
```bash
cd egress && python generate-envoy-config.py --env dev -a egress-allowlist.yaml -t envoy.yaml.j2 -o envoy-dev.yaml 2>&1
```

Expected output includes:
- `[gRPC] DEV: dagster-code-server...->X.X.X.X:4266`
- `gRPC rules: 2` (Dagster + Jaeger)
- `mTLS clusters: 0` (mTLS rules are stg/prd only)

Run for prd:
```bash
cd egress && python generate-envoy-config.py --env prd -a egress-allowlist.yaml -t envoy.yaml.j2 -o envoy-prd.yaml 2>&1
```

Expected:
- `mTLS clusters: 2`
- Output file contains `mtls_payment_gateway` and `mtls_secure_api` clusters

Verify:
```bash
grep -A5 "codec_type: HTTP2" egress/envoy-dev.yaml  # Should show gRPC filter chains
grep -A3 "transport_socket" egress/envoy-prd.yaml     # Should show mTLS transport sockets
```

- [ ] **Step 5: Regenerate all environments**

```bash
cd egress && bash generate-all-envs.sh
```

- [ ] **Step 6: Commit**

```bash
git add egress/egress-allowlist.yaml egress/envoy-dev.yaml egress/envoy-stg.yaml egress/envoy-prd.yaml
git commit -m "feat(egress): add example gRPC and mTLS rules to allowlist"
```

---

### Task 7: Update test-egress.sh with new test cases

**Files:**
- Modify: `egress/test-egress.sh`

- [ ] **Step 1: Add port_range config verification test**

In `egress/test-egress.sh`, before the final "Test Suite Complete" block (line 121), add:

```bash
# Test 8: Port range config verification
echo "─────────────────────────────────────────────"
echo "Test 8: port_range uses native filter_chain_match (no RBAC)"
echo "─────────────────────────────────────────────"
CONFIG_FILE="${CONFIG_FILE:-envoy-dev.yaml}"
if [ -f "$CONFIG_FILE" ]; then
    RBAC_COUNT=$(grep -c "envoy.filters.network.rbac" "$CONFIG_FILE" 2>/dev/null || echo "0")
    PORT_RANGE_COUNT=$(grep -c "port_range:" "$CONFIG_FILE" 2>/dev/null || echo "0")
    if [ "$RBAC_COUNT" = "0" ] && [ "$PORT_RANGE_COUNT" -gt "0" ]; then
        pass "No RBAC filters found, $PORT_RANGE_COUNT port_range entries in filter_chain_match"
    elif [ "$RBAC_COUNT" != "0" ]; then
        fail "Found $RBAC_COUNT RBAC filter(s) — should use native port_range instead"
    else
        warn "No port_range rules found in config (may be expected)"
    fi
else
    warn "Config file $CONFIG_FILE not found, skipping"
fi
echo ""

# Test 9: gRPC filter chain verification
echo "─────────────────────────────────────────────"
echo "Test 9: gRPC filter chains use HTTP/2 codec"
echo "─────────────────────────────────────────────"
if [ -f "$CONFIG_FILE" ]; then
    GRPC_COUNT=$(grep -c "codec_type: HTTP2" "$CONFIG_FILE" 2>/dev/null || echo "0")
    if [ "$GRPC_COUNT" -gt "0" ]; then
        pass "Found $GRPC_COUNT gRPC filter chain(s) with HTTP2 codec"
    else
        warn "No gRPC filter chains found in config (may be expected if no gRPC rules)"
    fi
else
    warn "Config file $CONFIG_FILE not found, skipping"
fi
echo ""

# Test 10: mTLS cluster verification
echo "─────────────────────────────────────────────"
echo "Test 10: mTLS cluster configuration"
echo "─────────────────────────────────────────────"
if [ -f "$CONFIG_FILE" ]; then
    MTLS_COUNT=$(grep -c "UpstreamTlsContext" "$CONFIG_FILE" 2>/dev/null || echo "0")
    if [ "$MTLS_COUNT" -gt "0" ]; then
        pass "Found $MTLS_COUNT mTLS cluster(s) with UpstreamTlsContext"
        grep "certificate_chain:" "$CONFIG_FILE" | sed 's/^/  /'
    else
        warn "No mTLS clusters found in config (may be expected)"
    fi
else
    warn "Config file $CONFIG_FILE not found, skipping"
fi
echo ""
```

- [ ] **Step 2: Update the cluster status test to include mTLS clusters**

Update Test 7 (line 118) to also show mTLS clusters:

Old:
```bash
curl -s "http://${PROXY_HOST}:${ADMIN_PORT}/clusters" | grep -E "^(dynamic_forward_proxy|original_dst|blackhole)" | head -10
```

New:
```bash
curl -s "http://${PROXY_HOST}:${ADMIN_PORT}/clusters" | grep -E "^(dynamic_forward_proxy|original_dst|blackhole|mtls_)" | head -15
```

- [ ] **Step 3: Commit**

```bash
git add egress/test-egress.sh
git commit -m "feat(egress): add gRPC, mTLS, and port_range verification tests"
```

---

### Task 8: Update documentation

**Files:**
- Modify: `egress/EGRESS-ALLOWLIST-GUIDE.md`

- [ ] **Step 1: Add gRPC protocol documentation**

Find the section that documents protocol types in `egress/EGRESS-ALLOWLIST-GUIDE.md`. Add a new subsection for gRPC after the TCP protocol documentation:

```markdown
### Protocol: `grpc`

gRPC rules handle HTTP/2-based gRPC traffic. They work like TCP rules (hostnames resolved to IPs at generation time) but generate HTTP/2-aware filter chains instead of raw TCP proxy.

**When to use:** For gRPC services like Dagster code servers, Jaeger collectors, or any service communicating over gRPC (HTTP/2 + protobuf).

**Fields:** Same as TCP — `destination`, `destinations`, `port`, `port_range`, `envs`, `description`. Also supports optional `tls` block for mTLS.

```yaml
# Single gRPC destination
- destination: dagster-code-server.dagster.svc.cluster.local
  port: 4266
  protocol: grpc
  envs: [dev, stg]
  description: "Dagster code server gRPC"

# Multiple gRPC destinations
- destinations:
    - grpc-server-1.internal
    - grpc-server-2.internal
  port: 50051
  protocol: grpc
  description: "gRPC backend servers"
```

**Key behavior:**
- Uses `codec_type: HTTP2` on the Envoy filter chain
- `timeout: 0s` for long-lived gRPC streams
- Intercepted by iptables (transparent proxy on :15001), not HTTP_PROXY
- Logs include `grpc_status` field
```

- [ ] **Step 2: Add mTLS documentation**

Add a new section for mTLS:

```markdown
### mTLS (Mutual TLS)

Add an optional `tls` block to any `tcp` or `grpc` rule to enable client certificate authentication:

```yaml
- destination: payment-gateway.partner.com
  port: 8443
  protocol: tcp
  tls:
    cert: /etc/envoy/certs/payment/client.crt   # required
    key: /etc/envoy/certs/payment/client.key     # required
    ca: /etc/envoy/certs/payment/ca.crt          # optional (server verification)
  description: "Payment gateway mTLS"
```

**Fields:**
| Field | Required | Description |
|-------|----------|-------------|
| `cert` | Yes | Path to client certificate file |
| `key` | Yes | Path to client private key file |
| `ca` | No | Path to CA cert for server verification |

**How it works:**
- Rules with `tls` generate a dedicated named cluster with `UpstreamTlsContext`
- The cluster uses `STRICT_DNS` type (not `ORIGINAL_DST`)
- SNI is set to the original hostname
- For `grpc` + `tls`, the cluster also enables HTTP/2 upstream protocol
- `ca` is optional — omit it to skip server certificate verification

**Not supported on:** `http` rules (HTTP uses CONNECT tunnel — TLS is end-to-end between client and server).

**Certificate management:** Certificates are expected as files on the container filesystem. Mount them via Kubernetes Secrets, CyberArk sidecar injection, or any method that produces files at the specified paths.
```

- [ ] **Step 3: Update the port_range documentation**

Find the port_range documentation section and update it to remove any mention of RBAC:

```markdown
### Port Ranges

Use `port_range` instead of `port` when a service uses a range of ports:

```yaml
- destinations:
    - redis-1.internal
    - redis-2.internal
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  description: "Redis cluster"
```

Port ranges use Envoy's native `filter_chain_match.port_range` for efficient matching.
Both `start` and `end` are inclusive.
```

- [ ] **Step 4: Commit**

```bash
git add egress/EGRESS-ALLOWLIST-GUIDE.md
git commit -m "docs(egress): add gRPC, mTLS, and updated port_range documentation"
```

---

### Task 9: End-to-end verification

**Files:** None (verification only)

- [ ] **Step 1: Regenerate all environments from clean state**

```bash
cd egress && bash generate-all-envs.sh 2>&1
```

Expected output should show:
- HTTP rules, TCP rules, gRPC rules, mTLS clusters for each env
- No errors or warnings (except DNS resolution which may fail on hostnames that don't exist locally)

- [ ] **Step 2: Verify port_range fix across all envs**

```bash
for f in egress/envoy-dev.yaml egress/envoy-stg.yaml egress/envoy-prd.yaml; do
  echo "=== $f ==="
  echo "RBAC count: $(grep -c 'envoy.filters.network.rbac' "$f" 2>/dev/null || echo 0)"
  echo "port_range count: $(grep -c 'port_range:' "$f" 2>/dev/null || echo 0)"
done
```

Expected: RBAC count = 0 for all, port_range count > 0 for all.

- [ ] **Step 3: Verify gRPC filter chains exist**

```bash
grep -c "codec_type: HTTP2" egress/envoy-dev.yaml  # Should be >= 2
grep -c "codec_type: HTTP2" egress/envoy-prd.yaml   # Should be >= 2
```

- [ ] **Step 4: Verify mTLS clusters in prd config**

```bash
grep -A20 "mtls_" egress/envoy-prd.yaml | head -40
```

Expected: shows `mtls_payment_gateway` and `mtls_secure_api` clusters with `UpstreamTlsContext`.

- [ ] **Step 5: Run the test script against the config files**

```bash
cd egress && CONFIG_FILE=envoy-dev.yaml bash test-egress.sh 2>&1 || true
```

The proxy-dependent tests (1-7) will fail without a running Envoy, but tests 8-10 (config verification) should pass.

- [ ] **Step 6: Final commit if any generated files changed**

```bash
cd egress && git status
# If envoy-*.yaml files changed:
git add egress/envoy-dev.yaml egress/envoy-stg.yaml egress/envoy-prd.yaml
git commit -m "chore(egress): regenerate all env configs with gRPC, mTLS, and port_range fixes"
```
