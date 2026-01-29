# HTTP Domains Examples

This document shows how to use multiple domains and wildcards in HTTP/HTTPS egress rules.

## Single Domain (Original Format)

```yaml
- destination: api.github.com
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "GitHub API"
```

**Generated Envoy config:**
```yaml
- name: api_github_com_443
  domains:
    - "api.github.com"
    - "api.github.com:443"
```

## Multiple Domains (New Format)

```yaml
- domains:
    - api.github.com
    - api-v2.github.com
    - raw.githubusercontent.com
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "GitHub APIs"
```

**Generated Envoy config:**
```yaml
- name: api_github_com_443
  domains:
    - "api.github.com"
    - "api.github.com:443"
    - "api-v2.github.com"
    - "api-v2.github.com:443"
    - "raw.githubusercontent.com"
    - "raw.githubusercontent.com:443"
```

**Benefits:**
- Single rule for related services
- Cleaner configuration
- Easier to maintain

## Wildcard Domains

### Subdomain Wildcard

```yaml
- domains:
    - "*.monitoring.internal"
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "All monitoring subdomains"
```

**Matches:**
- `grafana.monitoring.internal`
- `prometheus.monitoring.internal`
- `alertmanager.monitoring.internal`
- Any `*.monitoring.internal`

**Generated Envoy config:**
```yaml
- name: monitoring_internal_443
  domains:
    - "*.monitoring.internal"
```

**Note:** Wildcard domains don't get `:port` appended since they already match any port in the pattern.

### Multiple Wildcards

```yaml
- domains:
    - "*.api.corp.local"
    - "*.services.corp.local"
  port: 443
  protocol: http
  envs: [dev]
  description: "All internal service APIs - DEV"
```

**Matches:**
- `users.api.corp.local`
- `orders.api.corp.local`
- `payments.services.corp.local`
- Any subdomain of the specified domains

## Use Cases

### 1. Microservices with Multiple Versions

```yaml
- domains:
    - "api-v1.service.com"
    - "api-v2.service.com"
    - "api-v3.service.com"
  port: 443
  protocol: http
  envs: [prd]
  description: "Service API - all versions"
```

### 2. Regional Endpoints

```yaml
- domains:
    - "api-us.external.com"
    - "api-eu.external.com"
    - "api-asia.external.com"
  port: 443
  protocol: http
  envs: [prd]
  description: "External API - all regions"
```

### 3. Development Wildcards (Flexible Testing)

```yaml
- domains:
    - "*.dev.internal"
  port: 443
  protocol: http
  envs: [dev]
  description: "All dev subdomains - convenient for testing"
```

**Warning:** Be careful with wildcards in production!

### 4. Migration (Old and New Domains)

```yaml
- domains:
    - "api-legacy.corp.local"
    - "api.corp.local"
  port: 80
  protocol: http
  envs: [prd]
  description: "API - old and new endpoints during migration"
```

### 5. CDN with Multiple Domains

```yaml
- domains:
    - "cdn1.assets.com"
    - "cdn2.assets.com"
    - "cdn3.assets.com"
    - "static.assets.com"
  port: 443
  protocol: http
  envs: [prd]
  description: "CDN endpoints"
```

## Environment-Specific Domains

### Different domains per environment

```yaml
# DEV - wildcard for flexibility
- domains:
    - "*.dev.internal"
  port: 443
  protocol: http
  envs: [dev]
  description: "All dev services"

# STG - specific domains
- domains:
    - "api-stg.corp.local"
    - "auth-stg.corp.local"
  port: 443
  protocol: http
  envs: [stg]
  description: "STG services"

# PRD - production domains only
- domains:
    - "api.corp.local"
    - "auth.corp.local"
  port: 443
  protocol: http
  envs: [prd]
  description: "PRD services"
```

## Combining with TCP Rules

```yaml
egress:
  # HTTP - multiple domains
  - domains:
      - "api.github.com"
      - "raw.githubusercontent.com"
    port: 443
    protocol: http
    envs: [dev, stg, prd]
    description: "GitHub APIs"

  # TCP - multiple destinations
  - destinations:
      - redis-1.internal
      - redis-2.internal
      - 10.50.100.25
    port_range:
      start: 30000
      end: 30999
    protocol: tcp
    envs: [prd]
    description: "Redis cluster"
```

## Wildcard Patterns

### Supported Patterns

| Pattern | Matches | Example |
|---------|---------|---------|
| `*.example.com` | All subdomains | `api.example.com`, `www.example.com` |
| `*-api.example.com` | Subdomains ending in `-api` | `users-api.example.com` |
| `api-*.example.com` | Subdomains starting with `api-` | `api-v1.example.com` |

