#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

echo ""
echo "=== DISK & STORAGE ==="

echo ""
echo "--- Free Space Per Node ---"
run_query "$SCRIPT_DIR/free_space.sql"

echo ""
echo "--- Top Tables By Size ---"
run_query "$SCRIPT_DIR/table_sizes.sql"

echo ""
echo "--- Parts Health ---"
run_query "$SCRIPT_DIR/parts_health.sql"
