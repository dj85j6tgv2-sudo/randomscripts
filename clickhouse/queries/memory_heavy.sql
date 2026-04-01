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
