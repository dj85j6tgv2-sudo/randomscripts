# ClickHouse Cluster Monitoring Suite

Read-only DBA monitoring for multi-node ClickHouse clusters with replication. Organized as domain-grouped SQL files with thin shell wrappers.

---

## Requirements

- `clickhouse-client` installed and on your `$PATH`
- Network access to your ClickHouse cluster (native protocol, port 9000 by default)
- Bash 4+

---

## Quick Start

```bash
# Run the full report with defaults (localhost, last 24h)
bash clickhouse/report_all.sh

# Run against your actual cluster
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/report_all.sh

# With authentication
CLICKHOUSE_HOST=ch-node1.internal CLICKHOUSE_USER=dba CLICKHOUSE_PASSWORD=secret bash clickhouse/report_all.sh
```

---

## Configuration

All settings are passed via environment variables — no files to edit.

| Variable | Default | Description |
|---|---|---|
| `CLICKHOUSE_HOST` | `localhost` | Any node in the cluster |
| `CLICKHOUSE_PORT` | `9000` | Native TCP port |
| `CLICKHOUSE_USER` | `default` | ClickHouse user |
| `CLICKHOUSE_PASSWORD` | *(empty)* | Leave unset for passwordless auth |
| `CLICKHOUSE_CLUSTER` | `default` | Cluster name as defined in `system.clusters` |
| `LOOKBACK_HOURS` | `24` | How far back time-windowed queries look |

**Check your cluster name:**
```sql
SELECT cluster, host_name FROM system.clusters;
```

---

## Lookback Window

All time-based queries (slow queries, user activity, insert rates, errors) respect `LOOKBACK_HOURS`.

```bash
LOOKBACK_HOURS=1   bash clickhouse/report_all.sh   # last 1 hour
LOOKBACK_HOURS=24  bash clickhouse/report_all.sh   # last 24 hours (default)
LOOKBACK_HOURS=168 bash clickhouse/report_all.sh   # last 7 days
LOOKBACK_HOURS=720 bash clickhouse/report_all.sh   # last 30 days
```

Queries that show **live state** (currently running queries, active merges, mutations, disk space, system metrics) are always point-in-time and ignore `LOOKBACK_HOURS`.

---

## Running Individual Domains

Run a single domain when you only need one area of visibility:

```bash
bash clickhouse/cluster/report.sh        # Node health + replication queue
bash clickhouse/disk/report.sh           # Disk space + table sizes + part health
bash clickhouse/queries/report.sh        # Running + slow + memory-heavy queries
bash clickhouse/users/report.sh          # User activity + errors + top tables
bash clickhouse/merges/report.sh         # Active merges + mutations + queue depth
bash clickhouse/inserts/report.sh        # Insert rates + async insert queue
bash clickhouse/system_metrics/report.sh # Live metrics + cumulative events
```

Environment variables apply to individual domain runs too:

```bash
LOOKBACK_HOURS=1 CLICKHOUSE_HOST=ch-node2 bash clickhouse/queries/report.sh
```

---

## Running Individual SQL Queries

Every `.sql` file is a standalone query you can run directly in any ClickHouse client (clickhouse-client, DBeaver, Tabix, etc.).

**Via clickhouse-client:**
```bash
clickhouse-client \
  --host ch-node1.internal \
  --user dba \
  --param_cluster=default \
  --param_lookback_hours=24 \
  --format PrettyCompact \
  --queries-file clickhouse/queries/slow_queries.sql
```

**Time-windowed queries** need both `--param_cluster` and `--param_lookback_hours`.

**Live/point-in-time queries** (running_now, active_merges, mutations, current_metrics, events_summary, free_space) only need `--param_cluster`.

**Local-only queries** (table_sizes, parts_health) need neither parameter.

---

## What Each Domain Reports

### `cluster/` — Cluster Health
| File | What it shows |
|---|---|
| `node_status.sql` | All nodes: alive check, uptime, ClickHouse version |
| `replication_lag.sql` | Replication queue depth per table/node, error count, oldest entry age |

### `disk/` — Disk & Storage
| File | What it shows |
|---|---|
| `free_space.sql` | Free/used/total disk space per disk per node, used % |
| `table_sizes.sql` | Top 30 tables by compressed size, compression ratio, row count |
| `parts_health.sql` | Part count per table with OK/CAUTION/WARNING/CRITICAL assessment |

