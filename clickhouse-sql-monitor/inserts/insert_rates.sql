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
