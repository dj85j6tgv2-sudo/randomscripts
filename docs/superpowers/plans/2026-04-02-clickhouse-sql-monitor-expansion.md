# ClickHouse SQL Monitor Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 14 new SQL monitoring files across 4 new/extended domains, each with inline ALERT/ACTION/DOCS remediation comments.

**Architecture:** Pure SQL files, no scripts. Each file queries ClickHouse system tables and uses CASE expressions for severity labels. Remediation guidance lives in `-- ALERT / -- ACTION / -- DOCS` comment blocks inside each file. Follow the established style: header comment, purpose comment, SELECT with human-readable formatting functions, cluster-wide queries via `clusterAllReplicas()`, severity CASE blocks.

**Tech Stack:** ClickHouse SQL, `system.*` tables, `clusterAllReplicas({cluster:String}, ...)` macro.

---

## Style Conventions (match existing files)

```sql
-- filename.sql
-- One-line purpose.
-- Second line: what to look for / alert condition.
SELECT
    hostName()                              AS hostname,
    formatReadableSize(bytes)               AS size,
    CASE
        WHEN value > threshold THEN 'CRITICAL - description'
        WHEN value > lower     THEN 'WARNING  - description'
        ELSE                        'OK       - description'
    END AS status
    -- ALERT: condition that triggers this status
    -- ACTION: exact command to resolve
    -- DOCS: system table reference
FROM clusterAllReplicas({cluster:String}, system.table_name)
ORDER BY value DESC;
```

---

## File Map

| Task | File | System Table(s) |
|------|------|-----------------|
| 1 | `disk/detached_parts.sql` | `system.detached_parts` |
| 2 | `disk/broken_parts.sql` | `system.part_log` |
| 3 | `disk/ttl_progress.sql` | `system.parts`, `system.tables` |
| 4 | `cluster/zookeeper_health.sql` | `system.zookeeper_connection`, `system.metrics` |
| 5 | `cluster/replica_consistency.sql` | `system.replicas` |
| 6 | `cluster/fetch_queue.sql` | `system.replication_queue` |
| 7 | `queries/full_table_scans.sql` | `system.query_log` |
| 8 | `queries/top_query_patterns.sql` | `system.query_log` |
| 9 | `threads/thread_pool_usage.sql` | `system.metrics`, `system.server_settings` |
| 10 | `threads/distributed_sends.sql` | `system.distribution_queue` |
| 11 | `threads/background_tasks.sql` | `system.merges`, `system.mutations`, `system.replication_queue` |
| 12 | `dictionaries/status.sql` | `system.dictionaries` |
| 13 | `dictionaries/memory_usage.sql` | `system.dictionaries` |
| 14 | `connections/session_stats.sql` | `system.metrics`, `system.server_settings` |

---

## Task 1: disk/detached_parts.sql

**Files:**
- Create: `clickhouse-sql-monitor/disk/detached_parts.sql`

Parts land in `detached/` when ClickHouse cannot attach them (corrupted, mismatched schema, manual intervention). These consume disk and indicate past data integrity events.

- [ ] **Step 1: Write the file**

```sql
-- detached_parts.sql
-- Parts currently in the detached/ directory across all nodes.
-- Detached parts consume disk space and indicate past attachment failures.
SELECT
    hostName()                                  AS hostname,
    database,
    table,
    name                                        AS part_name,
    partition_id,
    disk,
    reason,
    formatReadableSize(bytes_on_disk)           AS size_on_disk,
    modification_time,
    dateDiff('day', modification_time, now())   AS age_days,
    CASE
        WHEN reason = 'broken'                          THEN 'CRITICAL - Broken part, data may be lost'
        WHEN dateDiff('day', modification_time, now()) > 30 THEN 'WARNING  - Stale detached part (>30 days)'
        WHEN dateDiff('day', modification_time, now()) > 7  THEN 'CAUTION  - Detached part older than 7 days'
        ELSE                                                 'INFO     - Recently detached, may still be needed'
    END AS status
    -- ALERT: reason = 'broken' → data integrity issue
    -- ACTION: For broken parts: verify data from replicas with
    --         SELECT * FROM system.replicas WHERE table = '<table>'
    --         Then drop if confirmed safe:
    --         ALTER TABLE <db>.<table> DROP DETACHED PART '<part_name>'
    -- ALERT: age_days > 30 → orphaned part accumulating disk waste
    -- ACTION: After confirming the part is not needed:
    --         ALTER TABLE <db>.<table> DROP DETACHED PART '<part_name>'
    -- DOCS: https://clickhouse.com/docs/en/sql-reference/statements/alter/partition#drop-detached-partitionpart
FROM clusterAllReplicas({cluster:String}, system.detached_parts)
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY age_days DESC, bytes_on_disk DESC;
```

- [ ] **Step 2: Verify columns exist in target table**

