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
