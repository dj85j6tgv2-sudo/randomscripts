# Environment-Based Egress Configuration

This directory contains tools to generate environment-specific Envoy egress proxy configurations from a single allowlist file.

## Overview

Instead of maintaining separate configuration files for DEV, STG, and PRD, we maintain:
- **Single source of truth**: `egress-allowlist.yaml` with environment tags
- **Single template**: `envoy.yaml.j2` 
- **Generation script**: `generate-envoy-config.py` that filters by environment

## Quick Start

```bash
# Generate config for a specific environment
python generate-envoy-config.py --env dev -o envoy-dev.yaml
python generate-envoy-config.py --env stg -o envoy-stg.yaml
python generate-envoy-config.py --env prd -o envoy-prd.yaml

# Or generate all at once
./generate-all-envs.sh

# With validation (requires envoy binary)
./generate-all-envs.sh --validate
```

## Configuration Format

### Single Environment Rule

```yaml
- destination: api.github.com
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "GitHub API - all environments"
```

### Environment-Specific Rules

```yaml
# DEV only
- destination: internal-api-dev.corp.local
  port: 80
  protocol: http
  envs: [dev]
  description: "Internal API - DEV"

# STG only
- destination: internal-api-stg.corp.local
  port: 80
  protocol: http
  envs: [stg]
  description: "Internal API - STG"

# PRD only
- destination: internal-api.corp.local
  port: 80
  protocol: http
  envs: [prd]
  description: "Internal API - PRD"
```

### TCP with Multiple Destinations and Port Range

```yaml
# PRD environment with HA Redis cluster
- destinations:
    - redis-prd-master.internal
    - redis-prd-replica-1.internal
    - redis-prd-replica-2.internal
    - 10.50.250.25
    - 10.50.250.26
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  envs: [prd]
  description: "Redis cluster - PRD (HA)"
```

## File Structure

```
egress/
├── egress-allowlist.yaml       # Source of truth with env tags
├── envoy.yaml.j2               # Jinja2 template
├── generate-envoy-config.py    # Generation script
├── generate-all-envs.sh        # Batch generation
├── resolve-hostnames.py        # DNS resolution utility
├── envoy-dev.yaml             # Generated - DEV
├── envoy-stg.yaml             # Generated - STG
└── envoy-prd.yaml             # Generated - PRD
```

## Environment Rules

### DEV Environment
- More permissive rules for development
- Direct database access allowed
- Lower security requirements
- Can include test/mock services

### STG Environment
- Production-like rules
- Limited direct access
- Staging endpoints
- Pre-production validation

### PRD Environment
- Strictest rules
- High availability configurations
- Production endpoints only
- Multiple replicas/IPs

## How It Works

### 1. Define Rules with Environment Tags

```yaml
egress:
  - destination: api.external.com
    port: 443
    protocol: http
    envs: [stg, prd]  # Only STG and PRD
    description: "External API"
```

### 2. Generate Environment-Specific Config

```bash
python generate-envoy-config.py --env prd
```

The script:
1. Reads `egress-allowlist.yaml`
2. Filters rules where `prd` is in `envs` list
3. Resolves hostnames to IPs for TCP rules
4. Renders `envoy.yaml.j2` template
5. Outputs `envoy-prd.yaml`

### 3. Generated Config Includes Environment Metadata

```yaml
# Environment: PRD
static_resources:
  listeners:
    - name: http_proxy
      # ... config for PRD environment only
```

## Examples

### HTTP Rule - All Environments

```yaml
- destination: api.github.com
  port: 443
  protocol: http
  envs: [dev, stg, prd]
  description: "GitHub API"
```

### HTTP Rule - Environment-Specific

```yaml
- destination: partner-api-staging.external.com
  port: 443
  protocol: http
  envs: [stg]
  description: "Partner API - Staging"

- destination: partner-api.external.com
  port: 443
  protocol: http
  envs: [prd]
  description: "Partner API - Production"
```

### TCP Rule - Single Port, Environment-Specific

```yaml
- destination: postgres-dev.db.internal
  port: 5432
  protocol: tcp
  envs: [dev]
  description: "PostgreSQL - DEV (direct access allowed)"

- destination: 172.16.20.5
  port: 5432
  protocol: tcp
  envs: [prd]
  description: "PostgreSQL - PRD"
```

### TCP Rule - Port Range, Multiple Destinations

```yaml
# DEV - smaller cluster
- destinations:
    - redis-dev-1.internal
    - redis-dev-2.internal
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  envs: [dev]
  description: "Redis cluster - DEV"

# PRD - HA cluster with more nodes
- destinations:
    - redis-prd-master.internal
    - redis-prd-replica-1.internal
    - redis-prd-replica-2.internal
    - 10.50.250.25
    - 10.50.250.26
    - 10.50.250.27
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  envs: [prd]
  description: "Redis cluster - PRD (HA)"
```

## Common Workflows

### Adding a New Service

1. Edit `egress-allowlist.yaml`:
```yaml
- destination: new-service-dev.corp.local
  port: 8080
  protocol: http
  envs: [dev]
  description: "New Service - DEV testing"
```

2. Generate DEV config:
```bash
python generate-envoy-config.py --env dev
```

3. Test in DEV, then promote to STG/PRD:
```yaml
- destination: new-service.corp.local
  port: 8080
  protocol: http
  envs: [dev, stg, prd]
  description: "New Service - all environments"
```

### Resolving Load Balancer IPs