Confirm columns used: `database`, `table`, `name`, `partition_id`, `disk`, `reason`, `bytes_on_disk`, `modification_time` are all present in `system.detached_parts`. Reference: https://clickhouse.com/docs/en/operations/system-tables/detached_parts

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/disk/detached_parts.sql
git commit -m "feat(clickhouse): add disk/detached_parts monitoring query"
```

---

## Task 2: disk/broken_parts.sql

**Files:**
- Create: `clickhouse-sql-monitor/disk/broken_parts.sql`

The `system.part_log` records `BrokenPart` events when ClickHouse detects data corruption during reads or merges. This query surfaces recent incidents.

- [ ] **Step 1: Write the file**

```sql
-- broken_parts.sql
-- Recent BrokenPart events recorded in the part log across all nodes.
-- A broken part indicates data corruption; ClickHouse moves it to detached/.
SELECT
    hostName()                  AS hostname,
    event_time,
    database,
    table,
    part_name,
    partition_id,
    rows,
    formatReadableSize(size_in_bytes)   AS size,
    error,
    exception
    -- ALERT: Any row in this result = data integrity event requiring investigation
    -- ACTION (step 1): Check if table is replicated:
    --         SELECT * FROM system.replicas WHERE database = '<db>' AND table = '<table>'
    -- ACTION (step 2): If replicated, fetch healthy copy from another replica:
    --         SYSTEM SYNC REPLICA <db>.<table>
    -- ACTION (step 3): If standalone, restore from backup or DROP the broken detached part:
    --         ALTER TABLE <db>.<table> DROP DETACHED PART '<part_name>'
    -- ACTION (step 4): Run CHECK TABLE to assess remaining data health:
    --         CHECK TABLE <db>.<table>
    -- DOCS: system.part_log — https://clickhouse.com/docs/en/operations/system-tables/part_log
FROM clusterAllReplicas({cluster:String}, system.part_log)
WHERE event_type = 'BrokenPart'
  AND event_time >= now() - toIntervalDay({lookback_days:UInt32})
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY event_time DESC
LIMIT 50;
```

- [ ] **Step 2: Verify columns exist**

Confirm `event_type`, `event_time`, `database`, `table`, `part_name`, `partition_id`, `rows`, `size_in_bytes`, `error`, `exception` in `system.part_log`. Reference: https://clickhouse.com/docs/en/operations/system-tables/part_log

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/disk/broken_parts.sql
git commit -m "feat(clickhouse): add disk/broken_parts monitoring query"
```

---

## Task 3: disk/ttl_progress.sql

**Files:**
- Create: `clickhouse-sql-monitor/disk/ttl_progress.sql`

Tables with TTL rules should regularly expire old data. Stale TTL (no recent deletions despite old data present) indicates the background TTL process is stuck or misconfigured.

- [ ] **Step 1: Write the file**

```sql
-- ttl_progress.sql
-- Tables with TTL rules: shows oldest data age and estimated TTL backlog.
-- Stale TTL means old data is not being deleted, wasting disk space.
WITH ttl_tables AS (
    SELECT
        database,
        name AS table,
        engine,
        create_table_query
    FROM clusterAllReplicas({cluster:String}, system.tables)
    WHERE engine LIKE '%MergeTree%'
      AND create_table_query LIKE '%TTL%'
      AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
),
part_ages AS (
    SELECT
        database,
        table,
        min(modification_time)                  AS oldest_part_modified,
        max(modification_time)                  AS newest_part_modified,
        count()                                 AS total_active_parts,
        formatReadableSize(sum(bytes_on_disk))  AS total_size,
        dateDiff('day', min(modification_time), now()) AS oldest_part_age_days
    FROM clusterAllReplicas({cluster:String}, system.parts)
    WHERE active = 1
    GROUP BY database, table
)
SELECT
    t.database,
    t.table,
    t.engine,
    p.total_active_parts,
    p.total_size,
    p.oldest_part_age_days,
    p.oldest_part_modified,
    p.newest_part_modified,
    CASE
        WHEN p.oldest_part_age_days > 90 THEN 'CRITICAL - TTL likely stuck, data older than 90 days'
        WHEN p.oldest_part_age_days > 30 THEN 'WARNING  - Old data present, verify TTL is running'
        WHEN p.oldest_part_age_days > 7  THEN 'CAUTION  - Data older than 7 days, monitor TTL progress'
        ELSE                                  'OK       - TTL appears active'
    END AS ttl_status
    -- ALERT: oldest_part_age_days > threshold on a table with TTL = TTL not keeping up
    -- ACTION (diagnose): Check active TTL merges:
    --         SELECT * FROM system.merges WHERE is_mutation = 0 AND merge_type = 'TTL_DELETE'
    -- ACTION (force): Trigger TTL deletion manually:
    --         OPTIMIZE TABLE <db>.<table> FINAL
    -- ACTION (check settings): Verify TTL merge is not disabled:
    --         SELECT * FROM system.merge_tree_settings WHERE name = 'ttl_only_drop_parts'
    -- DOCS: https://clickhouse.com/docs/en/engines/table-engines/mergetree-family/mergetree#table_engine-mergetree-ttl
FROM ttl_tables t
INNER JOIN part_ages p ON t.database = p.database AND t.table = p.table
ORDER BY p.oldest_part_age_days DESC;
```

- [ ] **Step 2: Verify columns exist**

