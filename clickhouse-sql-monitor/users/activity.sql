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
