-- replication_lag.sql
-- Replication queue depth per table per node.
-- High queue_depth or entries_with_errors indicates replication problems.
SELECT
    hostName()                                                              AS hostname,
    database,
    table,
    count()                                                                 AS queue_depth,
    countIf(is_currently_executing)                                         AS executing,
    countIf(last_exception != '')                                           AS entries_with_errors,
    max(toUnixTimestamp(now()) - toUnixTimestamp(create_time))              AS oldest_entry_age_seconds,
    min(create_time)                                                        AS oldest_entry_time,
    anyIf(last_exception, last_exception != '')                             AS last_error
FROM clusterAllReplicas({cluster:String}, system.replication_queue)
GROUP BY hostname, database, table
ORDER BY queue_depth DESC;
