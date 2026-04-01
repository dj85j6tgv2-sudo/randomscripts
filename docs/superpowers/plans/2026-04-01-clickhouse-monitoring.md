# ClickHouse Cluster Monitoring Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only DBA monitoring suite for a multi-node ClickHouse cluster with replication, organized as domain-grouped SQL files with thin shell wrappers.

**Architecture:** Domain-organized directories (`cluster/`, `disk/`, `queries/`, `users/`, `merges/`, `inserts/`, `system_metrics/`) each containing focused `.sql` files and a `report.sh`. A shared `lib/common.sh` holds connection config and the `run_query` helper. A top-level `report_all.sh` runs everything. All time-windowed queries accept a `{lookback_hours:UInt32}` ClickHouse named parameter (default 24h, overridable via `LOOKBACK_HOURS` env var). Cross-cluster queries use `clusterAllReplicas({cluster:String}, ...)`.

**Tech Stack:** `clickhouse-client` (native protocol, port 9000), Bash, ClickHouse SQL named parameters (`--param_*`)

---

## File Map

| File | Responsibility |
|------|---------------|
| `clickhouse/lib/common.sh` | Connection config, `run_query` helper |
| `clickhouse/cluster/node_status.sql` | All nodes alive, uptime, CH version |
| `clickhouse/cluster/replication_lag.sql` | Replication queue depth, errors, age |
| `clickhouse/cluster/report.sh` | Cluster domain runner |
| `clickhouse/disk/free_space.sql` | Free/used/total per disk per node |
| `clickhouse/disk/table_sizes.sql` | Top tables by compressed size |
| `clickhouse/disk/parts_health.sql` | Part count + fragmentation assessment |
| `clickhouse/disk/report.sh` | Disk domain runner |
| `clickhouse/queries/running_now.sql` | Live running queries (system.processes) |
| `clickhouse/queries/slow_queries.sql` | Top 20 slowest queries in window |
| `clickhouse/queries/memory_heavy.sql` | Top 20 memory consumers in window |
| `clickhouse/queries/report.sh` | Queries domain runner |
| `clickhouse/users/activity.sql` | Query count, duration, rows per user |
| `clickhouse/users/errors.sql` | Exception count + last error per user |
| `clickhouse/users/top_tables.sql` | Most-hit tables per user |
| `clickhouse/users/report.sh` | Users domain runner |
| `clickhouse/merges/active_merges.sql` | Running merges with progress |
| `clickhouse/merges/mutations.sql` | Active (incomplete) mutations |
| `clickhouse/merges/queue_depth.sql` | Replication queue backlog per table |
| `clickhouse/merges/report.sh` | Merges domain runner |
| `clickhouse/inserts/insert_rates.sql` | Insert counts/rows/bytes per table |
| `clickhouse/inserts/async_inserts.sql` | Pending async insert queue stats |
| `clickhouse/inserts/report.sh` | Inserts domain runner |
| `clickhouse/system_metrics/current_metrics.sql` | Live system.metrics snapshot |
| `clickhouse/system_metrics/events_summary.sql` | Cumulative system.events counters |
| `clickhouse/system_metrics/report.sh` | System metrics domain runner |
| `clickhouse/report_all.sh` | Full cluster report, all domains |

---

## Task 1: Scaffold directory structure + `lib/common.sh`

**Files:**
- Create: `clickhouse/lib/common.sh`

- [ ] **Step 1: Create directory skeleton**

```bash
mkdir -p clickhouse/lib
mkdir -p clickhouse/cluster
mkdir -p clickhouse/disk
mkdir -p clickhouse/queries
mkdir -p clickhouse/users
mkdir -p clickhouse/merges
mkdir -p clickhouse/inserts
mkdir -p clickhouse/system_metrics
```

- [ ] **Step 2: Write `clickhouse/lib/common.sh`**

```bash
#!/usr/bin/env bash
# ClickHouse connection config — all values overridable via environment variables.
# Usage: source this file, then call run_query <path/to/file.sql>

CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-localhost}"
CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-9000}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}"
CLICKHOUSE_CLUSTER="${CLICKHOUSE_CLUSTER:-default}"

# LOOKBACK_HOURS controls how far back time-windowed queries look.
# Default: 24 (last 24 hours). Override: LOOKBACK_HOURS=168 (7 days), 720 (30 days)
LOOKBACK_HOURS="${LOOKBACK_HOURS:-24}"

run_query() {
    local sql_file="$1"
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
```

