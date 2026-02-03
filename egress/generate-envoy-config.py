#!/usr/bin/env python3
"""
generate-envoy-config.py

Generates environment-specific Envoy egress proxy configuration from a simple allowlist.

Features:
- Environment filtering: Generate config for specific environment (dev/stg/prd)
- HTTP rules: uses hostname directly (matched by :authority header)
- TCP rules with hostname: resolves DNS to IP at generation time
- TCP rules with IP/CIDR: uses directly
- Port ranges: Support for destination_port_range
- Multiple destinations: Single rule can have multiple IPs/hostnames

Usage:
    # With bundled template (from package)
    python -m jenkins_tools.generate_envoy_config --env dev -a egress-allowlist.yaml -o envoy-dev.yaml

    # With custom template
    python generate-envoy-config.py --env dev -a egress-allowlist.yaml -t envoy.yaml.j2 -o envoy-dev.yaml
"""

import ipaddress
import os
import re
import socket
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml
from jinja2 import Environment, FileSystemLoader


def get_bundled_template_path() -> Optional[str]:
    """
    Get the path to the bundled envoy.yaml.j2 template.

    This function tries multiple methods to locate the template:
    1. importlib.resources (Python 3.9+)
    2. importlib_resources (backport)
    3. pkg_resources (setuptools)
    4. Relative to this script file

    The template is expected to be at: jenkins_tools/config/envoy.yaml.j2

    Returns:
        Path to the template file, or None if not found
    """
    # Method 1: Try importlib.resources (Python 3.9+)
    try:
        import importlib.resources as resources

        # For Python 3.9+
        try:
            files = resources.files("jenkins_tools.config")
            template_path = files.joinpath("envoy.yaml.j2")
            if hasattr(template_path, "exists"):
                # Python 3.9+
                if template_path.exists():
                    return str(template_path)
            else:
                # Try to read it (will fail if doesn't exist)
                with resources.as_file(template_path) as p:
                    if p.exists():
                        return str(p)
        except (TypeError, AttributeError, FileNotFoundError):
            pass

        # For Python 3.7-3.8
        try:
            with resources.path("jenkins_tools.config", "envoy.yaml.j2") as p:
                if p.exists():
                    return str(p)
        except (TypeError, FileNotFoundError, ModuleNotFoundError):
            pass
    except ImportError:
        pass

    # Method 2: Try pkg_resources (setuptools)
    try:
        import pkg_resources

        template_path = pkg_resources.resource_filename(
            "jenkins_tools", "config/envoy.yaml.j2"
        )
        if os.path.exists(template_path):
            return template_path
    except (ImportError, Exception):
        pass

    # Method 3: Relative to this script (for development/standalone use)
    script_dir = Path(__file__).parent

    # Try config subdirectory
    template_path = script_dir / "config" / "envoy.yaml.j2"
    if template_path.exists():
        return str(template_path)

    # Try same directory as script (legacy/standalone)
    template_path = script_dir / "envoy.yaml.j2"
    if template_path.exists():
        return str(template_path)

    return None


def get_template_path(user_provided: Optional[str] = None) -> str:
    """
    Determine the template path to use.

    Priority:
    1. User-provided path (if specified and exists)
    2. Bundled template from package (config/envoy.yaml.j2)
    3. Default 'envoy.yaml.j2' in current directory

    Args:
        user_provided: User-specified template path

    Returns:
        Path to the template file
    """
    # If user provided a path and it exists, use it
    if user_provided and os.path.exists(user_provided):
        print(f"Using user-provided template: {user_provided}", file=sys.stderr)
        return user_provided

    # Try to find bundled template
    bundled_template = get_bundled_template_path()
    if bundled_template:
        print(f"Using bundled template: {bundled_template}", file=sys.stderr)
        return bundled_template

    # Fall back to user-provided path (even if doesn't exist - will error later)
    if user_provided:
        print(f"Template not found, trying: {user_provided}", file=sys.stderr)
        return user_provided

    # Last resort: default name in current directory
    default_path = "envoy.yaml.j2"
    print(f"Using default template path: {default_path}", file=sys.stderr)
    return default_path


def is_ip_or_cidr(destination: str) -> bool:
    """Check if destination is an IP address or CIDR range."""
    try:
        ipaddress.ip_network(destination, strict=False)
        return True
    except ValueError:
        return False


def resolve_hostname(hostname: str) -> List[str]:
    """
    Resolve hostname to IP addresses.

    Args:
        hostname: The hostname to resolve

    Returns:
        List of resolved IP addresses
    """
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET)
        ips = list(set(r[4][0] for r in results))
        return sorted(ips)
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


