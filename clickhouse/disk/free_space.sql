-- free_space.sql
-- Disk space per disk per node. used_pct approaching 100 is critical.
SELECT
    hostName()                                                      AS hostname,
    name                                                            AS disk_name,
    type,
    formatReadableSize(free_space)                                  AS free,
    formatReadableSize(total_space)                                 AS total,
    formatReadableSize(total_space - free_space)                    AS used,
    round((1 - (free_space / total_space)) * 100, 1)               AS used_pct
FROM clusterAllReplicas({cluster:String}, system.disks)
ORDER BY hostname, used_pct DESC;