- [ ] **Step 3: Verify `common.sh` is valid bash**

```bash
bash -n clickhouse/lib/common.sh
```

Expected: no output (syntax OK).

- [ ] **Step 4: Commit**

```bash
git add clickhouse/lib/common.sh
git commit -m "feat(clickhouse): add lib/common.sh with connection config and run_query helper"
```

---

## Task 2: `cluster/` domain

**Files:**
- Create: `clickhouse/cluster/node_status.sql`
- Create: `clickhouse/cluster/replication_lag.sql`
- Create: `clickhouse/cluster/report.sh`

- [ ] **Step 1: Write `clickhouse/cluster/node_status.sql`**

```sql
-- node_status.sql
-- All nodes in the cluster: confirms each is alive, shows uptime and version.
-- Uses clusterAllReplicas so a missing node shows as a gap in results.
SELECT
    hostName()                          AS hostname,
    uptime()                            AS uptime_seconds,
    formatReadableTimeDelta(uptime())   AS uptime_human,
    version()                           AS clickhouse_version
FROM clusterAllReplicas({cluster:String}, system.one)
ORDER BY hostname;
```

- [ ] **Step 2: Verify `node_status.sql` runs without error**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/cluster/node_status.sql
```

Expected: a table with one row per node showing hostname, uptime, and version. No error output.

- [ ] **Step 3: Write `clickhouse/cluster/replication_lag.sql`**

```sql
-- replication_lag.sql
-- Replication queue depth per table per node.
-- High queue_depth or entries_with_errors indicates replication problems.
SELECT
    hostName()                                                              AS hostname,
    database,
    table,
    count()                                                                 AS queue_depth,
    countIf(is_currently_executing)                                         AS executing,
    countIf(last_exception != '')                                           AS entries_with_errors,
    max(toUnixTimestamp(now()) - toUnixTimestamp(create_time))              AS oldest_entry_age_seconds,
    min(create_time)                                                        AS oldest_entry_time,
    anyIf(last_exception, last_exception != '')                             AS last_error
FROM clusterAllReplicas({cluster:String}, system.replication_queue)
GROUP BY hostname, database, table
ORDER BY queue_depth DESC;
```

- [ ] **Step 4: Verify `replication_lag.sql` runs without error**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/cluster/replication_lag.sql
```

Expected: table showing queue depths (empty result is fine if no replication lag). No error.

- [ ] **Step 5: Write `clickhouse/cluster/report.sh`**

```bash
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
```

- [ ] **Step 6: Make executable and verify syntax**

```bash
chmod +x clickhouse/cluster/report.sh
bash -n clickhouse/cluster/report.sh
```

Expected: no output (syntax OK).

- [ ] **Step 7: Run the domain report end-to-end**

```bash
bash clickhouse/cluster/report.sh
```

Expected: output with `=== CLUSTER HEALTH ===` header, two sections, query results, no errors.

- [ ] **Step 8: Commit**

```bash
git add clickhouse/cluster/
git commit -m "feat(clickhouse): add cluster domain — node_status and replication_lag queries"
```

---

## Task 3: `disk/` domain

**Files:**
- Create: `clickhouse/disk/free_space.sql`
- Create: `clickhouse/disk/table_sizes.sql`
- Create: `clickhouse/disk/parts_health.sql`
- Create: `clickhouse/disk/report.sh`

- [ ] **Step 1: Write `clickhouse/disk/free_space.sql`**

```sql
-- free_space.sql
-- Disk space per disk per node. used_pct approaching 100 is critical.
SELECT
    hostName()                                                      AS hostname,
    name                                                            AS disk_name,
    type,
    formatReadableSize(free_space)                                  AS free,
    formatReadableSize(total_space)                                 AS total,
    formatReadableSize(total_space - free_space)                    AS used,
    round((1 - (free_space / total_space)) * 100, 1)               AS used_pct
FROM clusterAllReplicas({cluster:String}, system.disks)
ORDER BY hostname, used_pct DESC;
```

