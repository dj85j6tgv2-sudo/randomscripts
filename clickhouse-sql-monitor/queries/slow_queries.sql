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
