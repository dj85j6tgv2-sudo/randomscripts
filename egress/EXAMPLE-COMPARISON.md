# Environment Configuration Comparison

This shows how the same allowlist generates different configs for each environment.

## Source: egress-allowlist.yaml

```yaml
egress:
  # Rule 1: All environments
  - destination: api.github.com
    port: 443
    protocol: http
    envs: [dev, stg, prd]
    description: "GitHub API"

  # Rule 2: DEV only
  - destination: redis-dev.internal
    port: 6379
    protocol: tcp
    envs: [dev]
    description: "Redis - DEV"

  # Rule 3: STG only
  - destination: redis-stg.internal
    port: 6379
    protocol: tcp
    envs: [stg]
    description: "Redis - STG"

  # Rule 4: PRD only (HA with multiple IPs)
  - destinations:
      - redis-prd-master.internal
      - redis-prd-replica.internal
    port_range:
      start: 6379
      end: 6389
    protocol: tcp
    envs: [prd]
    description: "Redis - PRD (HA cluster)"
```

## Generated Configs

### DEV: envoy-dev.yaml
```
HTTP rules: 1
  - api.github.com:443

TCP rules: 1
  - redis-dev.internal:6379 (1 IP)
```

### STG: envoy-stg.yaml
```
HTTP rules: 1
  - api.github.com:443

TCP rules: 1
  - redis-stg.internal:6379 (1 IP)
```

### PRD: envoy-prd.yaml
```
HTTP rules: 1
  - api.github.com:443

TCP rules: 1
  - redis-prd-master.internal, redis-prd-replica.internal:6379-6389 (N IPs)
```

## Usage

```bash
# Generate DEV config
python generate-envoy-config.py --env dev

# Output:
# [HTTP] DEV: api.github.com:443
# [TCP]  DEV: redis-dev.internal:6379 (1 IPs)
# ✓ Generated: envoy-dev.yaml

# Generate STG config
python generate-envoy-config.py --env stg

# Output:
# [HTTP] STG: api.github.com:443
# [TCP]  STG: redis-stg.internal:6379 (1 IPs)
# ✓ Generated: envoy-stg.yaml

# Generate PRD config
python generate-envoy-config.py --env prd

# Output:
# [HTTP] PRD: api.github.com:443
# [TCP]  PRD: redis-prd-master.internal->10.50.250.25, redis-prd-replica.internal->10.50.250.26:6379-6389 (2 IPs)
# ✓ Generated: envoy-prd.yaml
```

## Key Points

1. **Shared rules** (github.com) appear in all environments
2. **Environment-specific rules** only appear in their target environment
3. **PRD rules** often have:
   - Multiple IPs (HA)
   - Port ranges (dynamic ports)
   - More replicas
4. **DEV/STG rules** are simpler, single instances
