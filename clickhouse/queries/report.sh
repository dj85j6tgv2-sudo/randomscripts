#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

echo ""
echo "=== QUERY PERFORMANCE (lookback: ${LOOKBACK_HOURS}h) ==="

echo ""
echo "--- Currently Running Queries ---"
run_query "$SCRIPT_DIR/running_now.sql"

echo ""
echo "--- Slowest Queries ---"
run_query "$SCRIPT_DIR/slow_queries.sql"

echo ""
echo "--- Most Memory-Intensive Query Patterns ---"
run_query "$SCRIPT_DIR/memory_heavy.sql"
