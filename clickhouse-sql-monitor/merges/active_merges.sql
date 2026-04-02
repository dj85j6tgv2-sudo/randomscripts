-- active_merges.sql
-- All currently running merges across the cluster.
-- High elapsed time on a single merge may indicate a stuck merge.
SELECT
    hostName()                                          AS hostname,
    database,
    table,
    round(elapsed, 1)                                   AS elapsed_seconds,
    round(progress * 100, 1)                            AS progress_pct,
    formatReadableSize(total_size_bytes_compressed)     AS total_compressed,
    is_mutation,
    result_part_name
FROM clusterAllReplicas({cluster:String}, system.merges)
ORDER BY elapsed DESC;
