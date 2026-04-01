# ClickHouse Cluster Monitoring Suite — Design Spec

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Read-only DBA monitoring for a multi-node ClickHouse cluster with replication (ClickHouse Keeper)

---

## Context

A new ClickHouse cluster is being rolled out and used by many users and scripts for inserts and queries. The DBA needs comprehensive visibility into infrastructure health and user activity. The output is a library of SQL files organized by domain, with thin shell script wrappers for reporting. No alerting — read-only reporting only.

---

## Architecture

**Approach:** Domain-organized SQL files + per-domain shell scripts + a top-level runner.

- SQL files are first-class: each is a standalone query runnable in any ClickHouse client (clickhouse-client, DBeaver, Tabix, etc.)
- Shell scripts are thin wrappers that call `clickhouse-client --queries-file` — no SQL embedded in shell
- A shared `lib/common.sh` holds all connection config and the `run_query` helper
- All time-windowed queries use a `{lookback_hours:UInt32}` ClickHouse named parameter, defaulting to 24h, overridable at runtime via `LOOKBACK_HOURS` env var
- Cross-cluster queries use `clusterAllReplicas()` to aggregate across all shards and replicas

---

## Directory Structure

```
clickhouse/
├── lib/
│   └── common.sh                  # Connection config + run_query helper
├── cluster/
│   ├── node_status.sql            # All nodes alive, uptime, CH version per node
│   ├── replication_lag.sql        # Replication queue depth per table/shard, error counts
│   └── report.sh
├── disk/
│   ├── free_space.sql             # Free/used/total space per disk per node
│   ├── table_sizes.sql            # Top tables by compressed size, compression ratio
│   ├── parts_health.sql           # Part count + avg rows/part with OK/WARN/CRITICAL assessment
│   └── report.sh
├── queries/
│   ├── running_now.sql            # Live: system.processes — query, user, elapsed, memory
│   ├── slow_queries.sql           # Top 20 slowest finished queries in lookback window
│   ├── memory_heavy.sql           # Top 20 memory-consuming queries in lookback window
│   └── report.sh
├── users/
│   ├── activity.sql               # Query count, total duration, read rows per user
│   ├── errors.sql                 # Exception count per user + last error message
│   ├── top_tables.sql             # Which tables each user queries most
│   └── report.sh
├── merges/
│   ├── active_merges.sql          # Running merges: progress %, elapsed, size, is_mutation
│   ├── mutations.sql              # Active mutations: command, parts to do, fail reason
│   ├── queue_depth.sql            # Replication queue: depth per table, oldest entry age, errors
│   └── report.sh
├── inserts/
│   ├── insert_rates.sql           # Rows/bytes inserted per table in lookback window
│   ├── async_inserts.sql          # Pending async inserts: count, bytes in queue, oldest age
│   └── report.sh
├── system_metrics/
│   ├── current_metrics.sql        # Live system.metrics: connections, threads, memory, BG tasks
│   ├── events_summary.sql         # Cumulative system.events: queries, inserts, merges, cache hits
│   └── report.sh
└── report_all.sh                  # Runs all domain reports sequentially with timestamp header
```

---

## Connection Config — `lib/common.sh`

```bash
CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-localhost}"
CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-9000}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}"
CLICKHOUSE_CLUSTER="${CLICKHOUSE_CLUSTER:-default}"
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

All connection settings are env-var overridable. No credentials stored in tracked files — `CLICKHOUSE_PASSWORD` defaults to empty and is set in the shell environment before running.

---

## SQL Query Inventory

### cluster/

**`node_status.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.one)` joined with `system.metrics`
- Fields: hostname, is_alive (implicit — query returns if node responds), uptime seconds, ClickHouse version
- Purpose: Confirm all nodes are up and running the same version

**`replication_lag.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.replication_queue)`
- Fields: hostname, database, table, queue depth (count), oldest entry age (seconds), entries with errors
- Purpose: Identify replication lag or stuck entries per shard/replica

### disk/

**`free_space.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.disks)`
- Fields: hostname, disk name, type, free bytes, total bytes, used %, formatted readable sizes
- Purpose: Spot nodes running low on disk space

**`table_sizes.sql`**
- Source: `system.parts WHERE active = 1 AND database != 'system'`
- Fields: database, table, compressed size, uncompressed size, compression ratio, row count, part count
- Ordered by compressed size DESC
- Purpose: Identify largest tables for capacity planning

**`parts_health.sql`**
- Source: `system.parts WHERE active = 1`
- Fields: database, table, total parts, avg rows/part, total size MB, parts assessment (OK/CAUTION/WARNING/CRITICAL), part size assessment
- Purpose: Detect tables with too many small parts (merge health indicator)

### queries/

**`running_now.sql`**
- Source: `system.processes`
- Fields: user, elapsed seconds, memory usage, read rows, query (trimmed)
- Ordered by elapsed DESC
- Purpose: Live view of what's running right now — no time window needed

**`slow_queries.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.query_log)`
- Filter: `event_time >= now() - toIntervalHour({lookback_hours:UInt32})`, `type = 'QueryFinish'`
- Fields: hostname, user, event_time, query_duration_ms, read_rows, memory_usage, query (trimmed), tables
- Top 20 by query_duration_ms DESC
- Purpose: Find slowest queries in the lookback window

**`memory_heavy.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.query_log)`
- Filter: same time window, `type = 'QueryFinish'`, `query_kind = 'Select'`
- Fields: user, query count, total memory, normalized_query_hash, query (trimmed)
- Grouped by normalized_query_hash + user, top 20 by total memory DESC
- Purpose: Identify memory-hungry query patterns

### users/

**`activity.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.query_log)`
- Filter: time window, `type = 'QueryFinish'`
- Fields: user, query count, total duration ms, avg duration ms, total read rows, total read bytes
- Ordered by query count DESC
- Purpose: Overview of who is using the cluster and how much

**`errors.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.query_log)`
- Filter: time window, `type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')`
- Fields: user, error count, last exception message, last error time
- Ordered by error count DESC
- Purpose: Identify users hitting errors frequently

