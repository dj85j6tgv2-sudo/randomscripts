# ClickStack Dashboard Integration — Design Spec

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Implement the ClickHouse monitoring SQL queries as HyperDX dashboards in a self-managed ClickStack deployment, stored as JSON files and provisioned via API

---

## Context

We have an existing monitoring suite (`clickhouse/`) with SQL queries organized into 7 domains (`cluster/`, `disk/`, `queries/`, `users/`, `merges/`, `inserts/`, `system_metrics/`). ClickStack (self-managed) already includes built-in preset dashboards covering:

- **Infrastructure:** CPU, memory, disk, S3 requests, network
- **Inserts:** query count by table, max active parts per partition
- **Selects:** query latency, count by table, most time-consuming patterns, slowest queries

This integration builds **complementary dashboards** covering the gaps — user-level activity, replication/merge health, parts fragmentation, and async insert queues. Nothing from the presets is duplicated.

---

## Architecture

Dashboards are defined as JSON files in `clickhouse/dashboards/`. A provisioning script posts them to the HyperDX REST API (`/api/dashboards`) — idempotent: existing dashboards are updated by name, new ones are created. The cluster name is templated as `__CLUSTER__` in SQL and substituted at provision time via `CLICKHOUSE_CLUSTER` env var.

SQL queries are adapted from the existing domain SQL files:
- `{lookback_hours:UInt32}` replaced with hardcoded `24` (HyperDX time picker handles display range)
- `{cluster:String}` replaced with `'__CLUSTER__'` placeholder, substituted at provision time
- Live queries (`system.processes`, `system.merges`, `system.mutations`) need no time filter

---

## File Structure

```
clickhouse/dashboards/
├── overview.json                  # Key stat panels across all uncovered domains
├── user_activity.json             # User queries, errors, top tables per user
├── replication_merges.json        # Queue depth, active merges, stuck mutations
├── parts_health.json              # Part count + fragmentation assessment
├── async_inserts.json             # Async insert queue + insert rates per table
├── provision.sh                   # Bash: idempotent create/update via HyperDX API
└── provision.ps1                  # PowerShell: same, for Windows
```

---

## Dashboard Inventory

### `overview.json` — ClickHouse DBA Overview

One-glance health summary. Six panels in a 2×3 grid of stat+table panels.

| Panel | Type | SQL Source |
|---|---|---|
| Top 5 Users (last 24h) | table | `users/activity.sql` adapted |
| Users With Errors | table | `users/errors.sql` adapted |
| Replication Queue Total | number | `SELECT count() FROM clusterAllReplicas('__CLUSTER__', system.replication_queue)` |
| Active Merges | number | `SELECT count() FROM clusterAllReplicas('__CLUSTER__', system.merges)` |
| Tables With WARNING/CRITICAL Parts | table | `disk/parts_health.sql` filtered to `parts_assessment NOT LIKE 'OK%'` |
| Async Insert Queue Entries | number | `SELECT count() FROM clusterAllReplicas('__CLUSTER__', system.asynchronous_inserts)` |

### `user_activity.json` — User Activity & Errors

Four table panels showing user-level breakdown not available in presets.

| Panel | Type | SQL Source |
|---|---|---|
| Query Activity Per User | table | `users/activity.sql` adapted (user, query_count, total_duration, avg_duration, read_rows, read_bytes) |
| Errors Per User | table | `users/errors.sql` adapted (user, error_count, errors_before_start, errors_while_processing, last_error_time, last_exception) |
| Top Tables Per User | table | `users/top_tables.sql` adapted (user, table_name, query_count) |
| Query Count Over Time | timeseries | `SELECT toStartOfHour(event_time) AS t, user, count() FROM system.query_log WHERE event_time >= now() - interval 24 hour AND type = 'QueryFinish' GROUP BY t, user ORDER BY t` |

### `replication_merges.json` — Replication & Merges

Three table panels covering queue health, active merges, and mutations.

| Panel | Type | SQL Source |
|---|---|---|
| Replication Queue Per Table | table | `merges/queue_depth.sql` adapted (hostname, database, table, queue_depth, entries_with_errors, oldest_entry_age_seconds, last_error) |
| Active Merges | table | `merges/active_merges.sql` adapted (hostname, database, table, elapsed_seconds, progress_pct, total_compressed, is_mutation) |
| Incomplete Mutations | table | `merges/mutations.sql` adapted (hostname, database, table, mutation_id, command, create_time, parts_to_do, latest_fail_reason) |

### `parts_health.json` — Parts Fragmentation

Two panels: full assessment table + filtered critical view.

| Panel | Type | SQL Source |
|---|---|---|
| All Tables — Parts Assessment | table | `disk/parts_health.sql` adapted (database, table, total_parts, avg_rows_per_part, total_size, parts_assessment, part_size_assessment) |
| WARNING / CRITICAL Tables Only | table | Same query filtered to `parts_assessment NOT LIKE 'OK%'` |