- `system.tables`: `database`, `name`, `engine`, `create_table_query`
- `system.parts`: `database`, `table`, `active`, `modification_time`, `bytes_on_disk`
Reference: https://clickhouse.com/docs/en/operations/system-tables/tables

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/disk/ttl_progress.sql
git commit -m "feat(clickhouse): add disk/ttl_progress monitoring query"
```

---

## Task 4: cluster/zookeeper_health.sql

**Files:**
- Create: `clickhouse-sql-monitor/cluster/zookeeper_health.sql`

ClickHouse uses ZooKeeper (or ClickHouse Keeper) for replicated table coordination. `system.zookeeper_connection` shows live connection state; `system.metrics` provides watch/request counts.

- [ ] **Step 1: Write the file**

```sql
-- zookeeper_health.sql
-- ZooKeeper / ClickHouse Keeper connection health per node.
-- Lost or expired sessions cause replication to stall on that node.
SELECT
    hostName()              AS hostname,
    index,
    host,
    port,
    is_expired,
    keeper_api_version,
    connected_time,
    session_uptime_elapsed_seconds,
    CASE
        WHEN is_expired = 1                             THEN 'CRITICAL - Session expired, replication is stalled'
        WHEN session_uptime_elapsed_seconds < 60        THEN 'WARNING  - Session very young (<60s), may have reconnected recently'
        ELSE                                                 'OK       - Session is healthy'
    END AS zk_status
    -- ALERT: is_expired = 1 → replication to ZooKeeper is broken on this node
    -- ACTION (step 1): Check if ClickHouse can reach ZooKeeper:
    --         SELECT * FROM system.zookeeper WHERE path = '/'
    -- ACTION (step 2): Restart the ZooKeeper session:
    --         SYSTEM RESTART REPLICAS
    -- ACTION (step 3): If ZooKeeper itself is down, check ZK ensemble health externally
    --         and verify network connectivity from ClickHouse nodes
    -- ALERT: Multiple reconnects (young session_uptime) → network instability or ZK overload
    -- ACTION: Check ZK latency metrics and ClickHouse logs for ZooKeeper timeout messages
    -- DOCS: system.zookeeper_connection — https://clickhouse.com/docs/en/operations/system-tables/zookeeper_connection
FROM clusterAllReplicas({cluster:String}, system.zookeeper_connection)
ORDER BY hostname, index;
```

- [ ] **Step 2: Verify columns**

Confirm `index`, `host`, `port`, `is_expired`, `keeper_api_version`, `connected_time`, `session_uptime_elapsed_seconds` exist in `system.zookeeper_connection`. Available in ClickHouse 22.4+. Reference: https://clickhouse.com/docs/en/operations/system-tables/zookeeper_connection

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/cluster/zookeeper_health.sql
git commit -m "feat(clickhouse): add cluster/zookeeper_health monitoring query"
```

---

## Task 5: cluster/replica_consistency.sql

**Files:**
- Create: `clickhouse-sql-monitor/cluster/replica_consistency.sql`

`system.replicas` exposes per-table replica state including absolute delay, queue depth, and read-only mode. Diverged replicas or read-only replicas signal coordination failures.

- [ ] **Step 1: Write the file**

```sql
-- replica_consistency.sql
-- Per-table replica health: delay, queue depth, read-only state, and active replica count.
-- Replicas falling behind or going read-only indicate replication problems.
SELECT
    hostName()              AS hostname,
    database,
    table,
    is_leader,
    is_readonly,
    is_session_expired,
    total_replicas,
    active_replicas,
    absolute_delay,
    queue_size,
    inserts_in_queue,
    merges_in_queue,
    parts_to_check,
    last_queue_update_exception,
    CASE
        WHEN is_session_expired = 1             THEN 'CRITICAL - ZooKeeper session expired for this replica'
        WHEN is_readonly = 1                    THEN 'CRITICAL - Replica is read-only (ZK issue or disk full)'
        WHEN active_replicas < total_replicas   THEN 'WARNING  - Some replicas are offline'
        WHEN absolute_delay > 300               THEN 'WARNING  - Replica is >5 min behind leader'
        WHEN absolute_delay > 60                THEN 'CAUTION  - Replica is >1 min behind leader'
        WHEN parts_to_check > 0                 THEN 'CAUTION  - Parts awaiting consistency check'
        ELSE                                         'OK       - Replica is healthy'
    END AS replica_status
    -- ALERT: is_readonly = 1 → replica cannot accept writes
    -- ACTION: Check disk space first (df -h), then:
    --         SYSTEM RESTART REPLICA <db>.<table>
    -- ALERT: is_session_expired = 1 → lost ZooKeeper coordination
    -- ACTION: SYSTEM RESTART REPLICAS  (restores ZK session for all tables)
    -- ALERT: absolute_delay > 300 → replica is far behind, reads may return stale data
    -- ACTION: Monitor queue drain with:
    --         SELECT * FROM system.replication_queue WHERE database='<db>' AND table='<table>'
    --         If stuck: SYSTEM SYNC REPLICA <db>.<table>
    -- DOCS: system.replicas — https://clickhouse.com/docs/en/operations/system-tables/replicas
FROM clusterAllReplicas({cluster:String}, system.replicas)
ORDER BY absolute_delay DESC, hostname, database, table;
```

- [ ] **Step 2: Verify columns**

