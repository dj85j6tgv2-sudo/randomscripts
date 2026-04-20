#!/bin/bash
# test-egress.sh
# Test script for Envoy egress proxy
#
# Usage:
#   ./test-egress.sh

set -e

PROXY_HOST="${PROXY_HOST:-localhost}"
PROXY_PORT="${PROXY_PORT:-15000}"
ADMIN_PORT="${ADMIN_PORT:-9901}"

echo "=============================================="
echo "Envoy Egress Proxy Test Suite"
echo "=============================================="
echo "Proxy: http://${PROXY_HOST}:${PROXY_PORT}"
echo "Admin: http://${PROXY_HOST}:${ADMIN_PORT}"
echo "=============================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() {
    echo -e "${GREEN}✓ PASS${NC}: $1"
}

fail() {
    echo -e "${RED}✗ FAIL${NC}: $1"
}

warn() {
    echo -e "${YELLOW}⚠ WARN${NC}: $1"
}

# Test 1: Check Envoy is running
echo "─────────────────────────────────────────────"
echo "Test 1: Envoy Health Check"
echo "─────────────────────────────────────────────"
if curl -s "http://${PROXY_HOST}:${ADMIN_PORT}/ready" | grep -q "LIVE"; then
    pass "Envoy is healthy"
else
    fail "Envoy is not responding"
    exit 1
fi
echo ""

# Test 2: Allowed HTTPS destination
echo "─────────────────────────────────────────────"
echo "Test 2: Allowed HTTPS (api.github.com:443)"
echo "─────────────────────────────────────────────"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -x "http://${PROXY_HOST}:${PROXY_PORT}" https://api.github.com/zen 2>/dev/null || echo "000")
if [ "$RESPONSE" = "200" ]; then
    pass "api.github.com:443 - HTTP $RESPONSE"
else
    fail "api.github.com:443 - HTTP $RESPONSE (expected 200)"
fi
echo ""

# Test 3: Denied HTTPS destination
echo "─────────────────────────────────────────────"
echo "Test 3: Denied HTTPS (google.com:443)"
echo "─────────────────────────────────────────────"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -x "http://${PROXY_HOST}:${PROXY_PORT}" https://google.com 2>/dev/null || echo "000")
if [ "$RESPONSE" = "403" ]; then
    pass "google.com:443 - HTTP $RESPONSE (correctly denied)"
else
    fail "google.com:443 - HTTP $RESPONSE (expected 403)"
fi
echo ""

# Test 4: Denied response body
echo "─────────────────────────────────────────────"
echo "Test 4: Denied Response Body"
echo "─────────────────────────────────────────────"
BODY=$(curl -s -x "http://${PROXY_HOST}:${PROXY_PORT}" https://google.com 2>/dev/null || echo "")
if echo "$BODY" | grep -q "egress_denied"; then
    pass "Response contains 'egress_denied'"
    echo "  Response: $BODY"
else
    fail "Response does not contain expected error message"
    echo "  Response: $BODY"
fi
echo ""

# Test 5: Allowed HTTP destination (if configured)
echo "─────────────────────────────────────────────"
echo "Test 5: Allowed HTTP (internal-api.corp.local:80)"
echo "─────────────────────────────────────────────"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -x "http://${PROXY_HOST}:${PROXY_PORT}" http://internal-api.corp.local:80/ 2>/dev/null || echo "000")
if [ "$RESPONSE" = "200" ] || [ "$RESPONSE" = "502" ]; then
    # 502 is expected if the destination doesn't exist but is allowed
    pass "internal-api.corp.local:80 - HTTP $RESPONSE (allowed, may not exist)"
else
    warn "internal-api.corp.local:80 - HTTP $RESPONSE"
fi
echo ""

# Test 6: Check Envoy stats
echo "─────────────────────────────────────────────"
echo "Test 6: Envoy Stats"
echo "─────────────────────────────────────────────"
echo "HTTP Proxy stats:"
curl -s "http://${PROXY_HOST}:${ADMIN_PORT}/stats" | grep "egress_http" | head -5
echo ""
echo "TCP Proxy stats:"
curl -s "http://${PROXY_HOST}:${ADMIN_PORT}/stats" | grep "tcp_" | head -5
echo ""

# Test 7: Check clusters
echo "─────────────────────────────────────────────"
echo "Test 7: Cluster Status"
echo "─────────────────────────────────────────────"
curl -s "http://${PROXY_HOST}:${ADMIN_PORT}/clusters" | grep -E "^(dynamic_forward_proxy|original_dst|blackhole|mtls_)" | head -15
echo ""

# Test 8: Port range config verification
echo "─────────────────────────────────────────────"
echo "Test 8: port_range uses native filter_chain_match (no RBAC)"
echo "─────────────────────────────────────────────"
CONFIG_FILE="${CONFIG_FILE:-envoy-dev.yaml}"
if [ -f "$CONFIG_FILE" ]; then
    RBAC_COUNT=$(grep -c "envoy.filters.network.rbac" "$CONFIG_FILE" 2>/dev/null || echo "0")
    PORT_RANGE_COUNT=$(grep -c "port_range:" "$CONFIG_FILE" 2>/dev/null || echo "0")
    if [ "$RBAC_COUNT" = "0" ] && [ "$PORT_RANGE_COUNT" -gt "0" ]; then
        pass "No RBAC filters found, $PORT_RANGE_COUNT port_range entries in filter_chain_match"
    elif [ "$RBAC_COUNT" != "0" ]; then
        fail "Found $RBAC_COUNT RBAC filter(s) — should use native port_range instead"
    else
        warn "No port_range rules found in config (may be expected)"
    fi
else
    warn "Config file $CONFIG_FILE not found, skipping"
fi
echo ""

# Test 9: gRPC filter chain verification
echo "─────────────────────────────────────────────"
echo "Test 9: gRPC filter chains use HTTP/2 codec"
echo "─────────────────────────────────────────────"
if [ -f "$CONFIG_FILE" ]; then
    GRPC_COUNT=$(grep -c "codec_type: HTTP2" "$CONFIG_FILE" 2>/dev/null || echo "0")
    if [ "$GRPC_COUNT" -gt "0" ]; then
        pass "Found $GRPC_COUNT gRPC filter chain(s) with HTTP2 codec"
    else
        warn "No gRPC filter chains found in config (may be expected if no gRPC rules)"
    fi
else
    warn "Config file $CONFIG_FILE not found, skipping"
fi
echo ""

# Test 10: mTLS cluster verification
echo "─────────────────────────────────────────────"
echo "Test 10: mTLS cluster configuration"
echo "─────────────────────────────────────────────"
if [ -f "$CONFIG_FILE" ]; then
    MTLS_COUNT=$(grep -c "UpstreamTlsContext" "$CONFIG_FILE" 2>/dev/null || echo "0")
    if [ "$MTLS_COUNT" -gt "0" ]; then
        pass "Found $MTLS_COUNT mTLS cluster(s) with UpstreamTlsContext"
        grep "certificate_chain:" "$CONFIG_FILE" | sed 's/^/  /'
    else
        warn "No mTLS clusters found in config (may be expected)"
    fi
else
    warn "Config file $CONFIG_FILE not found, skipping"
fi
echo ""

echo "=============================================="
echo "Test Suite Complete"
echo "=============================================="
