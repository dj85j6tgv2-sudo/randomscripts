-- mutations.sql
-- All incomplete mutations across the cluster.
-- latest_fail_reason being non-empty indicates a stuck mutation.
SELECT
    hostName()          AS hostname,
    database,
    table,
    mutation_id,
    command,
    create_time,
    parts_to_do,
    is_done,
    latest_fail_reason
FROM clusterAllReplicas({cluster:String}, system.mutations)
WHERE is_done = 0
ORDER BY create_time ASC;
