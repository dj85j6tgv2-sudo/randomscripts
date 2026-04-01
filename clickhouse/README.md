# ClickHouse Cluster Monitoring Suite

Read-only DBA monitoring for multi-node ClickHouse clusters with replication. Organized as domain-grouped SQL files with thin shell wrappers — available for both **Bash (Linux/macOS)** and **PowerShell (Windows)**.

---

## Requirements

- `clickhouse-client` installed and on your `$PATH`
- Network access to your ClickHouse cluster (native protocol, port 9000 by default)
- **Bash:** Bash 4+ (Linux / macOS)
- **PowerShell:** PowerShell 5.1+ or PowerShell 7+ (Windows)

---

## Quick Start

**Bash (Linux / macOS):**
```bash
# Run the full report with defaults (localhost, last 24h)
bash clickhouse/report_all.sh

# Run against your actual cluster
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/report_all.sh

# With authentication
CLICKHOUSE_HOST=ch-node1.internal CLICKHOUSE_USER=dba CLICKHOUSE_PASSWORD=secret bash clickhouse/report_all.sh
```

**PowerShell (Windows):**
```powershell
# Run the full report with defaults (localhost, last 24h)
.\clickhouse\report_all.ps1

# Run against your actual cluster
$env:CLICKHOUSE_HOST = "ch-node1.internal"; .\clickhouse\report_all.ps1

# With authentication
$env:CLICKHOUSE_HOST = "ch-node1.internal"
$env:CLICKHOUSE_USER = "dba"
$env:CLICKHOUSE_PASSWORD = "secret"
.\clickhouse\report_all.ps1
```

> **PowerShell execution policy:** If scripts are blocked, run once:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

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

**Bash:** `VARNAME=value bash clickhouse/report_all.sh`
**PowerShell:** `$env:VARNAME = "value"; .\clickhouse\report_all.ps1`

**Check your cluster name:**
```sql
SELECT cluster, host_name FROM system.clusters;
```

---

## Lookback Window

All time-based queries (slow queries, user activity, insert rates, errors) respect `LOOKBACK_HOURS`.

**Bash:**
```bash
LOOKBACK_HOURS=1   bash clickhouse/report_all.sh   # last 1 hour
LOOKBACK_HOURS=24  bash clickhouse/report_all.sh   # last 24 hours (default)
LOOKBACK_HOURS=168 bash clickhouse/report_all.sh   # last 7 days
LOOKBACK_HOURS=720 bash clickhouse/report_all.sh   # last 30 days
```

**PowerShell:**
```powershell
$env:LOOKBACK_HOURS = 1;   .\clickhouse\report_all.ps1   # last 1 hour
$env:LOOKBACK_HOURS = 24;  .\clickhouse\report_all.ps1   # last 24 hours (default)
$env:LOOKBACK_HOURS = 168; .\clickhouse\report_all.ps1   # last 7 days
$env:LOOKBACK_HOURS = 720; .\clickhouse\report_all.ps1   # last 30 days
```

Queries that show **live state** (currently running queries, active merges, mutations, disk space, system metrics) are always point-in-time and ignore `LOOKBACK_HOURS`.

---

## Running Individual Domains

**Bash:**
```bash
bash clickhouse/cluster/report.sh        # Node health + replication queue
bash clickhouse/disk/report.sh           # Disk space + table sizes + part health
bash clickhouse/queries/report.sh        # Running + slow + memory-heavy queries
bash clickhouse/users/report.sh          # User activity + errors + top tables
bash clickhouse/merges/report.sh         # Active merges + mutations + queue depth
bash clickhouse/inserts/report.sh        # Insert rates + async insert queue
bash clickhouse/system_metrics/report.sh # Live metrics + cumulative events

LOOKBACK_HOURS=1 CLICKHOUSE_HOST=ch-node2 bash clickhouse/queries/report.sh
```

**PowerShell:**
```powershell
.\clickhouse\cluster\report.ps1
.\clickhouse\disk\report.ps1
.\clickhouse\queries\report.ps1
.\clickhouse\users\report.ps1
.\clickhouse\merges\report.ps1
.\clickhouse\inserts\report.ps1
.\clickhouse\system_metrics\report.ps1

$env:LOOKBACK_HOURS = 1; $env:CLICKHOUSE_HOST = "ch-node2"; .\clickhouse\queries\report.ps1
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
# Bash
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/report_all.sh 2>&1 | tee /tmp/ch-report-$(date +%F).txt
```
```powershell
# PowerShell
$env:CLICKHOUSE_HOST = "ch-node1.internal"
.\clickhouse\report_all.ps1 | Tee-Object -FilePath "C:\Logs\ch-report-$(Get-Date -Format 'yyyy-MM-dd').txt"
```

