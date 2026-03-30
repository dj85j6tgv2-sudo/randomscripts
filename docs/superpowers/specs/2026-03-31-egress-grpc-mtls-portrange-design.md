# Egress Control Plane: gRPC, mTLS, and port_range Design

**Date:** 2026-03-31
**Status:** Approved

## Overview

Three enhancements to the Envoy egress control plane:

1. **gRPC support** — new `protocol: grpc` rule type for HTTP/2-based gRPC traffic
2. **mTLS support** — optional `tls` block on tcp/grpc rules for client certificate authentication
3. **port_range fix** — replace RBAC workaround with native `filter_chain_match.port_range`

## Problem Context

- **gRPC:** Dagster cluster uses gRPC for scheduler-to-code-server communication. Scheduled runs fail because the egress proxy doesn't handle HTTP/2. Manual runs from the UI work because they use HTTP REST or bypass the proxy. Currently only `http` and `tcp` protocols are supported.
- **mTLS:** Some upstream endpoints require mutual TLS with client certificates. The egress proxy has no TLS origination — it only does passthrough. Certs are injected by CyberArk into the container filesystem.
- **port_range:** The RBAC-based workaround for port ranges is unnecessary. Envoy's `filter_chain_match` natively supports `port_range: {start, end}`. The current approach adds complexity and the dev config had a bug using the wrong field name (`destination_port_range` instead of `port_range`).

## Allowlist Schema Changes

### New protocol: `grpc`

Supports the same fields as `tcp`: `destination`, `destinations`, `port`, `port_range`, `envs`, `description`. Hostnames resolved to IPs at generation time.

```yaml
- protocol: grpc
  destination: dagster-code-server.dagster.svc.cluster.local
  port: 4266
  envs: [dev, stg]
  description: "Dagster code server gRPC"

- protocol: grpc
  destinations:
    - dagster-code-server-1.dagster.svc.cluster.local
    - dagster-code-server-2.dagster.svc.cluster.local
  port: 4266
  description: "Dagster code servers gRPC"
```

### New optional `tls` block

Available on `tcp` and `grpc` rules. Fields:

| Field | Required | Description |
|-------|----------|-------------|
| `cert` | Yes (when `tls` present) | Path to client certificate file |
| `key` | Yes (when `tls` present) | Path to client private key file |
| `ca` | No | Path to CA cert for server verification |

```yaml
- protocol: tcp
  destination: payment-gateway.partner.com
  port: 8443
  tls:
    cert: /etc/envoy/certs/payment/client.crt
    key: /etc/envoy/certs/payment/client.key
    ca: /etc/envoy/certs/payment/ca.crt
  description: "Payment gateway mTLS"

- protocol: grpc
  destination: secure-grpc.internal
  port: 50051
  tls:
    cert: /etc/envoy/certs/grpc/client.crt
    key: /etc/envoy/certs/grpc/client.key
  description: "Secure gRPC with client cert only"
```

### port_range — no schema change

The YAML schema is already correct. The fix is in template generation only.

## Envoy Config Generation

### gRPC Filter Chains

gRPC rules generate filter chains on the **TCP listener (:15001)**, not the HTTP listener. The difference from TCP: uses `http_connection_manager` with `codec_type: HTTP2` instead of raw `tcp_proxy`.

```yaml
- name: "allow_dagster_code_server_4266"
  filter_chain_match:
    destination_port: 4266
    prefix_ranges:
      - address_prefix: "10.50.100.15"
        prefix_len: 32
  filters:
    - name: envoy.filters.network.http_connection_manager
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
        codec_type: HTTP2
        stat_prefix: "grpc_dagster_code_server_4266"
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
                  rule: "{{ rule.description }}"
                  destination: "%UPSTREAM_HOST%"
                  grpc_status: "%GRPC_STATUS%"
                  duration_ms: "%DURATION%"
                  bytes_rx: "%BYTES_RECEIVED%"
                  bytes_tx: "%BYTES_SENT%"
        route_config:
          name: "grpc_dagster_code_server_4266"
          virtual_hosts:
            - name: "grpc_dagster_code_server_4266"
              domains: ["*"]
              routes:
                - match:
                    prefix: "/"
                  route:
                    cluster: original_dst
                    timeout: 0s
        http_filters:
          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
        http2_protocol_options:
          max_concurrent_streams: 100
```

Key decisions:
- Lives on TCP listener (intercepted by iptables, not HTTP_PROXY)
- `codec_type: HTTP2` forces H2 on the downstream connection
- Routes to `original_dst` cluster for transparent passthrough
- `timeout: 0s` supports long-lived gRPC streams (important for Dagster)
- No gRPC-web or transcoder filters needed for native gRPC

### mTLS Clusters

Rules with a `tls` block generate a **dedicated named cluster** with `UpstreamTlsContext`:

```yaml
- name: "mtls_payment_gateway_8443"
  type: STRICT_DNS
  lb_policy: ROUND_ROBIN
  connect_timeout: 10s
  load_assignment:
    cluster_name: "mtls_payment_gateway_8443"
    endpoints:
      - lb_endpoints:
          - endpoint:
              address:
                socket_address:
                  address: payment-gateway.partner.com
                  port_value: 8443
  transport_socket:
    name: envoy.transport_sockets.tls
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
      sni: payment-gateway.partner.com
      common_tls_context:
        tls_certificates:
          - certificate_chain:
              filename: /etc/envoy/certs/payment/client.crt
            private_key:
              filename: /etc/envoy/certs/payment/client.key
        validation_context:
          trusted_ca:
            filename: /etc/envoy/certs/payment/ca.crt
```

