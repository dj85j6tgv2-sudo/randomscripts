-- node_status.sql
-- All nodes in the cluster: confirms each is alive, shows uptime and version.
-- Uses clusterAllReplicas so a missing node shows as a gap in results.
SELECT
    hostName()                          AS hostname,
    uptime()                            AS uptime_seconds,
    formatReadableTimeDelta(uptime())   AS uptime_human,
    version()                           AS clickhouse_version
FROM clusterAllReplicas({cluster:String}, system.one)
ORDER BY hostname;
