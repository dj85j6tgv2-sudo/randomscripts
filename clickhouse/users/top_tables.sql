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
