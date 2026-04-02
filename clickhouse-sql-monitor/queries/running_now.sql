-- running_now.sql
-- Live snapshot of all currently executing queries.
-- No time window — this is a point-in-time view of system.processes.
SELECT
    user,
    round(elapsed, 1)                       AS elapsed_seconds,
    formatReadableSize(memory_usage)        AS memory,
    formatReadableQuantity(read_rows)       AS read_rows,
    formatReadableSize(read_bytes)          AS read_bytes,
    query_id,
    substring(query, 1, 120)               AS query_preview
FROM system.processes
ORDER BY elapsed DESC;
