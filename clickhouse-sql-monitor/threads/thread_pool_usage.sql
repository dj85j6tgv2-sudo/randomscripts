-- thread_pool_usage.sql
-- Background thread pool utilization: active tasks vs configured pool size.
-- High saturation (>80%) means new background work will be queued and delayed.
WITH pool_metrics AS (
    SELECT metric, value
    FROM system.metrics
    WHERE metric IN (
        'BackgroundMergesAndMutationsPoolTask',
        'BackgroundFetchesPoolTask',
        'BackgroundCommonPoolTask',
        'BackgroundMovePoolTask',
        'BackgroundSchedulePoolTask'
    )
),
pool_limits AS (
    SELECT name, toUInt64(value) AS max_size
    FROM system.server_settings
    WHERE name IN (
        'background_pool_size',
        'background_fetches_pool_size',
        'background_common_pool_size',
        'background_move_pool_size',
        'background_schedule_pool_size'
    )
)
SELECT
    hostName()                          AS hostname,
    m.metric                            AS pool_metric,
    m.value                             AS active_tasks,
    l.max_size                          AS pool_max,
    round(m.value * 100.0 / greatest(l.max_size, 1), 1)    AS utilization_pct,
    CASE
        WHEN m.value * 100.0 / greatest(l.max_size, 1) >= 95 THEN 'CRITICAL - Pool exhausted (>=95%)'
        WHEN m.value * 100.0 / greatest(l.max_size, 1) >= 80 THEN 'WARNING  - Pool near capacity (>=80%)'
        WHEN m.value * 100.0 / greatest(l.max_size, 1) >= 60 THEN 'CAUTION  - Pool moderately loaded (>=60%)'
        ELSE                                                       'OK       - Pool has headroom'
    END AS pool_status
    -- ALERT: BackgroundMergesAndMutationsPoolTask >= 95% → merges are queuing
    -- ACTION: Increase background_pool_size in config.xml (or users.xml):
    --         <background_pool_size>16</background_pool_size>
    --         Default is 16; double only after confirming CPU headroom exists
    -- ALERT: BackgroundFetchesPoolTask >= 95% → replica catch-up is blocked
    -- ACTION: Increase background_fetches_pool_size (default 8)
    -- DOCS: https://clickhouse.com/docs/en/operations/server-configuration-parameters/settings#background_pool_size
FROM clusterAllReplicas({cluster:String}, pool_metrics) m
LEFT JOIN pool_limits l
    ON l.name = replaceOne(replaceOne(m.metric,
        'BackgroundMergesAndMutationsPoolTask', 'background_pool_size'),
        'BackgroundFetchesPoolTask', 'background_fetches_pool_size')
ORDER BY utilization_pct DESC;
