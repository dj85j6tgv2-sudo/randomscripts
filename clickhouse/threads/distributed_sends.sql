-- distributed_sends.sql
-- Distributed table send queue: data buffered for delivery to remote shards.
-- Large queues or high error counts mean data is stuck and not reaching its shard.
SELECT
    hostName()                              AS hostname,
    database,
    table,
    data_path,
    is_blocked,
    error_count,
    max_delay_to_insert,
    dateDiff('second',
        last_exception_time,
        now())                              AS seconds_since_last_error,
    rows_to_insert,
    bytes_to_insert,
    formatReadableSize(bytes_to_insert)     AS size_to_insert,
    CASE
        WHEN is_blocked = 1 AND error_count > 10    THEN 'CRITICAL - Send queue blocked with repeated errors'
        WHEN is_blocked = 1                         THEN 'WARNING  - Send queue is blocked'
        WHEN error_count > 0                        THEN 'CAUTION  - Send errors present, retrying'
        ELSE                                             'OK       - Sending normally'
    END AS send_status
    -- ALERT: is_blocked = 1 → data accumulating locally, not reaching remote shard
    -- ACTION (check): Verify destination shard is reachable:
    --         SELECT * FROM remote('<shard_host>', system.one)
    -- ACTION (flush): Force flush all pending data:
    --         SYSTEM FLUSH DISTRIBUTED <db>.<table>
    -- ACTION (reset): If permanently stuck, you can drop the queue (data loss risk!):
    --         SYSTEM DROP DISTRIBUTED SEND QUEUE <db>.<table>
    -- DOCS: system.distribution_queue — https://clickhouse.com/docs/en/operations/system-tables/distribution_queue
FROM clusterAllReplicas({cluster:String}, system.distribution_queue)
ORDER BY is_blocked DESC, error_count DESC, bytes_to_insert DESC;