### `queries/` — Query Performance
| File | What it shows |
|---|---|
| `running_now.sql` | Live snapshot of all executing queries: user, elapsed, memory, read rows |
| `slow_queries.sql` | Top 20 slowest finished queries in the lookback window |
| `memory_heavy.sql` | Top 20 most memory-consuming query patterns, grouped by hash |

### `users/` — User Activity
| File | What it shows |
|---|---|
| `activity.sql` | Query count, total/avg duration, read rows/bytes per user |
| `errors.sql` | Exception count per user, last error message |
| `top_tables.sql` | Which tables each user queries most |

### `merges/` — Merges & Mutations
| File | What it shows |
|---|---|
| `active_merges.sql` | All running merges: progress %, elapsed time, size, is_mutation |
| `mutations.sql` | All incomplete mutations: command, parts remaining, fail reason |
| `queue_depth.sql` | Replication queue backlog per table per node |

### `inserts/` — Insert Performance
| File | What it shows |
|---|---|
| `insert_rates.sql` | Rows/bytes inserted per table in the lookback window |
| `async_inserts.sql` | Pending async insert queue: count, bytes, oldest entry age |

### `system_metrics/` — System Metrics
| File | What it shows |
|---|---|
| `current_metrics.sql` | Live counters: connections, threads, memory, background tasks |
| `events_summary.sql` | Cumulative counters since restart: queries, inserts, merges, cache |

---

## Common DBA Workflows

**Morning health check:**
```bash
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/report_all.sh 2>&1 | tee /tmp/ch-report-$(date +%F).txt
```

**Investigate a slow period from yesterday:**
```bash
CLICKHOUSE_HOST=ch-node1.internal LOOKBACK_HOURS=48 bash clickhouse/queries/report.sh
```

**Check who is hammering the cluster right now:**
```bash
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/queries/report.sh
# Then check user activity for the last hour:
CLICKHOUSE_HOST=ch-node1.internal LOOKBACK_HOURS=1 bash clickhouse/users/report.sh
```

**Diagnose disk pressure:**
```bash
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/disk/report.sh
```

**Check replication health after a node restart:**
```bash
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/cluster/report.sh
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/merges/report.sh
```

**Monthly storage review:**
```bash
CLICKHOUSE_HOST=ch-node1.internal LOOKBACK_HOURS=720 bash clickhouse/inserts/report.sh
```

---

## Saving Report Output

```bash
# Save to file
bash clickhouse/report_all.sh > /tmp/ch-report.txt 2>&1

# Save with timestamp in filename
bash clickhouse/report_all.sh > /tmp/ch-report-$(date +%F-%H%M).txt 2>&1

# View while saving
bash clickhouse/report_all.sh 2>&1 | tee /tmp/ch-report.txt
```

---

## Scheduled Reports (cron)

```cron
# Daily report at 08:00, saved to /var/log/clickhouse-reports/
0 8 * * * CLICKHOUSE_HOST=ch-node1.internal CLICKHOUSE_USER=monitoring bash /opt/scripts/clickhouse/report_all.sh > /var/log/clickhouse-reports/daily-$(date +\%F).txt 2>&1
```

---

## File Structure

```
clickhouse/
├── lib/
│   └── common.sh              # Connection config, run_query helper
├── cluster/
│   ├── node_status.sql
│   ├── replication_lag.sql
│   └── report.sh
├── disk/
│   ├── free_space.sql
│   ├── table_sizes.sql
│   ├── parts_health.sql
│   └── report.sh
├── queries/
│   ├── running_now.sql
│   ├── slow_queries.sql
│   ├── memory_heavy.sql
│   └── report.sh
├── users/
│   ├── activity.sql
│   ├── errors.sql
│   ├── top_tables.sql
│   └── report.sh
├── merges/
│   ├── active_merges.sql
│   ├── mutations.sql
│   ├── queue_depth.sql
│   └── report.sh
├── inserts/
│   ├── insert_rates.sql
│   ├── async_inserts.sql
│   └── report.sh
├── system_metrics/
│   ├── current_metrics.sql
│   ├── events_summary.sql
│   └── report.sh
└── report_all.sh              # Full report runner
```
