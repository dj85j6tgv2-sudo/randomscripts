#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

echo ""
echo "=== SYSTEM METRICS ==="

echo ""
echo "--- Current Live Metrics ---"
run_query "$SCRIPT_DIR/current_metrics.sql"

echo ""
echo "--- Cumulative Events Since Restart ---"
run_query "$SCRIPT_DIR/events_summary.sql"