def sanitize_name(name: str) -> str:
    """
    Sanitize a name for use in Envoy stats and filter chain names.

    Args:
        name: Original name

    Returns:
        Sanitized name with only alphanumeric and underscores
    """
    return re.sub(r"[^a-zA-Z0-9]", "_", name)


def rule_applies_to_env(rule: Dict, target_env: str) -> bool:
    """
    Check if a rule applies to the target environment.

    Args:
        rule: The egress rule
        target_env: Target environment (dev/stg/prd)

    Returns:
        True if rule applies to this environment
    """
    envs = rule.get("envs", [])

    # If no envs specified, apply to all environments
    if not envs:
        return True

    return target_env in envs


def process_http_rules(rules: List[Dict], target_env: str) -> List[Dict]:
    """
    Process HTTP/HTTPS rules for the target environment.

    Args:
        rules: List of all egress rules
        target_env: Target environment (dev/stg/prd)

    Returns:
        List of processed HTTP rules
    """
    http_rules = []

    for rule in rules:
        protocol = rule.get("protocol", "tcp").lower()
        if protocol != "http":
            continue

        if not rule_applies_to_env(rule, target_env):
            continue

        # Support both 'destination' (single) and 'domains' (multiple)
        domains = []
        if "domains" in rule:
            domains = rule["domains"]
        elif "destination" in rule:
            domains = [rule["destination"]]
        else:
            print(
                f"WARNING: HTTP rule missing 'destination' or 'domains', skipping",
                file=sys.stderr,
            )
            continue

        port = int(rule.get("port", 443))
        description = rule.get("description", "")
        envs = rule.get("envs", ["dev", "stg", "prd"])

        # Create rule name from first domain
        first_domain = domains[0]
        rule_name = sanitize_name(f"{first_domain}_{port}")

        http_rules.append(
            {
                "name": rule_name,
                "domains": domains,
                "port": port,
                "description": description,
                "envs": envs,
            }
        )

        # Log with domain list
        domains_str = ", ".join(domains)
        print(
            f"[HTTP] {target_env.upper()}: {domains_str}:{port}",
            file=sys.stderr,
        )

    return http_rules


def process_tcp_rules(
    rules: List[Dict], target_env: str, dns_cache: Dict[str, List[str]]
) -> List[Dict]:
    """
    Process TCP rules for the target environment.

    Args:
        rules: List of all egress rules
        target_env: Target environment (dev/stg/prd)
        dns_cache: DNS resolution cache

    Returns:
        List of processed TCP rules
    """
    tcp_rules = []

    for rule in rules:
        protocol = rule.get("protocol", "tcp").lower()
        if protocol != "tcp":
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
                f"WARNING: TCP rule missing 'destination' or 'destinations', skipping",
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
                f"WARNING: TCP rule missing 'port' or 'port_range', skipping",
                file=sys.stderr,
            )
            continue

        # Resolve all destinations to IPs
        ip_addresses = []
        resolved_destinations = []

        for destination in destinations:
            if is_ip_or_cidr(destination):
                # Already an IP or CIDR
                ip_address, prefix_len = parse_cidr(destination)
                ip_addresses.append(
                    {
                        "address": ip_address,
                        "prefix_len": prefix_len,
                    }
                )
                resolved_destinations.append(destination)
            else:
                # Hostname - resolve to IP(s)
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
            print(f"WARNING: No valid IPs for rule, skipping", file=sys.stderr)
            continue

        # Create rule name
        if port:
            port_str = str(port)
        else:
            port_str = f"{port_range['start']}_{port_range['end']}"

        # Use first destination for naming
        first_dest = destinations[0]
        rule_name = sanitize_name(f"allow_{first_dest}_{port_str}")
        stat_name = sanitize_name(f"{first_dest}_{port_str}")

        tcp_rule = {
            "name": rule_name,
            "description": description,
            "destinations": destinations,
            "ip_addresses": ip_addresses,
            "stat_name": stat_name,
            "envs": envs,
        }

        if port:
            tcp_rule["port"] = port
        else:
            tcp_rule["port_range"] = port_range

        tcp_rules.append(tcp_rule)

        # Logging
        if port:
            dest_summary = ", ".join(resolved_destinations)
            print(
                f"[TCP]  {target_env.upper()}: {dest_summary}:{port} ({len(ip_addresses)} IPs)",
                file=sys.stderr,
            )
        else:
            dest_summary = ", ".join(resolved_destinations)
            print(
                f"[TCP]  {target_env.upper()}: {dest_summary}:{port_range['start']}-{port_range['end']} ({len(ip_addresses)} IPs)",
                file=sys.stderr,
            )

    return tcp_rules


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


