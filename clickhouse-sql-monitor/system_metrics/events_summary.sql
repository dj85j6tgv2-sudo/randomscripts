-- events_summary.sql
-- Cumulative event counters from system.events since last server restart.
-- These accumulate monotonically — useful for comparing across runs or nodes.
SELECT
    event,
    value,
    description
FROM system.events
WHERE event IN (
    'Query',
    'SelectQuery',
    'InsertQuery',
    'InsertedRows',
    'InsertedBytes',
    'MergedRows',
    'MergedUncompressedBytes',
    'FileOpen',
    'ReadBufferFromFileDescriptorRead',
    'ContextLock',
    'RealTimeMicroseconds',
    'UserTimeMicroseconds',
    'SystemTimeMicroseconds'
)
ORDER BY event;
