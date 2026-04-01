#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

echo ""
echo "=== CLUSTER HEALTH ==="

echo ""
echo "--- Node Status ---"
run_query "$SCRIPT_DIR/node_status.sql"

echo ""
echo "--- Replication Queue ---"
run_query "$SCRIPT_DIR/replication_lag.sql"