Confirm `is_leader`, `is_readonly`, `is_session_expired`, `total_replicas`, `active_replicas`, `absolute_delay`, `queue_size`, `inserts_in_queue`, `merges_in_queue`, `parts_to_check`, `last_queue_update_exception` in `system.replicas`.

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/cluster/replica_consistency.sql
git commit -m "feat(clickhouse): add cluster/replica_consistency monitoring query"
```

---

## Task 6: cluster/fetch_queue.sql

**Files:**
- Create: `clickhouse-sql-monitor/cluster/fetch_queue.sql`

The replication queue has GET_PART entries when a replica needs to fetch missing parts from another replica. A large or stuck fetch queue means a replica is behind and cannot catch up.

- [ ] **Step 1: Write the file**

```sql
-- fetch_queue.sql
-- Replication fetch queue: parts waiting to be pulled from other replicas.
-- A large or stalled fetch queue means a replica is falling behind.
SELECT
    hostName()                                                          AS hostname,
    database,
    table,
    countIf(type = 'GET_PART')                                          AS parts_to_fetch,
    countIf(type = 'GET_PART' AND is_currently_executing = 1)           AS currently_fetching,
    countIf(type = 'GET_PART' AND last_exception != '')                 AS fetch_errors,
    max(toUnixTimestamp(now()) - toUnixTimestamp(create_time))          AS oldest_fetch_age_seconds,
    anyIf(last_exception, type = 'GET_PART' AND last_exception != '')   AS last_fetch_error,
    CASE
        WHEN countIf(type = 'GET_PART' AND last_exception != '') > 0
                                                THEN 'CRITICAL - Fetch errors present, parts cannot be retrieved'
        WHEN countIf(type = 'GET_PART') > 100   THEN 'WARNING  - Large fetch backlog (>100 parts)'
        WHEN countIf(type = 'GET_PART') > 20    THEN 'CAUTION  - Fetch queue building up (>20 parts)'
        ELSE                                         'OK       - Fetch queue is healthy'
    END AS fetch_status
    -- ALERT: fetch_errors > 0 → replica cannot download parts from source replicas
    -- ACTION (check source): Verify source replicas are alive:
    --         SELECT * FROM system.replicas WHERE database='<db>' AND table='<table>'
    -- ACTION (reset): Restart fetch tasks:
    --         SYSTEM RESTART REPLICA <db>.<table>
    -- ACTION (force sync): If severely behind:
    --         SYSTEM SYNC REPLICA <db>.<table>
    -- ALERT: parts_to_fetch > 100 → replica is significantly behind
    -- ACTION: Check network bandwidth between nodes; check ZooKeeper response time
    -- DOCS: system.replication_queue — https://clickhouse.com/docs/en/operations/system-tables/replication_queue
FROM clusterAllReplicas({cluster:String}, system.replication_queue)
GROUP BY hostname, database, table
HAVING countIf(type = 'GET_PART') > 0
ORDER BY parts_to_fetch DESC, fetch_errors DESC;
```

- [ ] **Step 2: Verify columns**

Confirm `type`, `is_currently_executing`, `last_exception`, `create_time` in `system.replication_queue`. The `type` column values include `GET_PART`, `MERGE_PARTS`, `DROP_RANGE_PART`, etc.

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/cluster/fetch_queue.sql
git commit -m "feat(clickhouse): add cluster/fetch_queue monitoring query"
```

---

## Task 7: queries/full_table_scans.sql

**Files:**
- Create: `clickhouse-sql-monitor/queries/full_table_scans.sql`

Queries that read many rows but return very few likely lack index support. The heuristic: `read_rows > 1,000,000` AND `result_rows < read_rows * 0.001` (read >1000x more than returned).

- [ ] **Step 1: Write the file**

```sql
-- full_table_scans.sql
-- Recent queries that read large amounts of data relative to rows returned.
-- High read_rows with low result_rows suggests missing or unused primary key / skip indexes.
SELECT
    hostName()                              AS hostname,
    user,
    event_time,
    query_duration_ms,
    formatReadableQuantity(read_rows)       AS read_rows,
    formatReadableQuantity(result_rows)     AS result_rows,
    round(read_rows / greatest(result_rows, 1), 0)  AS read_to_result_ratio,
    formatReadableSize(read_bytes)          AS read_bytes,
    formatReadableSize(memory_usage)        AS memory_usage,
    arrayStringConcat(tables, ', ')         AS tables_accessed,
    substring(query, 1, 200)               AS query_preview
    -- ALERT: read_to_result_ratio > 1000 → reading far more data than needed
    -- ACTION (short-term): Use EXPLAIN to inspect index usage:
    --         EXPLAIN indexes = 1 <your_query>
    -- ACTION (fix): Add a WHERE clause matching the table's primary key (ORDER BY columns)
    -- ACTION (fix): Use PREWHERE instead of WHERE for large column filters
    -- ACTION (fix): Consider adding a projection or skip index for common filter patterns
    -- DOCS: https://clickhouse.com/docs/en/optimize/sparse-primary-indexes
FROM clusterAllReplicas({cluster:String}, system.query_log)
WHERE event_time >= now() - toIntervalHour({lookback_hours:UInt32})
  AND type = 'QueryFinish'
  AND read_rows > 1000000
  AND result_rows < read_rows / 1000
  AND user NOT IN ('_clickhouse_system', 'monitoring-internal')
  AND arrayExists(db -> db NOT IN ('system', 'information_schema'), databases)
ORDER BY read_to_result_ratio DESC, read_rows DESC
LIMIT 25;
```

- [ ] **Step 2: Verify columns**

Confirm `read_rows`, `result_rows`, `read_bytes`, `memory_usage`, `tables`, `databases`, `query` in `system.query_log`. Note: `databases` is an Array(String); `tables` is an Array(String).

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/queries/full_table_scans.sql
git commit -m "feat(clickhouse): add queries/full_table_scans monitoring query"
```

---

## Task 8: queries/top_query_patterns.sql

**Files:**
- Create: `clickhouse-sql-monitor/queries/top_query_patterns.sql`

Groups queries by `normalized_query_hash` to surface the costliest repeated patterns — best targets for optimization, caching, or materialized views.

- [ ] **Step 1: Write the file**

```sql
-- top_query_patterns.sql
-- Top query patterns by total CPU time, grouped by normalized query hash.
-- Use this to find repeated expensive queries that are good candidates for optimization.
SELECT
    normalized_query_hash,
    count()                                         AS call_count,
    any(user)                                       AS sample_user,
    round(avg(query_duration_ms), 0)                AS avg_duration_ms,
    max(query_duration_ms)                          AS max_duration_ms,
    sum(query_duration_ms)                          AS total_duration_ms,
    formatReadableQuantity(avg(read_rows))          AS avg_read_rows,
    formatReadableSize(avg(memory_usage))           AS avg_memory,
    arrayStringConcat(
        arrayDistinct(flatten(groupArray(tables))), ', '
    )                                               AS all_tables,
    substring(any(query), 1, 200)                  AS sample_query
    -- ALERT: total_duration_ms is very high for a single pattern = systemic cost
    -- ACTION (diagnose): Run with EXPLAIN indexes=1 to check index usage:
    --         EXPLAIN indexes = 1 <sample_query>
    -- ACTION (cache): If result changes rarely, consider a materialized view or
    --         a scheduled POPULATE into a summary table
    -- ACTION (index): If filtering on non-key columns, add a skip index:
    --         ALTER TABLE <t> ADD INDEX ix_col (col) TYPE minmax GRANULARITY 4
    -- DOCS: https://clickhouse.com/docs/en/sql-reference/statements/explain
