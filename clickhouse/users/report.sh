#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

echo ""
echo "=== USER ACTIVITY (lookback: ${LOOKBACK_HOURS}h) ==="

echo ""
echo "--- Query Activity Per User ---"
run_query "$SCRIPT_DIR/activity.sql"

echo ""
echo "--- Errors Per User ---"
run_query "$SCRIPT_DIR/errors.sql"

echo ""
echo "--- Top Tables Per User ---"
run_query "$SCRIPT_DIR/top_tables.sql"
