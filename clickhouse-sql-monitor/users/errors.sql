-- errors.sql
-- Exception counts per user in the lookback window.
-- High error counts may indicate bad queries, permissions issues, or schema mismatches.
SELECT
    user,
    count()                                                 AS error_count,
    countIf(type = 'ExceptionBeforeStart')                  AS errors_before_start,
    countIf(type = 'ExceptionWhileProcessing')              AS errors_while_processing,
    max(event_time)                                         AS last_error_time,
    anyLast(exception)                                      AS last_exception_message
FROM clusterAllReplicas({cluster:String}, system.query_log)
WHERE event_time >= now() - toIntervalHour({lookback_hours:UInt32})
  AND type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')
GROUP BY user
ORDER BY error_count DESC;