### `async_inserts.json` — Async Inserts & Insert Rates

Two panels covering insert volume and async queue health.

| Panel | Type | SQL Source |
|---|---|---|
| Insert Rates Per Table | table | `inserts/insert_rates.sql` adapted (table_name, insert_count, total_rows, total_bytes, avg_rows_per_insert) |
| Async Insert Queue | table | `inserts/async_inserts.sql` adapted (hostname, database, table, pending_entries, total_bytes_queued, oldest_entry_age_seconds) |

---

## Dashboard JSON Schema

Each JSON file follows HyperDX's dashboard schema:

```json
{
  "name": "ClickHouse — <Dashboard Name>",
  "charts": [
    {
      "id": "<stable-uuid>",
      "name": "<Panel Title>",
      "x": 0,
      "y": 0,
      "w": 12,
      "h": 4,
      "series": [
        {
          "type": "table|number|time",
          "dataSource": "clickhouse",
          "sql": "<adapted SQL with __CLUSTER__ placeholder>"
        }
      ]
    }
  ]
}
```

Panel grid is 24 columns wide. `w: 12` = half width, `w: 24` = full width. `h` is in row units (~100px each).

Panel types:
- `"table"` — ranked/detail lists
- `"number"` — single stat (count, sum)
- `"time"` — timeseries line chart

---

## Provisioning Scripts

### `provision.sh` (Bash)

```bash
#!/usr/bin/env bash
set -euo pipefail

HYPERDX_URL="${HYPERDX_URL:-http://localhost:8080}"
HYPERDX_API_KEY="${HYPERDX_API_KEY:-}"
CLICKHOUSE_CLUSTER="${CLICKHOUSE_CLUSTER:-default}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[[ -z "$HYPERDX_API_KEY" ]] && { echo "Error: HYPERDX_API_KEY is required" >&2; exit 1; }

provision_dashboard() {
    local file="$1"
    # Substitute cluster name placeholder
    local json
    json=$(sed "s/__CLUSTER__/$CLICKHOUSE_CLUSTER/g" "$file")
    local name
    name=$(echo "$json" | jq -r '.name')

    # Check if dashboard exists by name
    local existing_id
    existing_id=$(curl -sf -H "Authorization: Bearer $HYPERDX_API_KEY" \
        "$HYPERDX_URL/api/dashboards" | jq -r \
        ".data[] | select(.name == \"$name\") | .id // empty")

    if [[ -n "$existing_id" ]]; then
        echo "Updating: $name"
        curl -sf -X PUT \
             -H "Authorization: Bearer $HYPERDX_API_KEY" \
             -H "Content-Type: application/json" \
             -d "$json" "$HYPERDX_URL/api/dashboards/$existing_id" > /dev/null
    else
        echo "Creating: $name"
        curl -sf -X POST \
             -H "Authorization: Bearer $HYPERDX_API_KEY" \
             -H "Content-Type: application/json" \
             -d "$json" "$HYPERDX_URL/api/dashboards" > /dev/null
    fi
}

for f in "$SCRIPT_DIR"/*.json; do
    provision_dashboard "$f"
done

echo "Done — $(ls "$SCRIPT_DIR"/*.json | wc -l | tr -d ' ') dashboards provisioned."
```

### `provision.ps1` (PowerShell)

Same logic using `Invoke-RestMethod` and `Get-Content | ConvertFrom-Json`. `__CLUSTER__` substitution via `-replace`.

---

## Usage

```bash
# Bash
HYPERDX_URL=http://ch-monitor:8080 \
HYPERDX_API_KEY=your-api-key \
CLICKHOUSE_CLUSTER=my_cluster \
bash clickhouse/dashboards/provision.sh
```

```powershell
# PowerShell
$env:HYPERDX_URL = "http://ch-monitor:8080"
$env:HYPERDX_API_KEY = "your-api-key"
$env:CLICKHOUSE_CLUSTER = "my_cluster"
.\clickhouse\dashboards\provision.ps1
```

Re-running the script updates all dashboards — safe to run after any JSON change or ClickStack upgrade.

---

## Constraints & Non-Goals

- **No preset duplication:** Does not recreate CPU, memory, disk I/O, S3, network, query latency, slowest queries, or insert count panels — those stay in ClickStack's built-in presets
- **No Grafana:** HyperDX only. Grafana integration is out of scope
- **No alerting:** Dashboard visualization only — no threshold alerts configured
- **Lookback hardcoded to 24h in SQL:** Users adjust the HyperDX time picker for display; SQL default is 24h. Panel SQL can be edited per-dashboard in the UI if needed
- **Self-managed only:** Managed ClickStack (ClickHouse Cloud) API differences are out of scope
- **`jq` required** for `provision.sh` — must be installed on the provisioning machine