FROM clusterAllReplicas({cluster:String}, system.query_log)
WHERE event_time >= now() - toIntervalHour({lookback_hours:UInt32})
  AND type = 'QueryFinish'
  AND user NOT IN ('_clickhouse_system', 'monitoring-internal')
GROUP BY normalized_query_hash
ORDER BY total_duration_ms DESC
LIMIT 20;
```

- [ ] **Step 2: Verify columns**

Confirm `normalized_query_hash`, `query_duration_ms`, `read_rows`, `memory_usage`, `tables`, `query`, `user` in `system.query_log`. `normalized_query_hash` available in ClickHouse 20.6+.

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/queries/top_query_patterns.sql
git commit -m "feat(clickhouse): add queries/top_query_patterns monitoring query"
```

---

## Task 9: threads/thread_pool_usage.sql

**Files:**
- Create: `clickhouse-sql-monitor/threads/thread_pool_usage.sql`

Pool saturation (active tasks / pool max) predicts merge/fetch starvation. Cross-joins `system.metrics` (active tasks) with `system.server_settings` (pool size limits).

- [ ] **Step 1: Write the file**

```sql
-- thread_pool_usage.sql
-- Background thread pool utilization: active tasks vs configured pool size.
-- High saturation (>80%) means new background work will be queued and delayed.
WITH pool_metrics AS (
    SELECT metric, value
    FROM system.metrics
    WHERE metric IN (
        'BackgroundMergesAndMutationsPoolTask',
        'BackgroundFetchesPoolTask',
        'BackgroundCommonPoolTask',
        'BackgroundMovePoolTask',
        'BackgroundSchedulePoolTask'
    )
),
pool_limits AS (
    SELECT name, toUInt64(value) AS max_size
    FROM system.server_settings
    WHERE name IN (
        'background_pool_size',
        'background_fetches_pool_size',
        'background_common_pool_size',
        'background_move_pool_size',
        'background_schedule_pool_size'
    )
)
SELECT
    hostName()                          AS hostname,
    m.metric                            AS pool_metric,
    m.value                             AS active_tasks,
    l.max_size                          AS pool_max,
    round(m.value * 100.0 / greatest(l.max_size, 1), 1)    AS utilization_pct,
    CASE
        WHEN m.value * 100.0 / greatest(l.max_size, 1) >= 95 THEN 'CRITICAL - Pool exhausted (>=95%)'
        WHEN m.value * 100.0 / greatest(l.max_size, 1) >= 80 THEN 'WARNING  - Pool near capacity (>=80%)'
        WHEN m.value * 100.0 / greatest(l.max_size, 1) >= 60 THEN 'CAUTION  - Pool moderately loaded (>=60%)'
        ELSE                                                       'OK       - Pool has headroom'
    END AS pool_status
    -- ALERT: BackgroundMergesAndMutationsPoolTask >= 95% → merges are queuing
    -- ACTION: Increase background_pool_size in config.xml (or users.xml):
    --         <background_pool_size>16</background_pool_size>
    --         Default is 16; double only after confirming CPU headroom exists
    -- ALERT: BackgroundFetchesPoolTask >= 95% → replica catch-up is blocked
    -- ACTION: Increase background_fetches_pool_size (default 8)
    -- DOCS: https://clickhouse.com/docs/en/operations/server-configuration-parameters/settings#background_pool_size
FROM clusterAllReplicas({cluster:String}, pool_metrics) m
LEFT JOIN pool_limits l
    ON l.name = replaceOne(replaceOne(m.metric,
        'BackgroundMergesAndMutationsPoolTask', 'background_pool_size'),
        'BackgroundFetchesPoolTask', 'background_fetches_pool_size')
ORDER BY utilization_pct DESC;
```

- [ ] **Step 2: Verify columns**

- `system.metrics`: `metric` (String), `value` (Int64)
- `system.server_settings`: `name` (String), `value` (String)

