# Egress Allowlist Configuration Guide

This guide explains how to configure egress rules in `egress-allowlist.yaml` for controlling outbound traffic through the Envoy proxy.

---

## Table of Contents

- [Overview](#overview)
- [File Structure](#file-structure)
- [Rule Fields Reference](#rule-fields-reference)
- [Protocol Types](#protocol-types)
  - [HTTP Rules](#http-rules)
  - [TCP Rules](#tcp-rules)
- [Environment Filtering](#environment-filtering)
- [Port Configuration](#port-configuration)
- [Destination Configuration](#destination-configuration)
- [Complete Examples](#complete-examples)
- [Generating Envoy Config](#generating-envoy-config)
- [CI/CD Integration](#cicd-integration)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)

---

## Overview

The `egress-allowlist.yaml` file defines which external destinations your applications can connect to. It supports:

- **HTTP/HTTPS traffic**: Matched by hostname (via `:authority` header)
- **TCP traffic**: Matched by IP address (hostnames resolved at generation time)
- **Environment-specific rules**: Apply rules to specific environments (dev/stg/prd)
- **Port ranges**: Allow connections to a range of ports
- **Multiple destinations**: Group related endpoints in a single rule

---

## File Structure

```yaml
egress:
  # HTTP/HTTPS rules
  - destination: api.example.com
    port: 443
    protocol: http
    envs: [dev, stg, prd]
    description: "Example API"

  # TCP rules
  - destination: 10.0.0.1
    port: 5432
    protocol: tcp
    envs: [prd]
    description: "Database server"
```

---

## Rule Fields Reference

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `protocol` | Yes | `http` or `tcp` | Traffic protocol type |
| `destination` | Yes* | string | Single hostname, IP, or CIDR |
| `destinations` | Yes* | list | Multiple hostnames/IPs/CIDRs (TCP only) |
| `domains` | Yes* | list | Multiple hostnames (HTTP only) |
| `port` | Yes** | integer | Single port number |
| `port_range` | Yes** | object | Port range with `start` and `end` |
| `envs` | No | list | Environments: `[dev, stg, prd]`. If omitted, applies to ALL |
| `description` | No | string | Human-readable description |

\* One of `destination`, `destinations`, or `domains` is required  
\** One of `port` or `port_range` is required

---

## Protocol Types

### HTTP Rules

HTTP rules match traffic by hostname using the HTTP `:authority` header. Use for:
- REST APIs
- Web services
- Any HTTP/HTTPS endpoint

#### Single Hostname

```yaml
- destination: api.github.com
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "GitHub API"
```

#### Multiple Hostnames

Use `domains` when you need to allow multiple related endpoints:

```yaml
- domains:
    - api.github.com
    - raw.githubusercontent.com
    - gist.githubusercontent.com
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "GitHub APIs"
```

#### Wildcard Domains

Use wildcards to match all subdomains (use with caution):

```yaml
# Single wildcard
- destination: "*.github.com"
  port: 443
  protocol: http
  envs: [dev]
  description: "All GitHub subdomains - DEV only"

# Multiple wildcards
- domains:
    - "*.api.internal"
    - "*.services.internal"
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "Internal service subdomains"
```

> ⚠️ **Warning**: Wildcards are powerful but can be overly permissive. Prefer explicit hostnames in production.

---

### TCP Rules

TCP rules match traffic by IP address. Hostnames are resolved to IPs at config generation time.

#### Single Hostname (Resolved to IP)

```yaml
- destination: postgres.db.internal
  port: 5432
  protocol: tcp
  envs: [prd]
  description: "PostgreSQL database"
```

#### Single IP Address

```yaml
- destination: 172.16.10.5
  port: 5432
  protocol: tcp
  envs: [prd]
  description: "PostgreSQL database"
```

#### CIDR Range

Allow entire subnets:

```yaml
- destination: 10.20.30.0/24
  port: 9092
  protocol: tcp
  envs: [prd]
  description: "Kafka brokers subnet"
```

#### Multiple Destinations

Mix hostnames, IPs, and CIDRs in a single rule:

```yaml
- destinations:
    - redis-prd-1.internal     # Hostname (resolved to IP)
    - redis-prd-2.internal     # Hostname (resolved to IP)
    - 10.50.250.25             # Direct IP
    - 10.50.250.26             # Direct IP
    - 10.50.0.0/16             # CIDR range
  port: 6379
  protocol: tcp
  envs: [prd]
  description: "Redis cluster"
```

---

## Environment Filtering

The `envs` field controls which environments a rule applies to.

### Specific Environments

```yaml
# DEV only
- destination: debug-api.internal
  port: 8080
  protocol: http
  envs: [dev]
  description: "Debug API - DEV only"

# STG and PRD
- destination: partner-api.external.com
  port: 443
  protocol: http
  envs: [stg, prd]
  description: "Partner API"
```

### All Environments

Omit `envs` to apply to all environments, or explicitly list all:

```yaml
# Option 1: Omit envs (applies to ALL)
- destination: logging.internal
  port: 443
  protocol: http
  description: "Logging service - all environments"

# Option 2: Explicit list
- destination: logging.internal
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "Logging service - all environments"
```

### Environment-Specific Endpoints

Common pattern for different endpoints per environment:

```yaml
# DEV database
- destination: postgres-dev.db.internal
  port: 5432
  protocol: tcp
  envs: [dev]
  description: "PostgreSQL - DEV"

# STG database
- destination: postgres-stg.db.internal
  port: 5432
  protocol: tcp
  envs: [stg]
  description: "PostgreSQL - STG"

# PRD database (HA cluster)
- destinations:
    - postgres-prd-primary.db.internal
    - postgres-prd-replica.db.internal
  port: 5432
  protocol: tcp
  envs: [prd]
  description: "PostgreSQL - PRD (HA)"
```

---

## Port Configuration

### Single Port

```yaml
- destination: api.example.com
  port: 443
  protocol: http
```

### Port Range

Use for services that use dynamic port allocation (e.g., Redis Cluster, Kafka):

```yaml
- destination: redis-cluster.internal
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  envs: [prd]
  description: "Redis cluster ports"
```

---

## Destination Configuration

### Quick Reference

| Scenario | HTTP | TCP |
|----------|------|-----|
| Single hostname | `destination: api.com` | `destination: db.internal` |
| Single IP | N/A (use hostname) | `destination: 10.0.0.1` |
| Single CIDR | N/A | `destination: 10.0.0.0/24` |
| Single wildcard | `destination: "*.api.com"` | N/A |
| Multiple hostnames | `domains: [a.com, b.com]` | `destinations: [a.internal, b.internal]` |
| Multiple IPs | N/A | `destinations: [10.0.0.1, 10.0.0.2]` |
| Mixed | `domains: [a.com, "*.b.com"]` | `destinations: [host.internal, 10.0.0.1, 10.0.0.0/24]` |

### Singular vs Plural

**Rule of thumb:**
- **Singular** (`destination`) = one thing
- **Plural** (`domains` / `destinations`) = multiple things

```yaml
# Singular - one destination
- destination: api.github.com
  port: 443
  protocol: http

# Plural - multiple destinations
- domains:
    - api.github.com
    - raw.githubusercontent.com
  port: 443
  protocol: http
```

---

## Complete Examples

### Example 1: Web API Access

```yaml
# External APIs - all environments
- domains:
    - api.stripe.com
    - api.sendgrid.com
    - api.twilio.com
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "Third-party SaaS APIs"
```

### Example 2: Database Access by Environment

```yaml
# DEV - direct access
- destination: postgres-dev.db.internal
  port: 5432
  protocol: tcp
  envs: [dev]
  description: "PostgreSQL - DEV"

# STG - single instance
- destination: 172.16.10.5
  port: 5432
  protocol: tcp
  envs: [stg]
  description: "PostgreSQL - STG"

# PRD - HA cluster with failover
- destinations:
    - postgres-prd-primary.internal
    - postgres-prd-standby-1.internal
    - postgres-prd-standby-2.internal
  port: 5432
  protocol: tcp
  envs: [prd]
  description: "PostgreSQL - PRD (HA cluster)"
```

### Example 3: Redis Cluster with Port Range

```yaml
- destinations:
    - redis-node-1.internal
    - redis-node-2.internal
    - redis-node-3.internal
    - 10.50.100.10
    - 10.50.100.11
    - 10.50.100.12
  port_range:
    start: 6379
    end: 6381
  protocol: tcp
  envs: [prd]
  description: "Redis cluster - multiple ports per node"
```

### Example 4: Monitoring Stack

```yaml
# Prometheus - TCP for metrics scraping
- destination: prometheus.monitoring.internal
  port: 9090
  protocol: tcp
  envs: [dev, stg, prd]
  description: "Prometheus metrics"

# Grafana - HTTP for dashboard access
- destination: grafana.monitoring.internal
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "Grafana dashboards"

# Jaeger - TCP for trace collection
- destination: jaeger-collector.tracing.internal
  port: 14250
  protocol: tcp
  envs: [dev, stg, prd]
  description: "Jaeger collector (gRPC)"
```

### Example 5: Kafka with Subnet Access

```yaml
# DEV - single broker
- destination: kafka-dev.internal
  port: 9092
  protocol: tcp
  envs: [dev]
  description: "Kafka - DEV"

# PRD - entire broker subnet
- destination: 10.20.30.0/24
  port: 9092
  protocol: tcp
  envs: [prd]
  description: "Kafka brokers subnet - PRD"

# PRD - specific brokers (alternative approach)
- destinations:
    - kafka-prd-1.internal
    - kafka-prd-2.internal
    - kafka-prd-3.internal
  port: 9092
  protocol: tcp
  envs: [prd]
  description: "Kafka brokers - PRD"
```

---

## Generating Envoy Config

### Basic Usage

```bash
# Generate for specific environment
python generate-envoy-config.py --env dev -o envoy-dev.yaml
python generate-envoy-config.py --env stg -o envoy-stg.yaml
python generate-envoy-config.py --env prd -o envoy-prd.yaml

# Generate with validation (requires envoy binary)
python generate-envoy-config.py --env prd -o envoy-prd.yaml --validate
```

### Custom Paths

```bash
python generate-envoy-config.py \
  --env prd \
  --allowlist /path/to/egress-allowlist.yaml \
  --template /path/to/envoy.yaml.j2 \
  --output /path/to/envoy-prd.yaml
```

### What Happens During Generation

1. **HTTP rules**: Hostnames are used directly (matched by `:authority` header)
2. **TCP rules with hostnames**: DNS resolution converts hostnames to IPs
3. **TCP rules with IP/CIDR**: Used directly without modification
4. **Environment filtering**: Only rules matching `--env` are included

---

## CI/CD Integration

### Jenkins Pipeline Example

```groovy
pipeline {
    agent any
    
    parameters {
        choice(name: 'ENV', choices: ['dev', 'stg', 'prd'], description: 'Target environment')
    }
    
    stages {
        stage('Generate Envoy Config') {
            steps {
                sh '''
                    pip install pyyaml jinja2
                    python generate-envoy-config.py --env ${ENV} -o envoy.yaml
                '''
            }
        }
        
        stage('Validate Config') {
            steps {
                // Use Docker to validate (Jenkins doesn't need Envoy installed)
                sh '''
                    docker run --rm -v $(pwd):/config envoyproxy/envoy:v1.28-latest \
                        --mode validate -c /config/envoy.yaml
                '''
            }
        }
        
        stage('Deploy ConfigMap') {
            steps {
                sh '''
                    kubectl create configmap envoy-config \
                        --from-file=envoy.yaml \
                        --namespace=${ENV} \
                        --dry-run=client -o yaml | kubectl apply -f -
                    
                    kubectl rollout restart deployment/envoy -n ${ENV}
                '''
            }
        }
    }
}
```

### Docker Multi-Stage Build

```dockerfile
# Stage 1: Generate config
FROM python:3.11-slim AS generator
WORKDIR /build
COPY egress-allowlist.yaml envoy.yaml.j2 generate-envoy-config.py ./
RUN pip install pyyaml jinja2 && \
    python generate-envoy-config.py --env ${ENV:-prd} -o envoy.yaml

# Stage 2: Validate
FROM envoyproxy/envoy:v1.28-latest AS validator
COPY --from=generator /build/envoy.yaml /etc/envoy/envoy.yaml
RUN envoy --mode validate -c /etc/envoy/envoy.yaml

# Stage 3: Final image
FROM envoyproxy/envoy:v1.28-latest
COPY --from=generator /build/envoy.yaml /etc/envoy/envoy.yaml
```

---

## Best Practices

### 1. Use Descriptive Descriptions

```yaml
# Bad
- destination: 10.20.30.5
  port: 5432
  protocol: tcp
  description: "DB"

# Good
- destination: 10.20.30.5
  port: 5432
  protocol: tcp
  envs: [prd]
  description: "PostgreSQL primary - PRD (us-east-1a)"
```

### 2. Group Related Rules

```yaml
# ─────────────────────────────────────────────────────────────────────────
# External SaaS APIs
# ─────────────────────────────────────────────────────────────────────────

- domains:
    - api.stripe.com
    - api.sendgrid.com
  port: 443
  protocol: http
  description: "Payment and email services"

# ─────────────────────────────────────────────────────────────────────────
# Internal Databases
# ─────────────────────────────────────────────────────────────────────────

- destination: postgres.db.internal
  port: 5432
  protocol: tcp
  description: "Primary database"
```

### 3. Be Specific in Production

```yaml
# DEV - more permissive (OK)
- destination: "*.internal.dev"
  port: 443
  protocol: http
  envs: [dev]

# PRD - explicit list (preferred)
- domains:
    - api.internal.prd
    - auth.internal.prd
    - data.internal.prd
  port: 443
  protocol: http
  envs: [prd]
```

### 4. Use CIDR Sparingly

```yaml
# Avoid: Too broad
- destination: 10.0.0.0/8
  port: 5432
  protocol: tcp

# Better: Specific subnet
- destination: 10.20.30.0/24
  port: 5432
  protocol: tcp
  description: "Database subnet only"

# Best: Explicit IPs when possible
- destinations:
    - 10.20.30.5
    - 10.20.30.6
  port: 5432
  protocol: tcp
  description: "Primary and standby databases"
```

### 5. Document Environment Differences

```yaml
# Explain why rules differ per environment
- destination: postgres-dev.db.internal
  port: 5432
  protocol: tcp
  envs: [dev]
  description: "DEV database - single instance, no SSL"

- destinations:
    - postgres-prd-primary.internal
    - postgres-prd-standby.internal
  port: 5432
  protocol: tcp
  envs: [prd]
  description: "PRD database - HA cluster with automatic failover"
```

---

## Troubleshooting

### DNS Resolution Failures

If TCP hostnames can't be resolved during generation:

```
ERROR: Cannot resolve redis-prd.internal for prd, skipping
```

**Solutions:**
1. Ensure DNS is accessible from the generation environment
2. Use IP addresses instead of hostnames for TCP rules
3. Run generation in an environment with access to internal DNS

### Config Validation Errors

```
Config validation failed: filter chain match has duplicate entries
```

**Common causes:**
- Duplicate rules for the same destination/port
- Overlapping CIDR ranges
- Same hostname listed in multiple rules

### Rules Not Applied

If traffic is blocked despite being in the allowlist:

1. **Check environment**: Verify `--env` matches your deployment
2. **Check protocol**: HTTP vs TCP matters
3. **Check port**: Ensure exact match or within `port_range`
4. **Check DNS**: For TCP, hostnames must resolve at generation time

---

## File Locations

```
egress/
├── egress-allowlist.yaml          # Source: Edit this file
├── envoy.yaml.j2                  # Template: Jinja2 template
├── generate-envoy-config.py       # Generator: Python script
├── EGRESS-ALLOWLIST-GUIDE.md      # This documentation
└── generated/                     # Output: Generated configs (optional)
    ├── envoy-dev.yaml
    ├── envoy-stg.yaml
    └── envoy-prd.yaml
```

---

## Quick Reference Card

```yaml
# HTTP - single hostname
- destination: api.example.com
  port: 443
  protocol: http

# HTTP - multiple hostnames
- domains: [api.example.com, www.example.com]
  port: 443
  protocol: http

# HTTP - wildcard
- destination: "*.example.com"
  port: 443
  protocol: http

# TCP - single hostname (resolved to IP)
- destination: db.internal
  port: 5432
  protocol: tcp

# TCP - single IP
- destination: 10.0.0.1
  port: 5432
  protocol: tcp

# TCP - CIDR range
- destination: 10.0.0.0/24
  port: 5432
  protocol: tcp

# TCP - multiple destinations
- destinations: [db-1.internal, db-2.internal, 10.0.0.3]
  port: 5432
  protocol: tcp

# Port range
- destination: redis.internal
  port_range: {start: 6379, end: 6381}
  protocol: tcp

# Environment-specific
- destination: api.example.com
  port: 443
  protocol: http
  envs: [prd]

# All environments (omit envs or list all)
- destination: logging.internal
  port: 443
  protocol: http
  envs: [dev, stg, prd]
```