- [ ] **Step 2: Verify `free_space.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/disk/free_space.sql
```

Expected: rows showing disk usage per node. No error.

- [ ] **Step 3: Write `clickhouse/disk/table_sizes.sql`**

```sql
-- table_sizes.sql
-- Top 30 tables by compressed on-disk size across all active parts.
-- compression_ratio > 5 is typical for well-chosen codecs/types.
SELECT
    database,
    table,
    formatReadableSize(sum(data_compressed_bytes))      AS compressed,
    formatReadableSize(sum(data_uncompressed_bytes))    AS uncompressed,
    round(
        sum(data_uncompressed_bytes) /
        nullIf(sum(data_compressed_bytes), 0), 2
    )                                                   AS compression_ratio,
    formatReadableQuantity(sum(rows))                   AS rows,
    count()                                             AS parts
FROM system.parts
WHERE active = 1
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY database, table
ORDER BY sum(data_compressed_bytes) DESC
LIMIT 30;
```

- [ ] **Step 4: Verify `table_sizes.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/disk/table_sizes.sql
```

Expected: table of databases/tables ordered by compressed size. No error.

- [ ] **Step 5: Write `clickhouse/disk/parts_health.sql`**

```sql
-- parts_health.sql
-- Part count and average rows per part per table.
-- Too many small parts degrades query performance and slows merges.
SELECT
    database,
    table,
    count()                             AS total_parts,
    formatReadableQuantity(sum(rows))   AS total_rows,
    round(avg(rows), 0)                 AS avg_rows_per_part,
    formatReadableSize(sum(bytes_on_disk)) AS total_size,
    CASE
        WHEN count() > 1000 THEN 'CRITICAL - Too many parts (>1000)'
        WHEN count() > 500  THEN 'WARNING  - Many parts (>500)'
        WHEN count() > 100  THEN 'CAUTION  - Getting many parts (>100)'
        ELSE                     'OK       - Reasonable part count'
    END AS parts_assessment,
    CASE
        WHEN avg(rows) < 1000   THEN 'POOR      - Very small parts'
        WHEN avg(rows) < 10000  THEN 'FAIR      - Small parts'
        WHEN avg(rows) < 100000 THEN 'GOOD      - Medium parts'
        ELSE                         'EXCELLENT - Large parts'
    END AS part_size_assessment
FROM system.parts
WHERE active = 1
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY database, table
ORDER BY total_parts DESC
LIMIT 30;
```

- [ ] **Step 6: Verify `parts_health.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/disk/parts_health.sql
```

Expected: table with assessment columns. No error.

- [ ] **Step 7: Write `clickhouse/disk/report.sh`**

```bash
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
```

- [ ] **Step 8: Make executable, verify, run**

```bash
chmod +x clickhouse/disk/report.sh
bash -n clickhouse/disk/report.sh
bash clickhouse/disk/report.sh
```

Expected: three sections of output, no errors.

- [ ] **Step 9: Commit**

```bash
git add clickhouse/disk/
git commit -m "feat(clickhouse): add disk domain — free_space, table_sizes, parts_health queries"
```

---

## Task 4: `queries/` domain

**Files:**
- Create: `clickhouse/queries/running_now.sql`
- Create: `clickhouse/queries/slow_queries.sql`
- Create: `clickhouse/queries/memory_heavy.sql`
- Create: `clickhouse/queries/report.sh`

- [ ] **Step 1: Write `clickhouse/queries/running_now.sql`**

```sql
-- running_now.sql
-- Live snapshot of all currently executing queries.
-- No time window — this is a point-in-time view of system.processes.
SELECT
    user,
    round(elapsed, 1)                       AS elapsed_seconds,
    formatReadableSize(memory_usage)        AS memory,
    formatReadableQuantity(read_rows)       AS read_rows,
    formatReadableSize(read_bytes)          AS read_bytes,
    query_id,
    substring(query, 1, 120)               AS query_preview
FROM system.processes
ORDER BY elapsed DESC;
```

- [ ] **Step 2: Verify `running_now.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/queries/running_now.sql
```

Expected: table of running queries (may be empty if nothing is running). No error.

- [ ] **Step 3: Write `clickhouse/queries/slow_queries.sql`**

