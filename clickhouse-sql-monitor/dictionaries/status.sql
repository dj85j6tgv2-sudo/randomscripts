-- status.sql
-- Dictionary load status, freshness, and any load errors across all nodes.
-- Failed dictionaries return stale or empty data without query errors, making them silent failures.
SELECT
    hostName()                                          AS hostname,
    database,
    name,
    status,
    origin,
    type,
    key,
    source,
    last_successful_update_time,
    dateDiff('minute', last_successful_update_time, now()) AS minutes_since_last_update,
    loading_start_time,
    last_exception,
    CASE
        WHEN status = 'FAILED'              THEN 'CRITICAL - Dictionary failed to load'
        WHEN status = 'FAILED_AND_EXPIRED'  THEN 'CRITICAL - Dictionary failed and data has expired'
        WHEN status = 'EXPIRED'             THEN 'WARNING  - Dictionary data has expired (not yet reloaded)'
        WHEN last_exception != ''           THEN 'WARNING  - Previous load had exceptions'
        WHEN minutes_since_last_update > 60 THEN 'CAUTION  - Not updated in >1 hour'
        ELSE                                     'OK       - Dictionary is current'
    END AS dict_status
    -- ALERT: status = 'FAILED' → queries using this dictionary return empty or error
    -- ACTION (reload): SYSTEM RELOAD DICTIONARY <db>.<name>
    -- ACTION (diagnose): Check last_exception for root cause (bad SQL, unreachable source, etc.)
    -- ACTION (source): Verify source connectivity (DB connection, HTTP URL, file path)
    -- ALERT: minutes_since_last_update > 60 for a frequently-updated dict → source may be slow
    -- ACTION: Check source system health; consider increasing LIFETIME MAX in dict definition
    -- DOCS: system.dictionaries — https://clickhouse.com/docs/en/operations/system-tables/dictionaries
FROM clusterAllReplicas({cluster:String}, system.dictionaries)
ORDER BY dict_status DESC, minutes_since_last_update DESC;