Note: The LEFT JOIN uses a simplified name mapping. Verify the exact metric→setting name mapping in your cluster version if pool names differ.

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/threads/thread_pool_usage.sql
git commit -m "feat(clickhouse): add threads/thread_pool_usage monitoring query"
```

---

## Task 10: threads/distributed_sends.sql

**Files:**
- Create: `clickhouse-sql-monitor/threads/distributed_sends.sql`

`system.distribution_queue` shows inserts buffered for delivery to remote shards in Distributed tables. A large or error-prone queue means data is not reaching its destination shard.

- [ ] **Step 1: Write the file**

```sql
-- distributed_sends.sql
-- Distributed table send queue: data buffered for delivery to remote shards.
-- Large queues or high error counts mean data is stuck and not reaching its shard.
SELECT
    hostName()                              AS hostname,
    database,
    table,
    data_path,
    is_blocked,
    error_count,
    max_delay_to_insert,
    dateDiff('second',
        last_exception_time,
        now())                              AS seconds_since_last_error,
    rows_to_insert,
    bytes_to_insert,
    formatReadableSize(bytes_to_insert)     AS size_to_insert,
    CASE
        WHEN is_blocked = 1 AND error_count > 10    THEN 'CRITICAL - Send queue blocked with repeated errors'
        WHEN is_blocked = 1                         THEN 'WARNING  - Send queue is blocked'
        WHEN error_count > 0                        THEN 'CAUTION  - Send errors present, retrying'
        ELSE                                             'OK       - Sending normally'
    END AS send_status
    -- ALERT: is_blocked = 1 → data accumulating locally, not reaching remote shard
    -- ACTION (check): Verify destination shard is reachable:
    --         SELECT * FROM remote('<shard_host>', system.one)
    -- ACTION (flush): Force flush all pending data:
    --         SYSTEM FLUSH DISTRIBUTED <db>.<table>
    -- ACTION (reset): If permanently stuck, you can drop the queue (data loss risk!):
    --         SYSTEM DROP DISTRIBUTED SEND QUEUE <db>.<table>
    -- DOCS: system.distribution_queue — https://clickhouse.com/docs/en/operations/system-tables/distribution_queue
FROM clusterAllReplicas({cluster:String}, system.distribution_queue)
ORDER BY is_blocked DESC, error_count DESC, bytes_to_insert DESC;
```

- [ ] **Step 2: Verify columns**

Confirm `database`, `table`, `data_path`, `is_blocked`, `error_count`, `max_delay_to_insert`, `last_exception_time`, `rows_to_insert`, `bytes_to_insert` in `system.distribution_queue`. Available in ClickHouse 21.1+.

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/threads/distributed_sends.sql
git commit -m "feat(clickhouse): add threads/distributed_sends monitoring query"
```

---

## Task 11: threads/background_tasks.sql

**Files:**
- Create: `clickhouse-sql-monitor/threads/background_tasks.sql`

A unified summary of all active background work: merges, mutations, and replication fetches. The goal is a single "what is ClickHouse doing in the background right now" view.

- [ ] **Step 1: Write the file**

```sql
-- background_tasks.sql
-- Unified view of active background work: merges, mutations, and replication fetches.
-- Use this as a quick overview of background load before diving into domain-specific queries.

-- Active merges (non-mutation)
SELECT
    hostName()                                  AS hostname,
    'merge'                                     AS task_type,
    database,
    table,
    result_part_name                            AS target,
    round(elapsed, 1)                           AS elapsed_seconds,
    round(progress * 100, 1)                    AS progress_pct,
    formatReadableSize(total_size_bytes_compressed) AS size,
    ''                                          AS fail_reason
    -- ALERT: elapsed_seconds > 3600 → merge running for >1 hour, possibly stuck
    -- ACTION: Check merge progress trend; if not advancing:
    --         SYSTEM STOP MERGES <db>.<table>
    --         then SYSTEM START MERGES <db>.<table>  to re-schedule
FROM clusterAllReplicas({cluster:String}, system.merges)
WHERE is_mutation = 0

UNION ALL

-- Active mutations
SELECT
    hostName()                                  AS hostname,
    'mutation'                                  AS task_type,
    database,
    table,
    mutation_id                                 AS target,
    dateDiff('second', create_time, now())      AS elapsed_seconds,
    round((1 - parts_to_do / greatest(parts_to_do_count, 1)) * 100, 1) AS progress_pct,
    ''                                          AS size,
    latest_fail_reason                          AS fail_reason
    -- ALERT: fail_reason != '' → mutation is stuck with an error
    -- ACTION: Kill the stuck mutation:
    --         KILL MUTATION WHERE database='<db>' AND table='<table>' AND mutation_id='<id>'
    -- ACTION: Fix the root cause (e.g. invalid column type in the mutation), then re-apply
FROM clusterAllReplicas({cluster:String}, system.mutations)
WHERE is_done = 0

UNION ALL

-- Replication fetch queue depth per table (not individual entries)
SELECT
    hostName()                                  AS hostname,
    'replication_fetch'                         AS task_type,
    database,
    table,
    toString(countIf(type = 'GET_PART'))        AS target,
    max(toUnixTimestamp(now()) - toUnixTimestamp(create_time)) AS elapsed_seconds,
    0.0                                         AS progress_pct,
    ''                                          AS size,
    anyIf(last_exception, last_exception != '') AS fail_reason
    -- ALERT: fail_reason != '' → fetch errors, replica cannot get parts
    -- ACTION: SYSTEM RESTART REPLICA <db>.<table>
FROM clusterAllReplicas({cluster:String}, system.replication_queue)
WHERE type = 'GET_PART'
GROUP BY hostname, database, table
HAVING count() > 0

ORDER BY task_type, elapsed_seconds DESC;
```

- [ ] **Step 2: Verify columns**