```sql
-- slow_queries.sql
-- Top 20 slowest finished queries in the lookback window across all cluster nodes.
-- Adjust LOOKBACK_HOURS env var to widen/narrow the window.
SELECT
    hostName()                              AS hostname,
    user,
    event_time,
    query_duration_ms,
    formatReadableQuantity(read_rows)       AS read_rows,
    formatReadableSize(memory_usage)        AS memory,
    arrayStringConcat(tables, ', ')         AS tables,
    substring(query, 1, 150)               AS query_preview
FROM clusterAllReplicas({cluster:String}, system.query_log)
WHERE event_time >= now() - toIntervalHour({lookback_hours:UInt32})
  AND type = 'QueryFinish'
  AND user NOT IN ('_clickhouse_system', 'monitoring-internal')
ORDER BY query_duration_ms DESC
LIMIT 20;
```

- [ ] **Step 4: Verify `slow_queries.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/queries/slow_queries.sql
```

Expected: up to 20 rows of slow queries. No error.

- [ ] **Step 5: Write `clickhouse/queries/memory_heavy.sql`**

```sql
-- memory_heavy.sql
-- Top 20 memory-consuming SELECT query patterns in the lookback window.
-- Grouped by normalized query hash to surface repeated expensive patterns.
SELECT
    user,
    count()                                         AS query_count,
    formatReadableSize(sum(memory_usage))           AS total_memory,
    formatReadableSize(max(memory_usage))           AS peak_memory,
    normalized_query_hash,
    substring(any(query), 1, 150)                  AS query_preview
FROM clusterAllReplicas({cluster:String}, system.query_log)
WHERE event_time >= now() - toIntervalHour({lookback_hours:UInt32})
  AND type = 'QueryFinish'
  AND query_kind = 'Select'
  AND user NOT IN ('_clickhouse_system', 'monitoring-internal')
GROUP BY user, normalized_query_hash
ORDER BY sum(memory_usage) DESC
LIMIT 20;
```

- [ ] **Step 6: Verify `memory_heavy.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/queries/memory_heavy.sql
```

Expected: up to 20 rows grouped by query pattern. No error.

- [ ] **Step 7: Write `clickhouse/queries/report.sh`**

```bash
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
```

- [ ] **Step 8: Make executable, verify, run**

```bash
chmod +x clickhouse/queries/report.sh
bash -n clickhouse/queries/report.sh
bash clickhouse/queries/report.sh
```

Expected: three sections with query results. No error.

- [ ] **Step 9: Commit**

```bash
git add clickhouse/queries/
git commit -m "feat(clickhouse): add queries domain — running_now, slow_queries, memory_heavy"
```

---

## Task 5: `users/` domain

**Files:**
- Create: `clickhouse/users/activity.sql`
- Create: `clickhouse/users/errors.sql`
- Create: `clickhouse/users/top_tables.sql`
- Create: `clickhouse/users/report.sh`

- [ ] **Step 1: Write `clickhouse/users/activity.sql`**

```sql
-- activity.sql
-- Query volume and resource consumption per user in the lookback window.
SELECT
    user,
    count()                                                         AS query_count,
    formatReadableTimeDelta(round(sum(query_duration_ms) / 1000))  AS total_duration,
    formatReadableTimeDelta(round(avg(query_duration_ms) / 1000))  AS avg_duration,
    formatReadableQuantity(sum(read_rows))                          AS total_read_rows,
    formatReadableSize(sum(read_bytes))                             AS total_read_bytes
FROM clusterAllReplicas({cluster:String}, system.query_log)
WHERE event_time >= now() - toIntervalHour({lookback_hours:UInt32})
  AND type = 'QueryFinish'
GROUP BY user
ORDER BY query_count DESC;
```

- [ ] **Step 2: Verify `activity.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/users/activity.sql
```

Expected: one row per user with counts and sizes. No error.

- [ ] **Step 3: Write `clickhouse/users/errors.sql`**

