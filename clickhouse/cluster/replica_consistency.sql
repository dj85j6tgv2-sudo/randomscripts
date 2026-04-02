-- replica_consistency.sql
-- Per-table replica health: delay, queue depth, read-only state, and active replica count.
-- Replicas falling behind or going read-only indicate replication problems.
SELECT
    hostName()              AS hostname,
    database,
    table,
    is_leader,
    is_readonly,
    is_session_expired,
    total_replicas,
    active_replicas,
    absolute_delay,
    queue_size,
    inserts_in_queue,
    merges_in_queue,
    parts_to_check,
    last_queue_update_exception,
    CASE
        WHEN is_session_expired = 1             THEN 'CRITICAL - ZooKeeper session expired for this replica'
        WHEN is_readonly = 1                    THEN 'CRITICAL - Replica is read-only (ZK issue or disk full)'
        WHEN active_replicas < total_replicas   THEN 'WARNING  - Some replicas are offline'
        WHEN absolute_delay > 300               THEN 'WARNING  - Replica is >5 min behind leader'
        WHEN absolute_delay > 60                THEN 'CAUTION  - Replica is >1 min behind leader'
        WHEN parts_to_check > 0                 THEN 'CAUTION  - Parts awaiting consistency check'
        ELSE                                         'OK       - Replica is healthy'
    END AS replica_status
    -- ALERT: is_readonly = 1 → replica cannot accept writes
    -- ACTION: Check disk space first (df -h), then:
    --         SYSTEM RESTART REPLICA <db>.<table>
    -- ALERT: is_session_expired = 1 → lost ZooKeeper coordination
    -- ACTION: SYSTEM RESTART REPLICAS  (restores ZK session for all tables)
    -- ALERT: absolute_delay > 300 → replica is far behind, reads may return stale data
    -- ACTION: Monitor queue drain with:
    --         SELECT * FROM system.replication_queue WHERE database='<db>' AND table='<table>'
    --         If stuck: SYSTEM SYNC REPLICA <db>.<table>
    -- DOCS: system.replicas — https://clickhouse.com/docs/en/operations/system-tables/replicas
FROM clusterAllReplicas({cluster:String}, system.replicas)
ORDER BY absolute_delay DESC, hostname, database, table;
