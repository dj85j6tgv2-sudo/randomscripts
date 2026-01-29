# Port Range and Multiple IPs Guide

This guide explains how to configure TCP egress rules with port ranges and multiple IP addresses, which is useful for load balancers and distributed services.

## Table of Contents

- [Overview](#overview)
- [Configuration Format](#configuration-format)
- [Use Cases](#use-cases)
- [Hostname Resolution](#hostname-resolution)
- [Examples](#examples)
- [Template Processing](#template-processing)

## Overview

For TCP egress rules, Envoy supports:

1. **Port Ranges**: Allow traffic to a range of ports (e.g., 30000-30999)
2. **Multiple IPs**: Allow traffic to multiple IP addresses with the same port/port range
3. **Mixed Destinations**: Combine hostnames (resolved at deploy time) and static IPs

This is particularly useful for:
- Redis/Kafka clusters with dynamic port allocation
- Load balancers that return multiple IPs via DNS
- Services running on ephemeral ports

## Configuration Format

### Single Port, Single Destination (Legacy)

```yaml
- destination: internal-redis.net.intra
  port: 6379
  protocol: tcp
  description: "Redis cache"
```

### Port Range, Multiple Destinations (New)

```yaml
- destinations:
    - internal-redis.net.intra    # Hostname (resolved at deploy time)
    - 10.50.100.26                # Static IP
    - 10.50.100.27                # Static IP
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  description: "Redis cluster with port range"
```

### Single Port, Multiple Destinations

```yaml
- destinations:
    - kafka-1.internal.corp
    - kafka-2.internal.corp
    - 10.20.30.40
  port: 9093
  protocol: tcp
  description: "Kafka brokers"
```

## Use Cases

### 1. Redis Cluster with Dynamic Ports

Redis Cluster uses a base port (e.g., 6379) plus a cluster bus port (base + 10000). For multiple nodes with ephemeral ports:

```yaml
- destinations:
    - redis-node-1.internal
    - redis-node-2.internal
    - redis-node-3.internal
  port_range:
    start: 6379
    end: 16379
  protocol: tcp
  description: "Redis cluster (data + bus ports)"
```

### 2. Kafka Cluster Behind Load Balancer

If `kafka.internal.corp` resolves to multiple IPs (DNS round-robin):

```yaml
- destinations:
    - kafka.internal.corp         # Resolves to: 10.20.1.5, 10.20.1.6, 10.20.1.7
  port: 9092
  protocol: tcp
  description: "Kafka cluster via load balancer"
```

The generation script will resolve this to:

```yaml
prefix_ranges:
  - address_prefix: "10.20.1.5"
    prefix_len: 32
  - address_prefix: "10.20.1.6"
    prefix_len: 32
  - address_prefix: "10.20.1.7"
    prefix_len: 32
```

### 3. Service Mesh Sidecar Ports

Allow traffic to multiple pods with ephemeral ports:

```yaml
- destinations:
    - 172.16.10.0/24              # Pod CIDR
  port_range:
    start: 15000
    end: 15010
  protocol: tcp
  description: "Service mesh control plane"
```

## Hostname Resolution

### Finding IPs Behind a Load Balancer

Use the provided utility script:

```bash
# Resolve a single hostname
python resolve-hostnames.py internal-redis.net.intra

# Output:
# 🔍 Resolving: internal-redis.net.intra
# ✅ Found 3 IP address(es):
#    - 10.50.100.25
#    - 10.50.100.26
#    - 10.50.100.27
#
# 📝 YAML snippet for egress-allowlist.yaml:
#    prefix_ranges:
#      - address_prefix: "10.50.100.25"
#        prefix_len: 32
#      - address_prefix: "10.50.100.26"
#        prefix_len: 32
#      - address_prefix: "10.50.100.27"
#        prefix_len: 32
```

### Resolve All Hostnames in Configuration

```bash
python resolve-hostnames.py --file egress-allowlist.yaml
```

This will scan your entire egress configuration and resolve all hostnames, showing:
- How many IPs each hostname resolves to
- Which destinations need updating if IPs change
- Potential issues with unresolvable hostnames

### Manual DNS Resolution

If you don't have the script available:

```bash
# Linux/macOS
dig +short internal-redis.net.intra
nslookup internal-redis.net.intra
host internal-redis.net.intra

# Windows
nslookup internal-redis.net.intra

# Using Python
python -c "import socket; print('\n'.join(set([ip[4][0] for ip in socket.getaddrinfo('internal-redis.net.intra', None)])))"
```

## Examples

### Example 1: Complete Redis Cluster Configuration

**egress-allowlist.yaml:**
```yaml
- destinations:
    - redis-master.internal       # Resolves to 10.50.100.25
    - redis-replica-1.internal    # Resolves to 10.50.100.26
    - redis-replica-2.internal    # Resolves to 10.50.100.27
    - 10.50.100.28                # Static IP for emergency node
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  description: "Redis cluster with ephemeral ports"
```

**Generated envoy.yaml:**
```yaml
- name: "allow_redis_cluster_30000_30999"
  filter_chain_match:
    destination_port_range:
      start: 30000
      end: 30999
    prefix_ranges:
      - address_prefix: "10.50.100.25"
        prefix_len: 32
      - address_prefix: "10.50.100.26"
        prefix_len: 32
      - address_prefix: "10.50.100.27"
        prefix_len: 32
      - address_prefix: "10.50.100.28"
        prefix_len: 32
  filters:
    - name: envoy.filters.network.tcp_proxy
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.filters.network.tcp_proxy.v3.TcpProxy
        stat_prefix: tcp_redis_cluster
        cluster: original_dst
```

### Example 2: Kafka with Mixed IPs and Hostnames

**egress-allowlist.yaml:**
```yaml
- destinations:
    - kafka-broker-1.corp         # Hostname
    - kafka-broker-2.corp         # Hostname
    - 10.20.30.45                 # Direct IP
    - 10.20.30.46                 # Direct IP
  port: 9093
  protocol: tcp
  description: "Kafka brokers (mixed hostnames and IPs)"
```

### Example 3: Database Connection Pool

**egress-allowlist.yaml:**
```yaml
- destinations:
    - postgres-primary.db.internal
    - postgres-replica.db.internal
  port: 5432
  protocol: tcp
  description: "PostgreSQL primary and read replica"

- destinations:
    - postgres-replica.db.internal
  port: 5433
  protocol: tcp
  description: "PostgreSQL replica on alternate port"
```

## Template Processing

The `envoy.yaml.j2` template processes these rules as follows:

### Port Range vs Single Port

```jinja2
{% if rule.port_range %}
destination_port_range:
  start: {{ rule.port_range.start }}
  end: {{ rule.port_range.end }}
{% else %}
destination_port: {{ rule.port }}
{% endif %}
```

### Multiple IP Addresses

```jinja2
prefix_ranges:
{% for ip in rule.ip_addresses %}
  - address_prefix: "{{ ip.address }}"
    prefix_len: {{ ip.prefix_len }}
{% endfor %}
```

### Expected Data Structure

Your generation script should transform the YAML into:

```python
{
    'name': 'allow_redis_cluster_30000_30999',
    'description': 'Redis cluster with ephemeral ports',
    'port_range': {
        'start': 30000,
        'end': 30999
    },
    'ip_addresses': [
        {'address': '10.50.100.25', 'prefix_len': 32},
        {'address': '10.50.100.26', 'prefix_len': 32},
        {'address': '10.50.100.27', 'prefix_len': 32},
        {'address': '10.50.100.28', 'prefix_len': 32},
    ]
}
```

## Best Practices

### 1. Periodic Hostname Re-resolution

Load balancer IPs can change. Set up automated checks:

```bash
# In your CI/CD pipeline
python resolve-hostnames.py --file egress-allowlist.yaml > current-ips.txt
diff current-ips.txt previous-ips.txt || echo "IPs changed - update config"
```

### 2. Use CIDR for Pod Networks

Instead of listing individual pod IPs:

```yaml
# ❌ Don't do this
- destinations:
    - 172.16.10.1
    - 172.16.10.2
    - 172.16.10.3
    # ... 253 more IPs
  port: 9090

# ✅ Do this
- destination: 172.16.10.0/24
  port: 9090
```

### 3. Document Why Port Ranges Are Needed

```yaml
- destinations:
    - redis-cluster.internal
  port_range:
    start: 30000
    end: 30999
  protocol: tcp
  description: "Redis cluster - ports 30000-30999 allocated by K8s NodePort"
  # ^ Explains WHY the range is needed
```

### 4. Test After IP Changes

When IPs behind a hostname change:

```bash
# 1. Verify current resolution
python resolve-hostnames.py kafka.internal.corp

# 2. Re-generate config
python generate-envoy-config.py -a egress-allowlist.yaml -t envoy.yaml.j2 -o envoy.yaml

# 3. Validate Envoy config
envoy --mode validate -c envoy.yaml

# 4. Deploy and test connectivity
```

## Troubleshooting

### Issue: Hostname resolves to different IPs

**Symptom:**
```bash
$ python resolve-hostnames.py lb.internal.corp
✅ Found 4 IP address(es):
   - 10.1.1.5
   - 10.1.1.6
   - 10.1.1.7
   - 10.1.1.8

# 5 minutes later...
$ python resolve-hostnames.py lb.internal.corp
✅ Found 3 IP address(es):
   - 10.1.1.6
   - 10.1.1.7
   - 10.1.1.9
```

**Solution:** Use all possible IPs or resolve at runtime:
```yaml
- destinations:
    - 10.1.1.5
    - 10.1.1.6
    - 10.1.1.7
    - 10.1.1.8
    - 10.1.1.9
  port: 8080
  protocol: tcp
  description: "Load balancer - all possible backend IPs"
```

### Issue: Traffic still blocked despite correct IPs

**Check:** Verify port range is correct:
```bash
# Monitor denied connections
docker logs envoy-proxy | grep "DENIED"

# Look for:
# {"decision":"DENIED","destination":"10.50.100.25:30672"}
#                                                   ^^^^^ Check this port
```

If port is outside your range, expand it:
```yaml
port_range:
  start: 30000
  end: 31000  # Expanded from 30999
```

### Issue: Too many IPs in prefix_ranges

**Symptom:** Envoy config becomes huge with hundreds of IPs.

**Solution:** Use CIDR aggregation:
```python
# Instead of 256 individual IPs
from ipaddress import ip_network

ips = ['10.0.1.1', '10.0.1.2', ..., '10.0.1.254']
network = ip_network('10.0.1.0/24')

# Use in config:
- destination: 10.0.1.0/24
  port: 9092
```

## Related Files

- `egress-allowlist.yaml` - Source of truth for egress rules
- `envoy.yaml.j2` - Jinja2 template for Envoy configuration
- `envoy.yaml` - Generated Envoy configuration (example)
- `resolve-hostnames.py` - Utility to resolve hostnames to IPs
- `generate-envoy-config.py` - Script to generate Envoy config from allowlist

## References

- [Envoy Filter Chain Match](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/listener/v3/listener_components.proto#config-listener-v3-filterchainmatch)
- [TCP Proxy Filter](https://www.envoyproxy.io/docs/envoy/latest/configuration/listeners/network_filters/tcp_proxy_filter)
- [Original Destination Cluster](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/service_discovery#arch-overview-service-discovery-types-original-destination)