- `system.merges`: `is_mutation`, `result_part_name`, `elapsed`, `progress`, `total_size_bytes_compressed`
- `system.mutations`: `mutation_id`, `create_time`, `parts_to_do`, `parts_to_do_count`, `latest_fail_reason`
- `system.replication_queue`: `type`, `create_time`, `last_exception`

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/threads/background_tasks.sql
git commit -m "feat(clickhouse): add threads/background_tasks monitoring query"
```

---

## Task 12: dictionaries/status.sql

**Files:**
- Create: `clickhouse-sql-monitor/dictionaries/status.sql`

`system.dictionaries` exposes load status, last successful update, and any exception messages. Failed dictionaries silently return empty results or stale data.

- [ ] **Step 1: Write the file**

```sql
-- status.sql
-- Dictionary load status, freshness, and any load errors across all nodes.
-- Failed dictionaries return stale or empty data without query errors, making them silent failures.
SELECT
    hostName()                                          AS hostname,
    database,
    name,
    status,
    origin,
    type,
    key,
    source,
    last_successful_update_time,
    dateDiff('minute', last_successful_update_time, now()) AS minutes_since_last_update,
    loading_start_time,
    last_exception,
    CASE
        WHEN status = 'FAILED'              THEN 'CRITICAL - Dictionary failed to load'
        WHEN status = 'FAILED_AND_EXPIRED'  THEN 'CRITICAL - Dictionary failed and data has expired'
        WHEN status = 'EXPIRED'             THEN 'WARNING  - Dictionary data has expired (not yet reloaded)'
        WHEN last_exception != ''           THEN 'WARNING  - Previous load had exceptions'
        WHEN minutes_since_last_update > 60 THEN 'CAUTION  - Not updated in >1 hour'
        ELSE                                     'OK       - Dictionary is current'
    END AS dict_status
    -- ALERT: status = 'FAILED' → queries using this dictionary return empty or error
    -- ACTION (reload): SYSTEM RELOAD DICTIONARY <db>.<name>
    -- ACTION (diagnose): Check last_exception for root cause (bad SQL, unreachable source, etc.)
    -- ACTION (source): Verify source connectivity (DB connection, HTTP URL, file path)
    -- ALERT: minutes_since_last_update > 60 for a frequently-updated dict → source may be slow
    -- ACTION: Check source system health; consider increasing LIFETIME MAX in dict definition
    -- DOCS: system.dictionaries — https://clickhouse.com/docs/en/operations/system-tables/dictionaries
FROM clusterAllReplicas({cluster:String}, system.dictionaries)
ORDER BY dict_status DESC, minutes_since_last_update DESC;
```

- [ ] **Step 2: Verify columns**

Confirm `database`, `name`, `status`, `origin`, `type`, `key`, `source`, `last_successful_update_time`, `loading_start_time`, `last_exception` in `system.dictionaries`.

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/dictionaries/status.sql
git commit -m "feat(clickhouse): add dictionaries/status monitoring query"
```

---

## Task 13: dictionaries/memory_usage.sql

**Files:**
- Create: `clickhouse-sql-monitor/dictionaries/memory_usage.sql`

Large dictionaries can consume significant RAM. This query identifies memory-heavy dictionaries and flags candidates for layout changes (e.g. `cache` or `ssd_cache`).

- [ ] **Step 1: Write the file**

```sql
-- memory_usage.sql
-- Memory consumption per dictionary across all nodes.
-- Large in-memory dictionaries can cause OOM; consider cache or ssd_cache layouts for large data.
SELECT
    hostName()                                  AS hostname,
    database,
    name,
    type                                        AS layout_type,
    element_count,
    formatReadableQuantity(element_count)       AS readable_element_count,
    round(load_factor * 100, 1)                 AS load_factor_pct,
    bytes_allocated,
    formatReadableSize(bytes_allocated)         AS memory_allocated,
    hit_rate,
    found_rate,
    query_count,
    CASE
        WHEN bytes_allocated > 10 * 1024 * 1024 * 1024  THEN 'CRITICAL - >10 GB in memory, consider ssd_cache layout'
        WHEN bytes_allocated > 2  * 1024 * 1024 * 1024  THEN 'WARNING  - >2 GB in memory, monitor for OOM risk'
        WHEN bytes_allocated > 500 * 1024 * 1024        THEN 'CAUTION  - >500 MB in memory'
        ELSE                                                  'OK       - Memory usage is reasonable'
    END AS memory_status,
    CASE
        WHEN hit_rate < 0.5 AND type = 'cache'  THEN 'ACTION   - Cache hit rate <50%, increase cache size or use flat layout'
        WHEN found_rate < 0.9                   THEN 'NOTE     - <90% of lookups find a key; check dictionary coverage'
        ELSE                                         'OK'
    END AS efficiency_note
    -- ALERT: memory_status CRITICAL/WARNING → this dictionary is a significant memory consumer
    -- ACTION (reduce): Switch from flat/hashed to cache layout:
    --         <layout><cache><size_in_cells>1000000</size_in_cells></cache></layout>
    -- ACTION (offload): Use ssd_cache for very large dictionaries (spills to SSD):
    --         <layout><ssd_cache><path>/var/lib/clickhouse/dict_ssd/</path></ssd_cache></layout>
    -- DOCS: https://clickhouse.com/docs/en/sql-reference/dictionaries#ways-to-store-dictionaries-in-memory
FROM clusterAllReplicas({cluster:String}, system.dictionaries)
ORDER BY bytes_allocated DESC
LIMIT 30;
```

- [ ] **Step 2: Verify columns**

