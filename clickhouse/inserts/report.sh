#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

echo ""
echo "=== INSERT PERFORMANCE (lookback: ${LOOKBACK_HOURS}h) ==="

echo ""
echo "--- Insert Rates Per Table ---"
run_query "$SCRIPT_DIR/insert_rates.sql"

echo ""
echo "--- Async Insert Queue ---"
run_query "$SCRIPT_DIR/async_inserts.sql"