### NOT Supported

| Pattern | Why Not | Alternative |
|---------|---------|-------------|
| `*` | Too broad, matches everything | List specific domains |
| `*.*.example.com` | Multiple wildcards | Use `*.example.com` |
| `example.*` | TLD wildcard | List domains explicitly |

## Best Practices

### ✅ DO

```yaml
# Group related domains
- domains:
    - "api-v1.service.com"
    - "api-v2.service.com"
  port: 443
  protocol: http
  envs: [prd]
  description: "Service APIs - v1 and v2"

# Use wildcards for dev/testing
- domains:
    - "*.dev.internal"
  port: 443
  protocol: http
  envs: [dev]
  description: "All dev services"

# Be specific in production
- domains:
    - "api.corp.local"
    - "auth.corp.local"
  port: 443
  protocol: http
  envs: [prd]
  description: "Production services - explicit list"
```

### ❌ DON'T

```yaml
# Don't use wildcards in PRD without good reason
- domains:
    - "*.corp.local"  # Too broad!
  port: 443
  protocol: http
  envs: [prd]
  description: "All internal services"

# Don't mix unrelated services
- domains:
    - "api.github.com"
    - "random-tool.example.com"
    - "internal-db.corp.local"
  port: 443
  protocol: http
  envs: [prd]
  description: "Mixed services"  # Hard to understand

# Don't use "*" to match everything
- domains:
    - "*"  # NEVER DO THIS
  port: 443
  protocol: http
  envs: [dev]
```

## Generation Output

### Command
```bash
python generate-envoy-config.py --env prd
```

### Output
```
[HTTP] PRD: api.github.com, raw.githubusercontent.com:443
[HTTP] PRD: *.monitoring.internal:443
[HTTP] PRD: api.corp.local, auth.corp.local:443
```

## Testing

### 1. Verify domains resolve
```bash
# Check all domains in your list
nslookup api.github.com
nslookup api-v2.github.com
```

### 2. Test wildcard matches
```bash
# Envoy will log matched domains
kubectl logs -f deployment/envoy-proxy -n dev | jq 'select(.listener=="http_proxy")'
```

### 3. Generate and inspect config
```bash
python generate-envoy-config.py --env dev -o envoy-dev.yaml
grep -A5 "domains:" envoy-dev.yaml
```

## Troubleshooting

### Domain not matching

**Problem:** Traffic to `api.github.com:443` is denied

**Check:**
1. Is the domain in the list?
2. Is the port correct?
3. Is the environment tag correct?

```yaml
- domains:
    - "api.github.com"
  port: 443  # Make sure this matches
  protocol: http
  envs: [prd]  # Make sure this includes your environment
```

### Wildcard too broad

**Problem:** Wildcard `*.internal` matches too many services

**Solution:** Be more specific
```yaml
# Instead of:
- domains:
    - "*.internal"

# Use:
- domains:
    - "*.api.internal"
    - "*.services.internal"
```

### Mixed HTTP/HTTPS ports

**Problem:** Service uses both port 80 and 443

**Solution:** Create separate rules
```yaml
- domains:
    - "api.corp.local"
  port: 80
  protocol: http
  envs: [prd]

- domains:
    - "api.corp.local"
  port: 443
  protocol: http
  envs: [prd]
```

## Complete Example

```yaml
egress:
  # Shared services - all environments
  - domains:
      - "api.github.com"
      - "raw.githubusercontent.com"
      - "gist.githubusercontent.com"
    port: 443
    protocol: http
    envs: [dev, stg, prd]
    description: "GitHub APIs"

  # Dev - flexible wildcards
  - domains:
      - "*.dev.internal"
    port: 443
    protocol: http
    envs: [dev]
    description: "All dev services"

  # STG - specific services
  - domains:
      - "api-stg.corp.local"
      - "auth-stg.corp.local"
      - "data-stg.corp.local"
    port: 443
    protocol: http
    envs: [stg]
    description: "STG services"

  # PRD - explicit list with legacy
  - domains:
      - "api.corp.local"
      - "api-legacy.corp.local"
      - "auth.corp.local"
      - "data.corp.local"
    port: 443
    protocol: http
    envs: [prd]
    description: "PRD services (including legacy)"

  # Monitoring - all environments with wildcard
  - domains:
      - "*.monitoring.internal"
    port: 443
    protocol: http
    envs: [dev, stg, prd]
    description: "Monitoring services (Grafana, Prometheus, etc)"
```

## Related Documentation

- `README-ENV.md` - Environment-based configuration
- `PORT-RANGE-GUIDE.md` - TCP port ranges and multiple IPs
- `QUICKSTART.txt` - Quick reference