-- async_inserts.sql
-- Pending async insert queue across the cluster.
-- Large queue or old entries may indicate async insert backpressure.
SELECT
    hostName()                                                          AS hostname,
    database,
    table,
    count()                                                             AS pending_entries,
    formatReadableSize(sum(bytes))                                      AS total_bytes_queued,
    max(toUnixTimestamp(now()) - toUnixTimestamp(first_update))         AS oldest_entry_age_seconds,
    min(first_update)                                                   AS oldest_entry_time
FROM clusterAllReplicas({cluster:String}, system.asynchronous_inserts)
GROUP BY hostname, database, table
ORDER BY pending_entries DESC;
