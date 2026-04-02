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