```sql
-- errors.sql
-- Exception counts per user in the lookback window.
-- High error counts may indicate bad queries, permissions issues, or schema mismatches.
SELECT
    user,
    count()                                                 AS error_count,
    countIf(type = 'ExceptionBeforeStart')                  AS errors_before_start,
    countIf(type = 'ExceptionWhileProcessing')              AS errors_while_processing,
    max(event_time)                                         AS last_error_time,
    anyLast(exception)                                      AS last_exception_message
FROM clusterAllReplicas({cluster:String}, system.query_log)
WHERE event_time >= now() - toIntervalHour({lookback_hours:UInt32})
  AND type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')
GROUP BY user
ORDER BY error_count DESC;
```

- [ ] **Step 4: Verify `errors.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/users/errors.sql
```

Expected: error counts per user (may be empty if no errors). No error from the query itself.

- [ ] **Step 5: Write `clickhouse/users/top_tables.sql`**

```sql
-- top_tables.sql
-- Which tables each user queries most in the lookback window.
-- Uses ARRAY JOIN on the tables array from query_log.
SELECT
    user,
    t                   AS table_name,
    count()             AS query_count
FROM clusterAllReplicas({cluster:String}, system.query_log)
ARRAY JOIN tables AS t
WHERE event_time >= now() - toIntervalHour({lookback_hours:UInt32})
  AND type = 'QueryFinish'
  AND t NOT LIKE 'system.%'
  AND t != ''
GROUP BY user, table_name
ORDER BY user ASC, query_count DESC
LIMIT 50;
```

- [ ] **Step 6: Verify `top_tables.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/users/top_tables.sql
```

Expected: user+table pairs with query counts. No error.

- [ ] **Step 7: Write `clickhouse/users/report.sh`**

```bash
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
```

- [ ] **Step 8: Make executable, verify, run**

```bash
chmod +x clickhouse/users/report.sh
bash -n clickhouse/users/report.sh
bash clickhouse/users/report.sh
```

Expected: three sections. No error.

- [ ] **Step 9: Commit**

```bash
git add clickhouse/users/
git commit -m "feat(clickhouse): add users domain — activity, errors, top_tables queries"
```

---

## Task 6: `merges/` domain

**Files:**
- Create: `clickhouse/merges/active_merges.sql`
- Create: `clickhouse/merges/mutations.sql`
- Create: `clickhouse/merges/queue_depth.sql`
- Create: `clickhouse/merges/report.sh`

- [ ] **Step 1: Write `clickhouse/merges/active_merges.sql`**

```sql
-- active_merges.sql
-- All currently running merges across the cluster.
-- High elapsed time on a single merge may indicate a stuck merge.
SELECT
    hostName()                                          AS hostname,
    database,
    table,
    round(elapsed, 1)                                   AS elapsed_seconds,
    round(progress * 100, 1)                            AS progress_pct,
    formatReadableSize(total_size_bytes_compressed)     AS total_compressed,
    is_mutation,
    result_part_name
FROM clusterAllReplicas({cluster:String}, system.merges)
ORDER BY elapsed DESC;
```

- [ ] **Step 2: Verify `active_merges.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/merges/active_merges.sql
```

Expected: running merges (may be empty). No error.

- [ ] **Step 3: Write `clickhouse/merges/mutations.sql`**

```sql
-- mutations.sql
-- All incomplete mutations across the cluster.
-- latest_fail_reason being non-empty indicates a stuck mutation.
SELECT
    hostName()          AS hostname,
    database,
    table,
    mutation_id,
    command,
    create_time,
    parts_to_do,
    is_done,
    latest_fail_reason
FROM clusterAllReplicas({cluster:String}, system.mutations)
WHERE is_done = 0
ORDER BY create_time ASC;
```

- [ ] **Step 4: Verify `mutations.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/merges/mutations.sql
```

Expected: incomplete mutations (may be empty). No error.

- [ ] **Step 5: Write `clickhouse/merges/queue_depth.sql`**

```sql
-- queue_depth.sql
-- Replication queue backlog per table per node.
-- Distinct from replication_lag in cluster/ — this focuses on merge-related entries.
SELECT
    hostName()                                                          AS hostname,
    database,
    table,
    count()                                                             AS queue_depth,
    countIf(last_exception != '')                                       AS entries_with_errors,
    max(toUnixTimestamp(now()) - toUnixTimestamp(create_time))          AS oldest_entry_age_seconds,
    anyIf(last_exception, last_exception != '')                         AS last_error
FROM clusterAllReplicas({cluster:String}, system.replication_queue)
GROUP BY hostname, database, table
ORDER BY queue_depth DESC;
```