```bash
# Check what IPs a hostname resolves to
python resolve-hostnames.py kafka-prd.internal.corp

# Update config with discovered IPs
- destinations:
    - kafka-prd.internal.corp  # Resolves to 3 IPs
    - 10.20.30.45              # Static backup
  port: 9093
  protocol: tcp
  envs: [prd]
```

### Deploying to Environments

```bash
# Generate all configs
./generate-all-envs.sh --validate

# Deploy to DEV (auto)
kubectl apply -f envoy-dev.yaml -n dev

# Deploy to STG (manual approval)
kubectl apply -f envoy-stg.yaml -n stg

# Deploy to PRD (requires PR approval)
git add envoy-prd.yaml
git commit -m "Add new egress rule for XYZ service"
git push
# Create PR → Review → Merge → Deploy
```

## Best Practices

### 1. Use Environment Tags Consistently

```yaml
# ✅ Good - explicit environment list
envs: [dev, stg, prd]

# ✅ Good - environment-specific
envs: [dev]

# ❌ Bad - missing envs (applies to ALL environments)
# Only omit if truly needed in all envs
```

### 2. Name Services by Environment

```yaml
# ✅ Good - clear naming
- destination: api-dev.corp.local
  envs: [dev]

- destination: api-stg.corp.local
  envs: [stg]

- destination: api.corp.local
  envs: [prd]

# ❌ Bad - confusing
- destination: api.corp.local
  envs: [dev]  # Hostname suggests PRD but only for DEV?
```

### 3. Document Why Rules Differ

```yaml
- destination: postgres-dev.db.internal
  port: 5432
  protocol: tcp
  envs: [dev]
  description: "PostgreSQL - DEV (direct access for debugging)"

- destination: postgres-pooler.db.internal
  port: 5432
  protocol: tcp
  envs: [prd]
  description: "PostgreSQL - PRD (via connection pooler)"
```

### 4. Use Port Ranges for Dynamic Services

```yaml
- destinations:
    - redis-cluster.internal
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  envs: [prd]
  description: "Redis cluster - K8s NodePort range 30000-30999"
```

### 5. Start Restrictive, Expand as Needed

```yaml
# Start: DEV only
envs: [dev]

# After testing: Add STG
envs: [dev, stg]

# After STG validation: Add PRD
envs: [dev, stg, prd]
```

## Troubleshooting

### Config Not Generated for Environment

**Problem**: Rule exists in allowlist but not in generated config

**Check**:
```yaml
# Does the rule have the right env tag?
envs: [dev, stg, prd]  # Should include your target env
```

### Hostname Resolution Fails

**Problem**: DNS resolution fails during generation

**Debug**:
```bash
# Check DNS resolution
python resolve-hostnames.py problematic-hostname.internal

# Use static IPs temporarily
- destination: 10.50.100.25
  port: 6379
  protocol: tcp
  envs: [dev]
```

### Too Many Rules in PRD

**Problem**: PRD config is huge, slow to load

**Solution**: Use CIDR ranges instead of individual IPs
```yaml
# ❌ Bad - 256 individual IPs
- destination: 10.0.1.1
- destination: 10.0.1.2
# ... 254 more

# ✅ Good - single CIDR
- destination: 10.0.1.0/24
  port: 9092
  protocol: tcp
  envs: [prd]
```

### Different IPs in Each Environment

**Problem**: Same service has different IPs per environment

**Solution**: Separate rules per environment
```yaml
- destinations:
    - 10.50.100.10  # DEV Redis
  port: 6379
  protocol: tcp
  envs: [dev]

- destinations:
    - 10.50.200.10  # STG Redis
  port: 6379
  protocol: tcp
  envs: [stg]

- destinations:
    - 10.50.250.25  # PRD Redis master
    - 10.50.250.26  # PRD Redis replica 1
    - 10.50.250.27  # PRD Redis replica 2
  port: 6379
  protocol: tcp
  envs: [prd]
```

## Monitoring

Generated configs include environment metadata in logs:

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "environment": "prd",
  "listener": "http_proxy",
  "decision": "ALLOWED",
  "destination": "api.external.com:443"
}
```

Query by environment:
```bash
# View PRD egress logs
kubectl logs -f deployment/envoy-proxy -n prd | jq 'select(.environment=="prd")'

# Check denied requests in STG
kubectl logs deployment/envoy-proxy -n stg | jq 'select(.decision=="DENIED")'
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Generate Envoy Configs

on:
  push:
    paths:
      - 'egress/egress-allowlist.yaml'

jobs:
  generate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Install dependencies
        run: pip install pyyaml jinja2
      
      - name: Generate all environment configs
        run: |
          cd egress
          ./generate-all-envs.sh
      
      - name: Commit generated configs
        run: |
          git config user.name "GitHub Actions"
          git config user.email "actions@github.com"
          git add envoy-*.yaml
          git commit -m "Auto-generate envoy configs" || true
          git push
```

## Security Considerations

1. **DEV**: More permissive for rapid development
2. **STG**: Production-like restrictions for testing
3. **PRD**: Strictest rules, change control required

### PRD Deployment Checklist

- [ ] Rule tested in DEV
- [ ] Rule validated in STG
- [ ] Security review completed
- [ ] PR approved by 2+ reviewers
- [ ] Generated config validated: `envoy --mode validate -c envoy-prd.yaml`
- [ ] Monitoring alerts configured
- [ ] Rollback plan documented

## Related Files

- `egress-allowlist.yaml` - Source configuration
- `envoy.yaml.j2` - Jinja2 template
- `generate-envoy-config.py` - Generator script
- `resolve-hostnames.py` - DNS resolution utility
- `PORT-RANGE-GUIDE.md` - Port range documentation