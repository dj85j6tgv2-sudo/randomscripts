-- table_sizes.sql
-- Top 30 tables by compressed on-disk size across all active parts.
-- compression_ratio > 5 is typical for well-chosen codecs/types.
SELECT
    database,
    table,
    formatReadableSize(sum(data_compressed_bytes))      AS compressed,
    formatReadableSize(sum(data_uncompressed_bytes))    AS uncompressed,
    round(
        sum(data_uncompressed_bytes) /
        nullIf(sum(data_compressed_bytes), 0), 2
    )                                                   AS compression_ratio,
    formatReadableQuantity(sum(rows))                   AS rows,
    count()                                             AS parts
FROM system.parts
WHERE active = 1
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY database, table
ORDER BY sum(data_compressed_bytes) DESC
LIMIT 30;
