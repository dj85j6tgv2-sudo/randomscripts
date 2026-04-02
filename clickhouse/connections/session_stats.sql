-- session_stats.sql
-- Active connection counts per protocol vs configured limits, plus top connection holders.
-- Near-limit connection counts cause new clients to be rejected with "Too many simultaneous queries".
SELECT
    hostName()  AS hostname,
    m.metric    AS protocol,
    m.value     AS active_connections,
    toUInt64(s.value)   AS configured_max,
    round(m.value * 100.0 / greatest(toUInt64(s.value), 1), 1) AS utilization_pct,
    CASE
        WHEN m.value * 100.0 / greatest(toUInt64(s.value), 1) >= 90
                            THEN 'CRITICAL - Connection pool near exhaustion (>=90%)'
        WHEN m.value * 100.0 / greatest(toUInt64(s.value), 1) >= 70
                            THEN 'WARNING  - High connection usage (>=70%)'
        WHEN m.value * 100.0 / greatest(toUInt64(s.value), 1) >= 50
                            THEN 'CAUTION  - Moderate connection usage (>=50%)'
        ELSE                     'OK       - Connection usage is normal'
    END AS connection_status
    -- ALERT: utilization_pct >= 90 → new connections will be rejected
    -- ACTION (immediate): Identify clients holding idle connections:
    --         SELECT user, client_hostname, count() FROM system.processes GROUP BY 1,2 ORDER BY 3 DESC
    -- ACTION (short-term): Increase max_connections in config.xml:
    --         <max_connections>4096</max_connections>
    -- ACTION (long-term): Implement connection pooling at the application layer
    --         (PgBouncer-equivalent: chproxy or HAProxy in front of ClickHouse)
    -- DOCS: https://clickhouse.com/docs/en/operations/server-configuration-parameters/settings#max-connections
FROM clusterAllReplicas({cluster:String}, system.metrics) m
LEFT JOIN system.server_settings s ON s.name = CASE m.metric
    WHEN 'TCPConnection'           THEN 'max_connections'
    WHEN 'HTTPConnection'          THEN 'max_connections'
    WHEN 'MySQLConnection'         THEN 'mysql_port'
    WHEN 'PostgreSQLConnection'    THEN 'postgresql_port'
    WHEN 'InterserverConnection'   THEN 'interserver_http_port'
    ELSE ''
END
WHERE m.metric IN (
    'TCPConnection',
    'HTTPConnection',
    'MySQLConnection',
    'PostgreSQLConnection',
    'InterserverConnection'
)
ORDER BY utilization_pct DESC;