- [ ] **Step 6: Verify `queue_depth.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/merges/queue_depth.sql
```

Expected: queue depths per table (may be empty). No error.

- [ ] **Step 7: Write `clickhouse/merges/report.sh`**

```bash
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
```

- [ ] **Step 8: Make executable, verify, run**

```bash
chmod +x clickhouse/merges/report.sh
bash -n clickhouse/merges/report.sh
bash clickhouse/merges/report.sh
```

Expected: three sections. No error.

- [ ] **Step 9: Commit**

```bash
git add clickhouse/merges/
git commit -m "feat(clickhouse): add merges domain — active_merges, mutations, queue_depth queries"
```

---

## Task 7: `inserts/` domain

**Files:**
- Create: `clickhouse/inserts/insert_rates.sql`
- Create: `clickhouse/inserts/async_inserts.sql`
- Create: `clickhouse/inserts/report.sh`

- [ ] **Step 1: Write `clickhouse/inserts/insert_rates.sql`**

```sql
-- insert_rates.sql
-- Insert volume per table in the lookback window across the cluster.
-- Shows which tables are receiving the most data.
SELECT
    t                                               AS table_name,
    count()                                         AS insert_count,
    formatReadableQuantity(sum(written_rows))       AS total_rows,
    formatReadableSize(sum(written_bytes))          AS total_bytes,
    formatReadableQuantity(round(avg(written_rows))) AS avg_rows_per_insert
FROM clusterAllReplicas({cluster:String}, system.query_log)
ARRAY JOIN tables AS t
WHERE event_time >= now() - toIntervalHour({lookback_hours:UInt32})
  AND type = 'QueryFinish'
  AND query_kind = 'Insert'
  AND t != ''
GROUP BY table_name
ORDER BY sum(written_rows) DESC
LIMIT 20;
```

- [ ] **Step 2: Verify `insert_rates.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/inserts/insert_rates.sql
```

Expected: insert stats per table (may be empty on idle cluster). No error.

- [ ] **Step 3: Write `clickhouse/inserts/async_inserts.sql`**

```sql
-- async_inserts.sql
-- Pending async insert queue across the cluster.
-- Large queue or old entries may indicate async insert backpressure.
SELECT
    hostName()                                                          AS hostname,
    database,
    table,
    count()                                                             AS pending_entries,
    formatReadableSize(sum(bytes))                                      AS total_bytes_queued,
    max(toUnixTimestamp(now()) - toUnixTimestamp(first_update))         AS oldest_entry_age_seconds,
    min(first_update)                                                   AS oldest_entry_time
FROM clusterAllReplicas({cluster:String}, system.asynchronous_inserts)
GROUP BY hostname, database, table
ORDER BY pending_entries DESC;
```

- [ ] **Step 4: Verify `async_inserts.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/inserts/async_inserts.sql
```

Expected: async insert queue stats (may be empty). No error.

- [ ] **Step 5: Write `clickhouse/inserts/report.sh`**

```bash
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
```

- [ ] **Step 6: Make executable, verify, run**

```bash
chmod +x clickhouse/inserts/report.sh
bash -n clickhouse/inserts/report.sh
bash clickhouse/inserts/report.sh
```

Expected: two sections. No error.

- [ ] **Step 7: Commit**

```bash
git add clickhouse/inserts/
git commit -m "feat(clickhouse): add inserts domain — insert_rates and async_inserts queries"
```

---

## Task 8: `system_metrics/` domain

**Files:**
- Create: `clickhouse/system_metrics/current_metrics.sql`
- Create: `clickhouse/system_metrics/events_summary.sql`
- Create: `clickhouse/system_metrics/report.sh`

- [ ] **Step 1: Write `clickhouse/system_metrics/current_metrics.sql`**

