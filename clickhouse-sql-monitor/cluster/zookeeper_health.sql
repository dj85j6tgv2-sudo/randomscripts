-- zookeeper_health.sql
-- ZooKeeper / ClickHouse Keeper connection health per node.
-- Lost or expired sessions cause replication to stall on that node.
SELECT
    hostName()              AS hostname,
    index,
    host,
    port,
    is_expired,
    keeper_api_version,
    connected_time,
    session_uptime_elapsed_seconds,
    CASE
        WHEN is_expired = 1                             THEN 'CRITICAL - Session expired, replication is stalled'
        WHEN session_uptime_elapsed_seconds < 60        THEN 'WARNING  - Session very young (<60s), may have reconnected recently'
        ELSE                                                 'OK       - Session is healthy'
    END AS zk_status
    -- ALERT: is_expired = 1 → replication to ZooKeeper is broken on this node
    -- ACTION (step 1): Check if ClickHouse can reach ZooKeeper:
    --         SELECT * FROM system.zookeeper WHERE path = '/'
    -- ACTION (step 2): Restart the ZooKeeper session:
    --         SYSTEM RESTART REPLICAS
    -- ACTION (step 3): If ZooKeeper itself is down, check ZK ensemble health externally
    --         and verify network connectivity from ClickHouse nodes
    -- ALERT: Multiple reconnects (young session_uptime) → network instability or ZK overload
    -- ACTION: Check ZK latency metrics and ClickHouse logs for ZooKeeper timeout messages
    -- DOCS: system.zookeeper_connection — https://clickhouse.com/docs/en/operations/system-tables/zookeeper_connection
FROM clusterAllReplicas({cluster:String}, system.zookeeper_connection)
ORDER BY hostname, index;
