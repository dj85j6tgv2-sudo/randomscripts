#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

echo ""
echo "=== MERGES & MUTATIONS ==="

echo ""
echo "--- Active Merges ---"
run_query "$SCRIPT_DIR/active_merges.sql"

echo ""
echo "--- Incomplete Mutations ---"
run_query "$SCRIPT_DIR/mutations.sql"

echo ""
echo "--- Replication Queue Depth ---"
run_query "$SCRIPT_DIR/queue_depth.sql"
