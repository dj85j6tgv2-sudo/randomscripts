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
