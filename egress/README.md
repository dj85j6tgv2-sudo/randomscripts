# Envoy Egress Control Plane

A simple, deny-by-default egress control solution using Envoy proxy with environment-based configuration (DEV/STG/PRD).

## Overview

This solution provides:
- **Deny by default**: All outbound traffic is blocked unless explicitly allowed
- **Environment filtering**: Separate configs for DEV, STG, and PRD from single source
- **Simple allowlist**: Users declare destinations in a simple YAML format
- **HTTP multi-domain**: Support for multiple domains and wildcards per rule
- **TCP port ranges**: Support for port ranges and multiple IPs per rule
- **Automatic DNS resolution**: Hostnames are resolved to IPs at deploy time for TCP rules
- **Comprehensive logging**: JSON logs with ALLOWED/DENIED decisions and environment tags

## Quick Start

```bash
# Generate config for specific environment
python generate-envoy-config.py --env dev
python generate-envoy-config.py --env stg
python generate-envoy-config.py --env prd

# Or generate all at once
./generate-all-envs.sh

# Check IPs behind a hostname
python resolve-hostnames.py kafka.internal.corp

# Check all hostnames in allowlist
python resolve-hostnames.py --file egress-allowlist.yaml
```

## File Structure

### Core Files (Edit These)
- **`egress-allowlist.yaml`** - Source of truth for egress rules
  - Single file with environment tags: `envs: [dev, stg, prd]`
  - Supports HTTP (single/multiple domains, wildcards)
  - Supports TCP (single/multiple destinations, port ranges)

### Scripts
- **`generate-envoy-config.py`** - Generate environment-specific Envoy configs
- **`generate-all-envs.sh`** - Batch generate all environments
- **`resolve-hostnames.py`** - DNS resolution utility for load balancers

### Templates & Examples
- **`envoy.yaml.j2`** - Jinja2 template for Envoy configuration
- **`envoy.yaml`** - Working example (generated output)
- **`COMPLETE-EXAMPLE.yaml`** - Comprehensive example showing all features

### Deployment
- **`deployment.yaml`** - Kubernetes deployment manifest
- **`docker-compose.yaml`** - Docker Compose for local testing
- **`test-egress.sh`** - Testing script
- **`requirements.txt`** - Python dependencies

### Documentation
- **`README.md`** (this file) - Main overview
- **`QUICKSTART.txt`** - Quick reference card
- **`README-ENV.md`** - Environment-based configuration guide (comprehensive)
- **`HTTP-DOMAINS-EXAMPLES.md`** - HTTP multi-domain & wildcard examples
- **`PORT-RANGE-GUIDE.md`** - TCP port ranges & multiple IPs guide
- **`EXAMPLE-COMPARISON.md`** - Side-by-side environment comparison

## Features

### HTTP/HTTPS Rules

#### Single Domain
```yaml
- destination: api.github.com
  port: 443
  protocol: http
  envs: [dev, stg, prd]
```

#### Multiple Domains
```yaml
- domains:
    - api.github.com
    - api-v2.github.com
    - raw.githubusercontent.com
  port: 443
  protocol: http
  envs: [dev, stg, prd]
```

#### Wildcard Domains
```yaml
- domains:
    - "*.monitoring.internal"
  port: 443
  protocol: http
  envs: [dev, stg, prd]
```

### TCP Rules

#### Single Port
```yaml
- destination: postgres.db.internal
  port: 5432
  protocol: tcp
  envs: [prd]
```

#### Port Range with Multiple Destinations
```yaml
- destinations:
    - redis-master.internal
    - redis-replica.internal
    - 10.50.100.25
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  envs: [prd]
```

### Environment Filtering

Rules can target specific environments:
```yaml
envs: [dev]           # DEV only
envs: [stg]           # STG only
envs: [prd]           # PRD only
envs: [dev, stg]      # DEV and STG
envs: [dev, stg, prd] # All environments
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Pod                                                                      │
│  ┌─────────────────┐          ┌──────────────────────────────────────┐  │
│  │   Application   │          │  Envoy Sidecar                       │  │
│  │                 │          │                                      │  │
│  │ HTTP_PROXY ─────┼──────────┼──► :15000 (HTTP Proxy)               │  │
│  │                 │          │      • Matches by hostname           │  │
│  │                 │          │      • HTTP + HTTPS CONNECT          │  │
│  │                 │ iptables │                                      │  │
│  │ Direct TCP ─────┼──────────┼──► :15001 (Transparent Proxy)        │  │
│  │                 │ redirect │      • Matches by IP + port          │  │
│  │                 │          │      • TCP passthrough               │  │
│  └─────────────────┘          └──────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Files

| File | Description |
|------|-------------|
| `egress-allowlist.yaml` | User-facing allowlist configuration |
| `envoy.yaml.j2` | Jinja2 template for Envoy config |
| `generate-envoy-config.py` | Python script to generate Envoy config |
| `envoy.yaml` | Generated Envoy configuration |
| `docker-compose.yaml` | Docker Compose for local testing |
| `kubernetes/deployment.yaml` | Kubernetes deployment manifests |
| `test-egress.sh` | Test script for validation |

## Quick Start

### 1. Install dependencies

```bash
pip install pyyaml jinja2
```

### 2. Edit the allowlist

```yaml
# egress-allowlist.yaml
egress:
  # HTTP/HTTPS - use hostname
  - destination: api.github.com
    port: 443
    protocol: http

  # TCP - use hostname (resolved to IP) or IP/CIDR
  - destination: internal-redis.net.intra
    port: 30672
    protocol: tcp

  - destination: 10.20.30.0/24
    port: 9092
    protocol: tcp