def generate_envoy_config(
    allowlist_path: str, template_path: str, output_path: str, target_env: str
) -> bool:
    """
    Generate Envoy config from allowlist using Jinja2 template.

    Args:
        allowlist_path: Path to the allowlist YAML file
        template_path: Path to the Jinja2 template file
        output_path: Path for the generated Envoy config
        target_env: Target environment (dev/stg/prd)

    Returns:
        True if successful, False otherwise
    """
    print(f"\n{'=' * 70}", file=sys.stderr)
    print(
        f"Generating Envoy config for environment: {target_env.upper()}",
        file=sys.stderr,
    )
    print(f"{'=' * 70}", file=sys.stderr)
    print(f"Allowlist: {allowlist_path}", file=sys.stderr)
    print(f"Template:  {template_path}", file=sys.stderr)
    print(f"Output:    {output_path}", file=sys.stderr)
    print(f"{'=' * 70}\n", file=sys.stderr)

    # Check files exist
    if not os.path.exists(allowlist_path):
        print(f"ERROR: Allowlist file not found: {allowlist_path}", file=sys.stderr)
        return False

    if not os.path.exists(template_path):
        print(f"ERROR: Template file not found: {template_path}", file=sys.stderr)
        return False

    # Process allowlist
    http_rules, tcp_rules = process_allowlist(allowlist_path, target_env)

    print(f"\n{'─' * 70}", file=sys.stderr)
    print(f"Summary for {target_env.upper()}:", file=sys.stderr)
    print(f"  HTTP rules: {len(http_rules)}", file=sys.stderr)
    print(f"  TCP rules:  {len(tcp_rules)}", file=sys.stderr)
    total_ips = sum(len(rule["ip_addresses"]) for rule in tcp_rules)
    print(f"  Total IPs:  {total_ips}", file=sys.stderr)
    print(f"{'─' * 70}\n", file=sys.stderr)

    # Load Jinja2 template
    template_dir = os.path.dirname(os.path.abspath(template_path)) or "."
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
            target_env=target_env,
        )
    except Exception as e:
        print(f"ERROR: Failed to render template: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return False

    # Write output
    try:
        with open(output_path, "w") as f:
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
            ["envoy", "--mode", "validate", "-c", config_path],
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
        description="Generate environment-specific Envoy egress config from allowlist",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate config using bundled template (recommended)
  %(prog)s --env dev -a egress-allowlist.yaml -o envoy-dev.yaml
  %(prog)s --env prd -a egress-allowlist.yaml -o envoy-prd.yaml --validate

  # Generate all environments
  %(prog)s --env dev -o envoy-dev.yaml
  %(prog)s --env stg -o envoy-stg.yaml
  %(prog)s --env prd -o envoy-prd.yaml

  # Use custom template
  %(prog)s --env prd -t /path/to/custom-envoy.yaml.j2 -o envoy-prd.yaml

  # As Python module (when installed as package)
  python3 -m jenkins_tools.generate_envoy_config --env prd -a egress-allowlist.yaml
        """,
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=["dev", "stg", "prd"],
        help="Target environment (dev/stg/prd)",
    )
    parser.add_argument(
        "-a",
        "--allowlist",
        default="egress-allowlist.yaml",
        help="Path to allowlist YAML (default: egress-allowlist.yaml)",
    )
    parser.add_argument(
        "-t",
        "--template",
        default=None,
        help="Path to Jinja2 template (default: use bundled template from config/envoy.yaml.j2)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output path for generated config (default: envoy-{env}.yaml)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate generated config with envoy --mode validate",
    )

    args = parser.parse_args()

    # Default output filename
    if not args.output:
        args.output = f"envoy-{args.env}.yaml"

    # Resolve template path (use bundled if not specified)
    template_path = get_template_path(args.template)

    # Generate config
    success = generate_envoy_config(
        allowlist_path=args.allowlist,
        template_path=template_path,
        output_path=args.output,
        target_env=args.env,
    )

    if not success:
        sys.exit(1)

    # Optionally validate
    if args.validate:
        if not validate_envoy_config(args.output):
            sys.exit(1)

    print(
        f"\n✓ Success! Generated config for {args.env.upper()}: {args.output}\n",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