- `validation_context` only included when `ca` is provided
- `sni` set to the original destination hostname
- For `grpc` + `tls`, the cluster also includes `http2_protocol_options`:

```yaml
- name: "mtls_secure_grpc_50051"
  type: STRICT_DNS
  lb_policy: ROUND_ROBIN
  connect_timeout: 10s
  typed_extension_protocol_options:
    envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
      "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
      explicit_http_config:
        http2_protocol_options: {}
  load_assignment:
    cluster_name: "mtls_secure_grpc_50051"
    endpoints:
      - lb_endpoints:
          - endpoint:
              address:
                socket_address:
                  address: secure-grpc.internal
                  port_value: 50051
  transport_socket:
    name: envoy.transport_sockets.tls
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
      sni: secure-grpc.internal
      common_tls_context:
        tls_certificates:
          - certificate_chain:
              filename: /etc/envoy/certs/grpc/client.crt
            private_key:
              filename: /etc/envoy/certs/grpc/client.key
```

The filter chain for mTLS rules references the named cluster instead of `original_dst`:

```yaml
filters:
  - name: envoy.filters.network.tcp_proxy
    typed_config:
      stat_prefix: "tcp_payment_gateway_8443"
      cluster: "mtls_payment_gateway_8443"
```

### port_range Fix

Replace RBAC with native `filter_chain_match.port_range`:

```yaml
# Before (RBAC workaround — REMOVE)
- name: "allow_redis_30000_30999"
  filter_chain_match:
    prefix_ranges: [...]
  filters:
    - name: envoy.filters.network.rbac
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.filters.network.rbac.v3.RBAC
        stat_prefix: "rbac_redis_30000_30999"
        rules:
          action: ALLOW
          policies:
            allow_port_range:
              permissions:
                - destination_port_range:
                    start: 30000
                    end: 31000
    - name: envoy.filters.network.tcp_proxy
      typed_config:
        cluster: original_dst

# After (native port_range)
- name: "allow_redis_30000_30999"
  filter_chain_match:
    port_range:
      start: 30000
      end: 30999
    prefix_ranges: [...]
  filters:
    - name: envoy.filters.network.tcp_proxy
      typed_config:
        cluster: original_dst
```

No exclusive-end adjustment needed — `port_range` in `filter_chain_match` uses inclusive end (unlike RBAC's `destination_port_range` which uses exclusive end).

## Python Generator Changes

### `process_tcp_rules()` / `process_grpc_rules()`

- Accept `protocol: grpc` rules alongside `tcp`
- Set `rule.is_grpc = True` flag for template branching
- Same hostname resolution, same IP/CIDR parsing

### mTLS Processing

- When `tls` block present on a rule:
  - Validate `cert` and `key` are provided
  - `ca` is optional
  - Add rule to `mtls_clusters` list passed to template context
  - Set `rule.cluster_name = sanitize_name(f"mtls_{first_dest}_{port}")`
  - Store original hostname (before DNS resolution) for `sni` and `STRICT_DNS` address

### port_range

- Remove RBAC-related logic from template rendering
- Pass `port_range` dict through to template as-is
- Template emits `port_range: {start, end}` in `filter_chain_match`

### Validation

- `protocol` must be one of: `http`, `tcp`, `grpc`
- `tls` block: `cert` and `key` required; `ca` optional
- `tls` block: not allowed on `http` rules (HTTP uses CONNECT tunnel — TLS is end-to-end)
- `port_range`: `start` <= `end`

## Logging

gRPC filter chains add `grpc_status` to the JSON log format:

```json
{
  "timestamp": "...",
  "listener": "grpc",
  "environment": "dev",
  "decision": "ALLOWED",
  "rule": "Dagster code server gRPC",
  "destination": "%UPSTREAM_HOST%",
  "grpc_status": "%GRPC_STATUS%",
  "duration_ms": "%DURATION%",
  "bytes_rx": "%BYTES_RECEIVED%",
  "bytes_tx": "%BYTES_SENT%"
}
```

TCP and mTLS rules use existing log format with `listener: "transparent_tcp"`.

## Testing

Update `test-egress.sh`:

- **gRPC**: Use `grpcurl` against allowed gRPC destination, verify success. Verify non-allowlisted gRPC port is denied.
- **mTLS**: Use `curl --cert --key --cacert` against mTLS endpoint through proxy. Verify connection succeeds. Verify it fails without certs.
- **port_range**: Regenerate configs, verify RBAC block is gone, verify `filter_chain_match.port_range` is present. Test connectivity to a port within range succeeds, port outside range is denied.

## Deployment

- **Kubernetes:** CyberArk-injected cert files must be accessible to the Envoy sidecar container via shared volume mount (e.g., `emptyDir` or CyberArk sidecar writes to a shared path).
- **Config generation workflow:** No change — edit allowlist, run generator, deploy ConfigMap.
- **iptables init container:** No change.

## What Does NOT Change

- HTTP listener (:15000) — untouched
- `original_dst` cluster — still used for non-mTLS tcp/grpc rules
- `dynamic_forward_proxy` cluster — still used for HTTP rules
- `blackhole` cluster — still used for deny
- Environment filtering (`envs`) — works the same for all protocol types
- Docker Compose local testing setup — still works