```sql
-- current_metrics.sql
-- Live system metrics from system.metrics (point-in-time, not accumulated).
-- These reset when the server restarts. Key ones to watch:
--   MemoryTracking: current memory allocated by all queries
--   BackgroundMergesAndMutationsPoolTask: how busy the merge pool is
--   Query: number of queries currently executing
SELECT
    metric,
    value,
    description
FROM system.metrics
WHERE metric IN (
    'Query',
    'TCPConnection',
    'HTTPConnection',
    'MemoryTracking',
    'BackgroundMergesAndMutationsPoolTask',
    'BackgroundFetchesPoolTask',
    'ReplicatedChecks',
    'PartsActive',
    'OpenFileForRead',
    'OpenFileForWrite'
)
ORDER BY metric;
```

- [ ] **Step 2: Verify `current_metrics.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/system_metrics/current_metrics.sql
```

Expected: rows for each of the named metrics. No error.

- [ ] **Step 3: Write `clickhouse/system_metrics/events_summary.sql`**

```sql
-- events_summary.sql
-- Cumulative event counters from system.events since last server restart.
-- These accumulate monotonically — useful for comparing across runs or nodes.
SELECT
    event,
    value,
    description
FROM system.events
WHERE event IN (
    'Query',
    'SelectQuery',
    'InsertQuery',
    'InsertedRows',
    'InsertedBytes',
    'MergedRows',
    'MergedUncompressedBytes',
    'FileOpen',
    'ReadBufferFromFileDescriptorRead',
    'ContextLock',
    'RealTimeMicroseconds',
    'UserTimeMicroseconds',
    'SystemTimeMicroseconds'
)
ORDER BY event;
```

- [ ] **Step 4: Verify `events_summary.sql`**

```bash
clickhouse-client \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/system_metrics/events_summary.sql
```

Expected: rows for each named event with cumulative counts. No error.

- [ ] **Step 5: Write `clickhouse/system_metrics/report.sh`**

```bash
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
```

- [ ] **Step 6: Make executable, verify, run**

```bash
chmod +x clickhouse/system_metrics/report.sh
bash -n clickhouse/system_metrics/report.sh
bash clickhouse/system_metrics/report.sh
```

Expected: two sections with metric data. No error.

- [ ] **Step 7: Commit**

```bash
git add clickhouse/system_metrics/
git commit -m "feat(clickhouse): add system_metrics domain — current_metrics and events_summary queries"
```

---

## Task 9: Top-level `report_all.sh`

**Files:**
- Create: `clickhouse/report_all.sh`

- [ ] **Step 1: Write `clickhouse/report_all.sh`**

```bash
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
```

- [ ] **Step 2: Make executable and verify syntax**

```bash
chmod +x clickhouse/report_all.sh
bash -n clickhouse/report_all.sh
```

Expected: no output (syntax OK).

- [ ] **Step 3: Run the full report (default 24h window)**

```bash
bash clickhouse/report_all.sh
```

Expected: full report with all 7 domain sections, header and footer, no errors.

- [ ] **Step 4: Test the lookback override**

```bash
LOOKBACK_HOURS=168 bash clickhouse/report_all.sh 2>&1 | head -10
```

Expected: header shows `Lookback: 168h`, queries run without error.

- [ ] **Step 5: Commit**

```bash
git add clickhouse/report_all.sh
git commit -m "feat(clickhouse): add report_all.sh — full cluster monitoring report runner"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All 7 domains implemented (cluster, disk, queries, users, merges, inserts, system_metrics). All SQL files from the file map are present. `lib/common.sh` with `run_query` and both params (`--param_lookback_hours`, `--param_cluster`) covered.
- [x] **Placeholder scan:** No TBD/TODO/etc. All steps contain actual SQL or bash code.
- [x] **Type consistency:** `{cluster:String}` and `{lookback_hours:UInt32}` used consistently across all SQL files that need them. `run_query` in `common.sh` passes both params. `SCRIPT_DIR` pattern consistent across all `report.sh` files.
- [x] **Non-time-windowed queries:** `running_now.sql`, `active_merges.sql`, `mutations.sql`, `current_metrics.sql`, `events_summary.sql` correctly do NOT use `{lookback_hours:UInt32}` — they are live/cumulative snapshots.
- [x] **`parts_health.sql` and `table_sizes.sql`:** These query the local node's `system.parts` (no `clusterAllReplicas`) — correct, as `system.parts` shows all parts on the local node for a replicated cluster when queried from a coordinator.
