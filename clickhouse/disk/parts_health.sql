-- parts_health.sql
-- Part count and average rows per part per table.
-- Too many small parts degrades query performance and slows merges.
SELECT
    database,
    table,
    count()                             AS total_parts,
    formatReadableQuantity(sum(rows))   AS total_rows,
    round(avg(rows), 0)                 AS avg_rows_per_part,
    formatReadableSize(sum(bytes_on_disk)) AS total_size,
    CASE
        WHEN count() > 1000 THEN 'CRITICAL - Too many parts (>1000)'
        WHEN count() > 500  THEN 'WARNING  - Many parts (>500)'
        WHEN count() > 100  THEN 'CAUTION  - Getting many parts (>100)'
        ELSE                     'OK       - Reasonable part count'
    END AS parts_assessment,
    CASE
        WHEN avg(rows) < 1000   THEN 'POOR      - Very small parts'
        WHEN avg(rows) < 10000  THEN 'FAIR      - Small parts'
        WHEN avg(rows) < 100000 THEN 'GOOD      - Medium parts'
        ELSE                         'EXCELLENT - Large parts'
    END AS part_size_assessment
FROM system.parts
WHERE active = 1
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY database, table
ORDER BY total_parts DESC
LIMIT 30;
