-- queue_depth.sql
-- Replication queue backlog per table per node.
-- Distinct from replication_lag in cluster/ — this focuses on merge-related entries.
SELECT
    hostName()                                                          AS hostname,
    database,
    table,
    count()                                                             AS queue_depth,
    countIf(last_exception != '')                                       AS entries_with_errors,
    max(toUnixTimestamp(now()) - toUnixTimestamp(create_time))          AS oldest_entry_age_seconds,
    anyIf(last_exception, last_exception != '')                         AS last_error
FROM clusterAllReplicas({cluster:String}, system.replication_queue)
GROUP BY hostname, database, table
ORDER BY queue_depth DESC;
