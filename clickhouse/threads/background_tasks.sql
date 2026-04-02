-- background_tasks.sql
-- Unified view of active background work: merges, mutations, and replication fetches.
-- Use this as a quick overview of background load before diving into domain-specific queries.

-- Active merges (non-mutation)
SELECT
    hostName()                                  AS hostname,
    'merge'                                     AS task_type,
    database,
    table,
    result_part_name                            AS target,
    round(elapsed, 1)                           AS elapsed_seconds,
    round(progress * 100, 1)                    AS progress_pct,
    formatReadableSize(total_size_bytes_compressed) AS size,
    ''                                          AS fail_reason
    -- ALERT: elapsed_seconds > 3600 → merge running for >1 hour, possibly stuck
    -- ACTION: Check merge progress trend; if not advancing:
    --         SYSTEM STOP MERGES <db>.<table>
    --         then SYSTEM START MERGES <db>.<table>  to re-schedule
FROM clusterAllReplicas({cluster:String}, system.merges)
WHERE is_mutation = 0

UNION ALL

-- Active mutations
SELECT
    hostName()                                  AS hostname,
    'mutation'                                  AS task_type,
    database,
    table,
    mutation_id                                 AS target,
    dateDiff('second', create_time, now())      AS elapsed_seconds,
    round((1 - parts_to_do / greatest(parts_to_do_count, 1)) * 100, 1) AS progress_pct,
    ''                                          AS size,
    latest_fail_reason                          AS fail_reason
    -- ALERT: fail_reason != '' → mutation is stuck with an error
    -- ACTION: Kill the stuck mutation:
    --         KILL MUTATION WHERE database='<db>' AND table='<table>' AND mutation_id='<id>'
    -- ACTION: Fix the root cause (e.g. invalid column type in the mutation), then re-apply
FROM clusterAllReplicas({cluster:String}, system.mutations)
WHERE is_done = 0

UNION ALL

-- Replication fetch queue depth per table (not individual entries)
SELECT
    hostName()                                  AS hostname,
    'replication_fetch'                         AS task_type,
    database,
    table,
    toString(countIf(type = 'GET_PART'))        AS target,
    max(toUnixTimestamp(now()) - toUnixTimestamp(create_time)) AS elapsed_seconds,
    0.0                                         AS progress_pct,
    ''                                          AS size,
    anyIf(last_exception, last_exception != '') AS fail_reason
    -- ALERT: fail_reason != '' → fetch errors, replica cannot get parts
    -- ACTION: SYSTEM RESTART REPLICA <db>.<table>
FROM clusterAllReplicas({cluster:String}, system.replication_queue)
WHERE type = 'GET_PART'
GROUP BY hostname, database, table
HAVING count() > 0

ORDER BY task_type, elapsed_seconds DESC;
