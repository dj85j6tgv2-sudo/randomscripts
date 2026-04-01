#!/usr/bin/env bash
# ClickHouse connection config — all values overridable via environment variables.
# Usage: source this file, then call run_query <path/to/file.sql>

CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-localhost}"
CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-9000}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}"  # Empty = passwordless auth
CLICKHOUSE_CLUSTER="${CLICKHOUSE_CLUSTER:-default}"

# LOOKBACK_HOURS controls how far back time-windowed queries look.
# Default: 24 (last 24 hours). Override: LOOKBACK_HOURS=168 (7 days), 720 (30 days)
LOOKBACK_HOURS="${LOOKBACK_HOURS:-24}"
[[ ! $LOOKBACK_HOURS =~ ^[0-9]+$ ]] && { echo "Error: LOOKBACK_HOURS must be a positive integer" >&2; exit 1; }

run_query() {
    local sql_file="$1"
    [[ ! -f "$sql_file" ]] && { echo "Error: SQL file not found: $sql_file" >&2; return 1; }
    clickhouse-client \
        --host "$CLICKHOUSE_HOST" \
        --port "$CLICKHOUSE_PORT" \
        --user "$CLICKHOUSE_USER" \
        --password "$CLICKHOUSE_PASSWORD" \
        --param_lookback_hours="$LOOKBACK_HOURS" \
        --param_cluster="$CLICKHOUSE_CLUSTER" \
        --multiquery \
        --format PrettyCompact \
        --queries-file "$sql_file"
}
