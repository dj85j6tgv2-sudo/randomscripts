#!/usr/bin/env python3
"""
resolve-hostnames.py

Utility to resolve hostnames to IP addresses for egress configuration.
Useful for discovering all IPs behind load balancers and updating egress rules.

Usage:
    python resolve-hostnames.py <hostname> [<hostname2> ...]
    python resolve-hostnames.py --file egress-allowlist.yaml
"""

import argparse
import socket
import sys
from typing import List, Set

import yaml


def resolve_hostname(hostname: str) -> Set[str]:
    """
    Resolve a hostname to all its IP addresses.

    Args:
        hostname: The hostname to resolve

    Returns:
        Set of IP addresses (strings)
    """
    ips = set()

    try:
        # Get all address info (handles both IPv4 and IPv6)
        addr_info = socket.getaddrinfo(hostname, None)

        for info in addr_info:
            # info[4] is the sockaddr tuple, [0] is the IP address
            ip = info[4][0]
            # Filter out IPv6 addresses if needed (or handle separately)
            if ":" not in ip:  # Simple IPv4 check
                ips.add(ip)

        return ips
    except socket.gaierror as e:
        print(f"❌ Failed to resolve {hostname}: {e}", file=sys.stderr)
        return set()
    except Exception as e:
        print(f"❌ Unexpected error resolving {hostname}: {e}", file=sys.stderr)
        return set()


def is_hostname(destination: str) -> bool:
    """
    Check if a destination is a hostname (not an IP or CIDR).

    Args:
        destination: The destination string

    Returns:
        True if it's a hostname, False if it's an IP/CIDR
    """
    # Check if it's a CIDR notation
    if "/" in destination:
        return False

    # Try to parse as IP address
    try:
        socket.inet_aton(destination)
        return False  # It's an IP address
    except socket.error:
        return True  # It's a hostname


def resolve_from_file(filepath: str) -> None:
    """
    Read egress-allowlist.yaml and resolve all hostnames.

    Args:
        filepath: Path to the egress-allowlist.yaml file
    """
    try:
        with open(filepath, "r") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"❌ File not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"❌ YAML parsing error: {e}", file=sys.stderr)
        sys.exit(1)

    if "egress" not in config:
        print("❌ No 'egress' key found in YAML", file=sys.stderr)
        sys.exit(1)

    print("🔍 Resolving hostnames from egress configuration...\n")

    for idx, rule in enumerate(config["egress"]):
        protocol = rule.get("protocol", "unknown")
        description = rule.get("description", "No description")

        # Handle single destination
        if "destination" in rule:
            destination = rule["destination"]
            if is_hostname(destination):
                print(f"📋 Rule {idx + 1}: {description}")
                print(f"   Protocol: {protocol}")
                print(f"   Hostname: {destination}")

                ips = resolve_hostname(destination)
                if ips:
                    print(f"   Resolved to {len(ips)} IP(s):")
                    for ip in sorted(ips):
                        print(f"      - {ip}")
                else:
                    print(f"   ⚠️  Could not resolve")
                print()

        # Handle multiple destinations
        elif "destinations" in rule:
            print(f"📋 Rule {idx + 1}: {description}")
            print(f"   Protocol: {protocol}")
            print(f"   Multiple destinations:")

            for destination in rule["destinations"]:
                if is_hostname(destination):
                    print(f"   - Hostname: {destination}")
                    ips = resolve_hostname(destination)
                    if ips:
                        print(f"     Resolved to {len(ips)} IP(s):")
                        for ip in sorted(ips):
                            print(f"        - {ip}")
                    else:
                        print(f"     ⚠️  Could not resolve")
                else:
                    print(f"   - IP/CIDR: {destination} (no resolution needed)")
            print()


def resolve_hostnames_cli(hostnames: List[str]) -> None:
    """
    Resolve hostnames from command line arguments.

    Args:
        hostnames: List of hostnames to resolve
    """
    for hostname in hostnames:
        print(f"🔍 Resolving: {hostname}")
        ips = resolve_hostname(hostname)

        if ips:
            print(f"✅ Found {len(ips)} IP address(es):")
            for ip in sorted(ips):
                print(f"   - {ip}")

            # Generate YAML snippet for easy copy-paste
            print("\n📝 YAML snippet for egress-allowlist.yaml:")
            print("   prefix_ranges:")
            for ip in sorted(ips):
                print(f'     - address_prefix: "{ip}"')
                print(f"       prefix_len: 32")
        else:
            print(f"❌ Could not resolve {hostname}")

        print()


def main():
    parser = argparse.ArgumentParser(
        description="Resolve hostnames to IP addresses for egress configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Resolve specific hostnames
  python resolve-hostnames.py internal-redis.net.intra kafka.internal.corp

  # Resolve all hostnames from egress-allowlist.yaml
  python resolve-hostnames.py --file egress-allowlist.yaml

  # Check if a load balancer returns multiple IPs
  python resolve-hostnames.py api.example.com
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("hostnames", nargs="*", help="Hostname(s) to resolve")
    group.add_argument(
        "--file",
        "-f",
        metavar="PATH",
        help="Path to egress-allowlist.yaml to resolve all hostnames",
    )

    args = parser.parse_args()

    if args.file:
        resolve_from_file(args.file)
    elif args.hostnames:
        resolve_hostnames_cli(args.hostnames)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
