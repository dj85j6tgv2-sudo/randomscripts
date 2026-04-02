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
