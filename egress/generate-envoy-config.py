#!/usr/bin/env python3
"""
generate-envoy-config.py

Generates Envoy egress proxy configuration from a simple allowlist.

Features:
- HTTP rules: uses hostname directly (matched by :authority header)
- TCP rules with hostname: resolves DNS to IP at generation time
- TCP rules with IP/CIDR: uses directly

Usage:
    python generate-envoy-config.py -a egress-allowlist.yaml -t envoy.yaml.j2 -o envoy.yaml
"""

import yaml
import socket
import ipaddress
import sys
import os
import re
from jinja2 import Environment, FileSystemLoader
from typing import List, Dict, Tuple, Optional


def is_ip_or_cidr(destination: str) -> bool:
    """Check if destination is an IP address or CIDR range."""
    try:
        ipaddress.ip_network(destination, strict=False)
        return True
    except ValueError:
        return False


def resolve_hostname(hostname: str, dns_server: Optional[str] = None) -> List[str]:
    """
    Resolve hostname to IP addresses.
    
    Args:
        hostname: The hostname to resolve
        dns_server: Optional DNS server to use (not implemented, uses system DNS)
    
    Returns:
        List of resolved IP addresses
    """
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET)
        ips = list(set(r[4][0] for r in results))
        return ips
    except socket.gaierror as e:
        print(f"WARNING: Could not resolve {hostname}: {e}", file=sys.stderr)
        return []


def parse_cidr(destination: str) -> Tuple[str, int]:
    """
    Parse IP or CIDR into (ip_address, prefix_len).
    
    Args:
        destination: IP address or CIDR notation
    
    Returns:
        Tuple of (network_address, prefix_length)
    """
    try:
        network = ipaddress.ip_network(destination, strict=False)
        return str(network.network_address), network.prefixlen
    except ValueError:
        # Single IP without /32
        return destination, 32


def sanitize_stat_name(name: str) -> str:
    """
    Sanitize a name for use in Envoy stats.
    
    Args:
        name: Original name
    
    Returns:
        Sanitized name with only alphanumeric and underscores
    """
    return re.sub(r'[^a-zA-Z0-9]', '_', name)


def process_allowlist(allowlist_path: str) -> Tuple[List[Dict], List[Dict]]:
    """
    Process allowlist and return (http_rules, tcp_rules).
    
    Args:
        allowlist_path: Path to the allowlist YAML file
    
    Returns:
        Tuple of (http_rules, tcp_rules) lists
    """
    with open(allowlist_path, 'r') as f:
        config = yaml.safe_load(f)
    
    http_rules = []
    tcp_rules = []
    dns_cache = {}  # Cache DNS lookups
    
    for rule in config.get('egress', []):
        destination = rule['destination']
        port = int(rule['port'])
        protocol = rule.get('protocol', 'tcp').lower()
        description = rule.get('description', '')
        
        if protocol == 'http':
            # HTTP rules - use hostname directly
            http_rules.append({
                'destination': destination,
                'port': port,
                'description': description,
            })
            print(f"[HTTP] Added: {destination}:{port}", file=sys.stderr)
        
        elif protocol == 'tcp':
            # TCP rules - need IP/CIDR for filter_chain_match
            if is_ip_or_cidr(destination):
                # Already an IP or CIDR
                ip_address, prefix_len = parse_cidr(destination)
                tcp_rules.append({
                    'destination': destination,
                    'original_destination': destination,
                    'ip_address': ip_address,
                    'prefix_len': prefix_len,
                    'port': port,
                    'description': description,
                    'stat_name': sanitize_stat_name(f"{destination}_{port}"),
                })
                print(f"[TCP]  Added: {destination}:{port} (CIDR)", file=sys.stderr)
            else:
                # Hostname - resolve to IP(s)
                if destination in dns_cache:
                    ips = dns_cache[destination]
                else:
                    ips = resolve_hostname(destination)
                    dns_cache[destination] = ips
                
                if not ips:
                    print(f"ERROR: Cannot resolve {destination}, skipping rule", file=sys.stderr)
                    continue
                
                for ip in ips:
                    tcp_rules.append({
                        'destination': destination,
                        'original_destination': destination,
                        'ip_address': ip,
                        'prefix_len': 32,
                        'port': port,
                        'description': f"{description}" if description else f"Resolved from {destination}",
                        'stat_name': sanitize_stat_name(f"{destination}_{port}"),
                    })
                    print(f"[TCP]  Added: {destination}:{port} -> {ip} (DNS resolved)", file=sys.stderr)
        
        else:
            print(f"WARNING: Unknown protocol '{protocol}' for {destination}, skipping", file=sys.stderr)
    
    return http_rules, tcp_rules