```

### 3. Generate Envoy config

```bash
python generate-envoy-config.py \
  --allowlist egress-allowlist.yaml \
  --template envoy.yaml.j2 \
  --output envoy.yaml
```

### 4. Test locally with Docker

```bash
# Start Envoy
docker-compose up -d

# Test allowed destination
curl -x http://localhost:15000 https://api.github.com/zen
# → Returns GitHub zen message

# Test denied destination
curl -x http://localhost:15000 https://google.com
# → Returns {"error":"egress_denied","listener":"http_proxy"}

# Run test suite
./test-egress.sh
```

### 5. Deploy to Kubernetes

```bash
kubectl apply -f kubernetes/deployment.yaml
```

## Allowlist Format

```yaml
egress:
  - destination: <hostname | IP | CIDR>
    port: <port number>
    protocol: http | tcp
    description: <optional description>
```

### Rules

| Protocol | Destination Type | How it works |
|----------|------------------|--------------|
| `http` | hostname | Matched by `:authority` header via HTTP proxy |
| `tcp` | hostname | DNS resolved at deploy time, matched by IP |
| `tcp` | IP/CIDR | Matched directly by destination IP |

### Examples

```yaml
egress:
  # HTTPS API
  - destination: api.github.com
    port: 443
    protocol: http

  # Internal HTTP service
  - destination: internal-api.corp.local
    port: 80
    protocol: http

  # Redis (hostname - resolved to IP)
  - destination: redis.internal.svc
    port: 6379
    protocol: tcp

  # Kafka cluster (CIDR range)
  - destination: 10.20.30.0/24
    port: 9092
    protocol: tcp

  # PostgreSQL (single IP)
  - destination: 172.16.0.5
    port: 5432
    protocol: tcp
```

## Logging

All traffic is logged to stdout in JSON format.

### Allowed HTTP request

```json
{
  "timestamp": "2025-01-20T10:00:00.123Z",
  "listener": "http_proxy",
  "method": "CONNECT",
  "destination": "api.github.com:443",
  "response_code": 200,
  "bytes_rx": 1234,
  "bytes_tx": 5678,
  "duration_ms": 150
}
```

### Denied HTTP request

```json
{
  "timestamp": "2025-01-20T10:00:01.456Z",
  "listener": "http_proxy",
  "method": "CONNECT",
  "destination": "google.com:443",
  "response_code": 403,
  "bytes_rx": 0,
  "bytes_tx": 52
}
```

### Allowed TCP connection

```json
{
  "timestamp": "2025-01-20T10:00:02.789Z",
  "listener": "transparent_tcp",
  "decision": "ALLOWED",
  "rule": "internal-redis.net.intra:30672",
  "destination": "10.50.100.25:30672",
  "bytes_rx": 100,
  "bytes_tx": 200
}
```

### Denied TCP connection

```json
{
  "timestamp": "2025-01-20T10:00:03.012Z",
  "listener": "transparent_tcp",
  "decision": "DENIED",
  "destination": "8.8.8.8:53"
}
```

## CI/CD Integration

### GitLab CI

```yaml
generate-envoy-config:
  stage: build
  image: python:3.11-slim
  script:
    - pip install pyyaml jinja2
    - python generate-envoy-config.py \
        --allowlist egress-allowlist.yaml \
        --template envoy.yaml.j2 \
        --output envoy.yaml
    - kubectl create configmap envoy-egress-config \
        --from-file=envoy.yaml \
        --dry-run=client -o yaml | kubectl apply -f -
  only:
    changes:
      - egress-allowlist.yaml
```

### GitHub Actions

```yaml
name: Deploy Egress Config

on:
  push:
    paths:
      - 'egress-allowlist.yaml'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install pyyaml jinja2
      
      - name: Generate Envoy config
        run: |
          python generate-envoy-config.py \
            --allowlist egress-allowlist.yaml \
            --template envoy.yaml.j2 \
            --output envoy.yaml
      
      - name: Deploy to Kubernetes
        run: |
          kubectl create configmap envoy-egress-config \
            --from-file=envoy.yaml \
            --dry-run=client -o yaml | kubectl apply -f -
```

## Troubleshooting

### Check Envoy is running

```bash
curl http://localhost:9901/ready
```

### View Envoy stats

```bash
curl http://localhost:9901/stats | grep egress
```

### View cluster status

```bash
curl http://localhost:9901/clusters
```

### View config dump

```bash
curl http://localhost:9901/config_dump | jq .
```

### Common issues

1. **DNS resolution fails**: Ensure the hostname is resolvable from where you run the generator script
2. **403 on allowed destination**: Check the domain spelling in the allowlist
3. **Connection timeout**: For TCP, ensure the IP/CIDR is correct and iptables is configured

## Security Considerations

- The allowlist should be stored in Git and require code review for changes
- DNS resolution happens at deploy time - if IPs change, redeploy is needed
- HTTP proxy requires applications to set `HTTP_PROXY`/`HTTPS_PROXY`
- Transparent proxy requires iptables setup (init container in Kubernetes)
