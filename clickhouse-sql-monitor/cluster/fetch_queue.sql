-- fetch_queue.sql
-- Replication fetch queue: parts waiting to be pulled from other replicas.
-- A large or stalled fetch queue means a replica is falling behind.
SELECT
    hostName()                                                          AS hostname,
    database,
    table,
    countIf(type = 'GET_PART')                                          AS parts_to_fetch,
    countIf(type = 'GET_PART' AND is_currently_executing = 1)           AS currently_fetching,
    countIf(type = 'GET_PART' AND last_exception != '')                 AS fetch_errors,
    max(toUnixTimestamp(now()) - toUnixTimestamp(create_time))          AS oldest_fetch_age_seconds,
    anyIf(last_exception, type = 'GET_PART' AND last_exception != '')   AS last_fetch_error,
    CASE
        WHEN countIf(type = 'GET_PART' AND last_exception != '') > 0
                                                THEN 'CRITICAL - Fetch errors present, parts cannot be retrieved'
        WHEN countIf(type = 'GET_PART') > 100   THEN 'WARNING  - Large fetch backlog (>100 parts)'
        WHEN countIf(type = 'GET_PART') > 20    THEN 'CAUTION  - Fetch queue building up (>20 parts)'
        ELSE                                         'OK       - Fetch queue is healthy'
    END AS fetch_status
    -- ALERT: fetch_errors > 0 → replica cannot download parts from source replicas
    -- ACTION (check source): Verify source replicas are alive:
    --         SELECT * FROM system.replicas WHERE database='<db>' AND table='<table>'
    -- ACTION (reset): Restart fetch tasks:
    --         SYSTEM RESTART REPLICA <db>.<table>
    -- ACTION (force sync): If severely behind:
    --         SYSTEM SYNC REPLICA <db>.<table>
    -- ALERT: parts_to_fetch > 100 → replica is significantly behind
    -- ACTION: Check network bandwidth between nodes; check ZooKeeper response time
    -- DOCS: system.replication_queue — https://clickhouse.com/docs/en/operations/system-tables/replication_queue
FROM clusterAllReplicas({cluster:String}, system.replication_queue)
GROUP BY hostname, database, table
HAVING countIf(type = 'GET_PART') > 0
ORDER BY parts_to_fetch DESC, fetch_errors DESC;
