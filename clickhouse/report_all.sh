#!/usr/bin/env bash
# report_all.sh — Run the full ClickHouse cluster monitoring report.
#
# Usage:
#   bash clickhouse/report_all.sh
#   LOOKBACK_HOURS=168 bash clickhouse/report_all.sh        # last 7 days
#   LOOKBACK_HOURS=720 bash clickhouse/report_all.sh        # last 30 days
#   CLICKHOUSE_HOST=ch-node1 bash clickhouse/report_all.sh  # target specific node
#   CLICKHOUSE_PASSWORD=secret bash clickhouse/report_all.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================================"
echo " ClickHouse Cluster Report"
echo " $(date)"
echo " Host:    ${CLICKHOUSE_HOST:-localhost}:${CLICKHOUSE_PORT:-9000}"
echo " Cluster: ${CLICKHOUSE_CLUSTER:-default}"
echo " Lookback: ${LOOKBACK_HOURS:-24}h"
echo "========================================================"

bash "$SCRIPT_DIR/cluster/report.sh"
bash "$SCRIPT_DIR/disk/report.sh"
bash "$SCRIPT_DIR/queries/report.sh"
bash "$SCRIPT_DIR/users/report.sh"
bash "$SCRIPT_DIR/merges/report.sh"
bash "$SCRIPT_DIR/inserts/report.sh"
bash "$SCRIPT_DIR/system_metrics/report.sh"

echo ""
echo "========================================================"
echo " Report complete — $(date)"
echo "========================================================"
