-- memory_usage.sql
-- Memory consumption per dictionary across all nodes.
-- Large in-memory dictionaries can cause OOM; consider cache or ssd_cache layouts for large data.
SELECT
    hostName()                                  AS hostname,
    database,
    name,
    type                                        AS layout_type,
    element_count,
    formatReadableQuantity(element_count)       AS readable_element_count,
    round(load_factor * 100, 1)                 AS load_factor_pct,
    bytes_allocated,
    formatReadableSize(bytes_allocated)         AS memory_allocated,
    hit_rate,
    found_rate,
    query_count,
    CASE
        WHEN bytes_allocated > 10 * 1024 * 1024 * 1024  THEN 'CRITICAL - >10 GB in memory, consider ssd_cache layout'
        WHEN bytes_allocated > 2  * 1024 * 1024 * 1024  THEN 'WARNING  - >2 GB in memory, monitor for OOM risk'
        WHEN bytes_allocated > 500 * 1024 * 1024        THEN 'CAUTION  - >500 MB in memory'
        ELSE                                                  'OK       - Memory usage is reasonable'
    END AS memory_status,
    CASE
        WHEN hit_rate < 0.5 AND type = 'cache'  THEN 'ACTION   - Cache hit rate <50%, increase cache size or use flat layout'
        WHEN found_rate < 0.9                   THEN 'NOTE     - <90% of lookups find a key; check dictionary coverage'
        ELSE                                         'OK'
    END AS efficiency_note
    -- ALERT: memory_status CRITICAL/WARNING → this dictionary is a significant memory consumer
    -- ACTION (reduce): Switch from flat/hashed to cache layout:
    --         <layout><cache><size_in_cells>1000000</size_in_cells></cache></layout>
    -- ACTION (offload): Use ssd_cache for very large dictionaries (spills to SSD):
    --         <layout><ssd_cache><path>/var/lib/clickhouse/dict_ssd/</path></ssd_cache></layout>
    -- DOCS: https://clickhouse.com/docs/en/sql-reference/dictionaries#ways-to-store-dictionaries-in-memory
FROM clusterAllReplicas({cluster:String}, system.dictionaries)
ORDER BY bytes_allocated DESC
LIMIT 30;