**`top_tables.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.query_log)` with `arrayJoin(tables)`
- Filter: time window, `type = 'QueryFinish'`
- Fields: user, table, query count
- Ordered by user, query count DESC
- Purpose: Know which tables each user is hitting most

### merges/

**`active_merges.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.merges)`
- Fields: hostname, database, table, elapsed seconds, progress %, total compressed bytes, is_mutation
- Ordered by elapsed DESC
- Purpose: Spot stuck or long-running merges

**`mutations.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.mutations)`
- Filter: `is_done = 0`
- Fields: hostname, database, table, mutation command, parts to do, create_time, latest_fail_reason
- Purpose: Track active mutations and any failures

**`queue_depth.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.replication_queue)`
- Fields: hostname, database, table, queue depth, entries with errors, oldest entry age seconds
- Grouped by hostname/database/table
- Purpose: Replication backlog view per table

### inserts/

**`insert_rates.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.query_log)`
- Filter: time window, `query_kind = 'Insert'`, `type = 'QueryFinish'`
- Fields: table (from `arrayJoin(tables)`), insert count, total rows written, total bytes written, avg rows/insert
- Ordered by total rows DESC
- Purpose: See which tables are receiving the most data

**`async_inserts.sql`**
- Source: `clusterAllReplicas({cluster:String}, system.asynchronous_inserts)`
- Fields: hostname, database, table, pending entries count, total bytes queued, oldest entry age
- Purpose: Monitor async insert queue health

### system_metrics/

**`current_metrics.sql`**
- Source: `system.metrics`
- Filter: key metrics — `Query`, `TCPConnection`, `HTTPConnection`, `MemoryTracking`, `BackgroundMergesAndMutationsPoolTask`, `BackgroundFetchesPoolTask`, `ReplicatedChecks`
- Fields: metric name, value, description
- Purpose: Live system health snapshot

**`events_summary.sql`**
- Source: `system.events`
- Filter: key events — `Query`, `SelectQuery`, `InsertQuery`, `InsertedRows`, `MergedRows`, `MergedUncompressedBytes`, `FileOpen`, `ReadBufferFromFileDescriptorRead`, `ContextLock`
- Fields: event name, value, description
- Purpose: Cumulative activity counters since last server restart

---

## Usage

```bash
# Run full cluster report (default: last 24h)
bash clickhouse/report_all.sh

# Override lookback window
LOOKBACK_HOURS=168 bash clickhouse/report_all.sh      # last 7 days
LOOKBACK_HOURS=720 bash clickhouse/report_all.sh      # last 30 days

# Run a single domain
bash clickhouse/disk/report.sh
LOOKBACK_HOURS=1 bash clickhouse/queries/report.sh    # last 1h

# Run a single SQL file directly in clickhouse-client
clickhouse-client --param_lookback_hours=24 --queries-file clickhouse/queries/slow_queries.sql

# Target a specific node
CLICKHOUSE_HOST=ch-node-3 bash clickhouse/cluster/report.sh

# With password
CLICKHOUSE_PASSWORD=secret bash clickhouse/report_all.sh
```

---

## Constraints & Non-Goals

- **Read-only:** No writes, no DDL, no mutations triggered by these scripts
- **No alerting:** Threshold checks and notifications are out of scope
- **No external dependencies:** Only `clickhouse-client` required — no Python, no Grafana, no extra tooling
- **ClickHouse Keeper only:** No ZooKeeper-specific queries (`system.zookeeper` for external ZK not included)
- **Cluster name:** `CLICKHOUSE_CLUSTER` env var must match an actual cluster name in `system.clusters`