Confirm `type`, `element_count`, `load_factor`, `bytes_allocated`, `hit_rate`, `found_rate`, `query_count` in `system.dictionaries`.

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/dictionaries/memory_usage.sql
git commit -m "feat(clickhouse): add dictionaries/memory_usage monitoring query"
```

---

## Task 14: connections/session_stats.sql

**Files:**
- Create: `clickhouse-sql-monitor/connections/session_stats.sql`

Connection exhaustion causes new clients to be rejected. This query shows current counts per protocol against the configured maximum and identifies clients holding many connections.

- [ ] **Step 1: Write the file**

```sql
-- session_stats.sql
-- Active connection counts per protocol vs configured limits, plus top connection holders.
-- Near-limit connection counts cause client rejections with "Too many simultaneous queries".
SELECT
    hostName()  AS hostname,
    m.metric    AS protocol,
    m.value     AS active_connections,
    toUInt64(s.value)   AS configured_max,
    round(m.value * 100.0 / greatest(toUInt64(s.value), 1), 1) AS utilization_pct,
    CASE
        WHEN m.value * 100.0 / greatest(toUInt64(s.value), 1) >= 90
                            THEN 'CRITICAL - Connection pool near exhaustion (>=90%)'
        WHEN m.value * 100.0 / greatest(toUInt64(s.value), 1) >= 70
                            THEN 'WARNING  - High connection usage (>=70%)'
        WHEN m.value * 100.0 / greatest(toUInt64(s.value), 1) >= 50
                            THEN 'CAUTION  - Moderate connection usage (>=50%)'
        ELSE                     'OK       - Connection usage is normal'
    END AS connection_status
    -- ALERT: utilization_pct >= 90 → new connections will be rejected
    -- ACTION (immediate): Identify clients holding idle connections:
    --         SELECT user, client_hostname, count() FROM system.processes GROUP BY 1,2 ORDER BY 3 DESC
    -- ACTION (short-term): Increase max_connections in config.xml:
    --         <max_connections>4096</max_connections>
    -- ACTION (long-term): Implement connection pooling at the application layer
    --         (PgBouncer-equivalent: chproxy or HAProxy in front of ClickHouse)
    -- DOCS: https://clickhouse.com/docs/en/operations/server-configuration-parameters/settings#max-connections
FROM clusterAllReplicas({cluster:String}, system.metrics) m
LEFT JOIN system.server_settings s ON s.name = CASE m.metric
    WHEN 'TCPConnection'           THEN 'max_connections'
    WHEN 'HTTPConnection'          THEN 'max_connections'
    WHEN 'MySQLConnection'         THEN 'mysql_port'
    WHEN 'PostgreSQLConnection'    THEN 'postgresql_port'
    WHEN 'InterserverConnection'   THEN 'interserver_http_port'
    ELSE ''
END
WHERE m.metric IN (
    'TCPConnection',
    'HTTPConnection',
    'MySQLConnection',
    'PostgreSQLConnection',
    'InterserverConnection'
)
ORDER BY utilization_pct DESC;
```

- [ ] **Step 2: Verify columns**

- `system.metrics`: `metric` (String), `value` (Int64) — metric names confirmed at https://clickhouse.com/docs/en/operations/system-tables/metrics
- `system.server_settings`: `name`, `value` — note MySQL/PostgreSQL/Interserver join keys return the port setting, not a connection max; those rows will have `configured_max = 0` if port not configured. This is expected behavior.

- [ ] **Step 3: Commit**

```bash
git add clickhouse-sql-monitor/connections/session_stats.sql
git commit -m "feat(clickhouse): add connections/session_stats monitoring query"
```

---

## Final Step: Verify complete file tree

After all tasks are complete, verify the full structure:

```bash
find clickhouse-sql-monitor -name "*.sql" | sort
```

Expected output (32 files total — 18 existing + 14 new):
```
clickhouse-sql-monitor/cluster/fetch_queue.sql          ← NEW
clickhouse-sql-monitor/cluster/node_status.sql
clickhouse-sql-monitor/cluster/replica_consistency.sql  ← NEW
clickhouse-sql-monitor/cluster/replication_lag.sql
clickhouse-sql-monitor/cluster/zookeeper_health.sql     ← NEW
clickhouse-sql-monitor/connections/session_stats.sql    ← NEW
clickhouse-sql-monitor/dictionaries/memory_usage.sql    ← NEW
clickhouse-sql-monitor/dictionaries/status.sql          ← NEW
clickhouse-sql-monitor/disk/broken_parts.sql            ← NEW
clickhouse-sql-monitor/disk/detached_parts.sql          ← NEW
clickhouse-sql-monitor/disk/free_space.sql
clickhouse-sql-monitor/disk/parts_health.sql
clickhouse-sql-monitor/disk/table_sizes.sql
clickhouse-sql-monitor/disk/ttl_progress.sql            ← NEW
clickhouse-sql-monitor/inserts/async_inserts.sql
clickhouse-sql-monitor/inserts/insert_rates.sql
clickhouse-sql-monitor/merges/active_merges.sql
clickhouse-sql-monitor/merges/mutations.sql
clickhouse-sql-monitor/merges/queue_depth.sql
clickhouse-sql-monitor/queries/full_table_scans.sql     ← NEW
clickhouse-sql-monitor/queries/memory_heavy.sql
clickhouse-sql-monitor/queries/running_now.sql
clickhouse-sql-monitor/queries/slow_queries.sql
clickhouse-sql-monitor/queries/top_query_patterns.sql   ← NEW
clickhouse-sql-monitor/system_metrics/current_metrics.sql
clickhouse-sql-monitor/system_metrics/events_summary.sql
clickhouse-sql-monitor/threads/background_tasks.sql     ← NEW
clickhouse-sql-monitor/threads/distributed_sends.sql    ← NEW
clickhouse-sql-monitor/threads/thread_pool_usage.sql    ← NEW
clickhouse-sql-monitor/users/activity.sql
clickhouse-sql-monitor/users/errors.sql
clickhouse-sql-monitor/users/top_tables.sql
```