**Investigate a slow period from yesterday:**
```bash
CLICKHOUSE_HOST=ch-node1.internal LOOKBACK_HOURS=48 bash clickhouse/queries/report.sh
```
```powershell
$env:CLICKHOUSE_HOST = "ch-node1.internal"; $env:LOOKBACK_HOURS = 48; .\clickhouse\queries\report.ps1
```

**Check who is hammering the cluster right now:**
```bash
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/queries/report.sh
CLICKHOUSE_HOST=ch-node1.internal LOOKBACK_HOURS=1 bash clickhouse/users/report.sh
```
```powershell
$env:CLICKHOUSE_HOST = "ch-node1.internal"; .\clickhouse\queries\report.ps1
$env:LOOKBACK_HOURS = 1; .\clickhouse\users\report.ps1
```

**Diagnose disk pressure:**
```bash
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/disk/report.sh
```
```powershell
$env:CLICKHOUSE_HOST = "ch-node1.internal"; .\clickhouse\disk\report.ps1
```

**Check replication health after a node restart:**
```bash
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/cluster/report.sh
CLICKHOUSE_HOST=ch-node1.internal bash clickhouse/merges/report.sh
```
```powershell
$env:CLICKHOUSE_HOST = "ch-node1.internal"
.\clickhouse\cluster\report.ps1
.\clickhouse\merges\report.ps1
```

**Monthly storage review:**
```bash
CLICKHOUSE_HOST=ch-node1.internal LOOKBACK_HOURS=720 bash clickhouse/inserts/report.sh
```
```powershell
$env:CLICKHOUSE_HOST = "ch-node1.internal"; $env:LOOKBACK_HOURS = 720; .\clickhouse\inserts\report.ps1
```

---

## Saving Report Output

**Bash:**
```bash
bash clickhouse/report_all.sh > /tmp/ch-report.txt 2>&1
bash clickhouse/report_all.sh > /tmp/ch-report-$(date +%F-%H%M).txt 2>&1
bash clickhouse/report_all.sh 2>&1 | tee /tmp/ch-report.txt
```

**PowerShell:**
```powershell
.\clickhouse\report_all.ps1 | Out-File "C:\Logs\ch-report.txt"
.\clickhouse\report_all.ps1 | Out-File "C:\Logs\ch-report-$(Get-Date -Format 'yyyy-MM-dd-HHmm').txt"
.\clickhouse\report_all.ps1 | Tee-Object -FilePath "C:\Logs\ch-report.txt"
```

---

## Scheduled Reports

**Linux/macOS (cron):**
```cron
# Daily report at 08:00
0 8 * * * CLICKHOUSE_HOST=ch-node1.internal CLICKHOUSE_USER=monitoring bash /opt/scripts/clickhouse/report_all.sh > /var/log/clickhouse-reports/daily-$(date +\%F).txt 2>&1
```

**Windows (Task Scheduler):**
```powershell
# Register a daily scheduled task at 08:00
$action = New-ScheduledTaskAction -Execute "pwsh.exe" `
  -Argument "-NonInteractive -File C:\scripts\clickhouse\report_all.ps1" `
  -WorkingDirectory "C:\scripts"
$trigger = New-ScheduledTaskTrigger -Daily -At "08:00"
$env_settings = @(
    [System.Environment]::SetEnvironmentVariable("CLICKHOUSE_HOST", "ch-node1.internal", "Machine")
    [System.Environment]::SetEnvironmentVariable("CLICKHOUSE_USER", "monitoring", "Machine")
)
Register-ScheduledTask -TaskName "ClickHouse Daily Report" -Action $action -Trigger $trigger
```

---

## File Structure

```
clickhouse/
├── lib/
│   ├── common.sh              # Bash: connection config, run_query helper
│   └── common.ps1             # PowerShell: connection config, Invoke-CHQuery helper
├── cluster/
│   ├── node_status.sql
│   ├── replication_lag.sql
│   ├── report.sh              # Bash runner
│   └── report.ps1             # PowerShell runner
├── disk/
│   ├── free_space.sql
│   ├── table_sizes.sql
│   ├── parts_health.sql
│   ├── report.sh
│   └── report.ps1
├── queries/
│   ├── running_now.sql
│   ├── slow_queries.sql
│   ├── memory_heavy.sql
│   ├── report.sh
│   └── report.ps1
├── users/
│   ├── activity.sql
│   ├── errors.sql
│   ├── top_tables.sql
│   ├── report.sh
│   └── report.ps1
├── merges/
│   ├── active_merges.sql
│   ├── mutations.sql
│   ├── queue_depth.sql
│   ├── report.sh
│   └── report.ps1
├── inserts/
│   ├── insert_rates.sql
│   ├── async_inserts.sql
│   ├── report.sh
│   └── report.ps1
├── system_metrics/
│   ├── current_metrics.sql
│   ├── events_summary.sql
│   ├── report.sh
│   └── report.ps1
├── report_all.sh              # Bash: full report runner
└── report_all.ps1             # PowerShell: full report runner
```
