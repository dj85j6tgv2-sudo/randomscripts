-- current_metrics.sql
-- Live system metrics from system.metrics (point-in-time, not accumulated).
-- These reset when the server restarts. Key ones to watch:
--   MemoryTracking: current memory allocated by all queries
--   BackgroundMergesAndMutationsPoolTask: how busy the merge pool is
--   Query: number of queries currently executing
SELECT
    metric,
    value,
    description
FROM system.metrics
WHERE metric IN (
    'Query',
    'TCPConnection',
    'HTTPConnection',
    'MemoryTracking',
    'BackgroundMergesAndMutationsPoolTask',
    'BackgroundFetchesPoolTask',
    'ReplicatedChecks',
    'PartsActive',
    'OpenFileForRead',
    'OpenFileForWrite'
)
ORDER BY metric;