def generate_envoy_config(
    allowlist_path: str,
    template_path: str,
    output_path: str
) -> bool:
    """
    Generate Envoy config from allowlist using Jinja2 template.
    
    Args:
        allowlist_path: Path to the allowlist YAML file
        template_path: Path to the Jinja2 template file
        output_path: Path for the generated Envoy config
    
    Returns:
        True if successful, False otherwise
    """
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Generating Envoy config", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"Allowlist: {allowlist_path}", file=sys.stderr)
    print(f"Template:  {template_path}", file=sys.stderr)
    print(f"Output:    {output_path}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)
    
    # Check files exist
    if not os.path.exists(allowlist_path):
        print(f"ERROR: Allowlist file not found: {allowlist_path}", file=sys.stderr)
        return False
    
    if not os.path.exists(template_path):
        print(f"ERROR: Template file not found: {template_path}", file=sys.stderr)
        return False
    
    # Process allowlist
    http_rules, tcp_rules = process_allowlist(allowlist_path)
    
    print(f"\n{'─'*60}", file=sys.stderr)
    print(f"Summary:", file=sys.stderr)
    print(f"  HTTP rules: {len(http_rules)}", file=sys.stderr)
    print(f"  TCP rules:  {len(tcp_rules)}", file=sys.stderr)
    print(f"{'─'*60}\n", file=sys.stderr)
    
    # Load Jinja2 template
    template_dir = os.path.dirname(os.path.abspath(template_path)) or '.'
    template_name = os.path.basename(template_path)
    
    env = Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    
    try:
        template = env.get_template(template_name)
    except Exception as e:
        print(f"ERROR: Failed to load template: {e}", file=sys.stderr)
        return False
    
    # Render template
    try:
        output = template.render(
            http_rules=http_rules,
            tcp_rules=tcp_rules,
        )
    except Exception as e:
        print(f"ERROR: Failed to render template: {e}", file=sys.stderr)
        return False
    
    # Write output
    try:
        with open(output_path, 'w') as f:
            f.write(output)
    except Exception as e:
        print(f"ERROR: Failed to write output: {e}", file=sys.stderr)
        return False
    
    print(f"✓ Generated: {output_path}", file=sys.stderr)
    return True


def validate_envoy_config(config_path: str) -> bool:
    """
    Validate the generated Envoy config using envoy --mode validate.
    
    Args:
        config_path: Path to the Envoy config file
    
    Returns:
        True if valid, False otherwise
    """
    import subprocess
    
    try:
        result = subprocess.run(
            ['envoy', '--mode', 'validate', '-c', config_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(f"✓ Config validation passed", file=sys.stderr)
            return True
        else:
            print(f"✗ Config validation failed:", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            return False
    except FileNotFoundError:
        print(f"WARNING: envoy binary not found, skipping validation", file=sys.stderr)
        return True
    except subprocess.TimeoutExpired:
        print(f"WARNING: Validation timed out", file=sys.stderr)
        return True


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Generate Envoy egress config from allowlist',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -a allowlist.yaml -t envoy.yaml.j2 -o envoy.yaml
  %(prog)s --validate
        """
    )
    parser.add_argument(
        '-a', '--allowlist',
        default='egress-allowlist.yaml',
        help='Path to allowlist YAML (default: egress-allowlist.yaml)'
    )
    parser.add_argument(
        '-t', '--template',
        default='envoy.yaml.j2',
        help='Path to Jinja2 template (default: envoy.yaml.j2)'
    )
    parser.add_argument(
        '-o', '--output',
        default='envoy.yaml',
        help='Output path for generated config (default: envoy.yaml)'
    )
    parser.add_argument(
        '--validate',
        action='store_true',
        help='Validate generated config with envoy --mode validate'
    )
    
    args = parser.parse_args()
    
    # Generate config
    success = generate_envoy_config(
        allowlist_path=args.allowlist,
        template_path=args.template,
        output_path=args.output,
    )
    
    if not success:
        sys.exit(1)
    
    # Optionally validate
    if args.validate:
        if not validate_envoy_config(args.output):
            sys.exit(1)
    
    sys.exit(0)


if __name__ == '__main__':
    main()
