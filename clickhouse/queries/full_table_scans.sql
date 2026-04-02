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
