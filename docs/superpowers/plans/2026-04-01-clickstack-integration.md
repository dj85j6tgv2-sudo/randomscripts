# ClickStack Dashboard Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build 5 HyperDX dashboard JSON files covering monitoring gaps not in ClickStack presets, plus idempotent bash/PowerShell provisioning scripts that deploy them via the HyperDX REST API.

**Architecture:** Dashboard definitions live in `clickhouse/dashboards/*.json` with `__CLUSTER__` as a SQL placeholder substituted at provision time. `provision.sh` / `provision.ps1` POST or PUT each JSON file to `$HYPERDX_URL/api/dashboards` — checking by name to update existing or create new. SQL is adapted from existing `clickhouse/` domain files: named parameters removed, `{cluster:String}` replaced with `'__CLUSTER__'`, `{lookback_hours:UInt32}` replaced with hardcoded `24`.

**Tech Stack:** HyperDX REST API, Bash + `curl` + `jq`, PowerShell `Invoke-RestMethod`, ClickHouse SQL (system tables), JSON

---

## File Map

| File | Responsibility |
|---|---|
| `clickhouse/dashboards/provision.sh` | Bash: idempotent create/update all dashboards via HyperDX API |
| `clickhouse/dashboards/provision.ps1` | PowerShell: same for Windows |
| `clickhouse/dashboards/overview.json` | 6-panel DBA summary (top users, errors, queue, merges, parts alerts, async queue) |
| `clickhouse/dashboards/user_activity.json` | 4 panels: query activity, errors, top tables per user, query count timeseries |
| `clickhouse/dashboards/replication_merges.json` | 3 panels: replication queue, active merges, incomplete mutations |
| `clickhouse/dashboards/parts_health.json` | 2 panels: full parts assessment + WARNING/CRITICAL filter |
| `clickhouse/dashboards/async_inserts.json` | 2 panels: insert rates per table + async insert queue |

---

## Pre-Flight: HyperDX API Key

Before starting, you need a HyperDX API key:
1. Open your self-managed ClickStack UI at `http://localhost:8080` (or your host)
2. Go to **Settings → API Keys → Create API Key**
3. Copy the key — you will pass it as `HYPERDX_API_KEY`

---

## Task 1: Scaffold `clickhouse/dashboards/` and verify HyperDX API schema

**Files:**
- Create directory: `clickhouse/dashboards/`

- [ ] **Step 1: Create the directory**

```bash
mkdir -p clickhouse/dashboards
```

- [ ] **Step 2: Verify HyperDX is reachable and list existing dashboards**

```bash
curl -sf \
  -H "Authorization: Bearer $HYPERDX_API_KEY" \
  "${HYPERDX_URL:-http://localhost:8080}/api/dashboards" | jq '.'
```

Expected: JSON response with a `data` array. The array may be empty `[]` or contain preset dashboards. Note the structure — each dashboard object has `_id`, `name`, and `charts` fields.

- [ ] **Step 3: If dashboards exist, inspect one to confirm chart schema**

```bash
curl -sf \
  -H "Authorization: Bearer $HYPERDX_API_KEY" \
  "${HYPERDX_URL:-http://localhost:8080}/api/dashboards" \
  | jq '.data[0]'
```

Expected: a single dashboard object. Confirm that:
- `.name` is a string
- `.charts` is an array of objects with `id`, `name`, `x`, `y`, `w`, `h`, `series` fields
- `.charts[0].series[0]` has `type` and `dataSource` fields

If the schema differs significantly from above (e.g., no `series.sql` field), note the actual field names and adjust the JSON files in later tasks accordingly.

- [ ] **Step 4: Commit scaffolded directory**

```bash
git add clickhouse/dashboards/.gitkeep 2>/dev/null || touch clickhouse/dashboards/.gitkeep
git add clickhouse/dashboards/
git commit -m "feat(clickstack): scaffold dashboards directory"
```

---

## Task 2: `provision.sh`

**Files:**
- Create: `clickhouse/dashboards/provision.sh`

- [ ] **Step 1: Write `clickhouse/dashboards/provision.sh`**

```bash
#!/usr/bin/env bash
# provision.sh — Idempotent HyperDX dashboard provisioner.
#
# Usage:
#   HYPERDX_API_KEY=your-key bash clickhouse/dashboards/provision.sh
#   HYPERDX_URL=http://ch-monitor:8080 HYPERDX_API_KEY=key CLICKHOUSE_CLUSTER=mycluster \
#     bash clickhouse/dashboards/provision.sh
#
# Requirements: curl, jq

set -euo pipefail

HYPERDX_URL="${HYPERDX_URL:-http://localhost:8080}"
HYPERDX_API_KEY="${HYPERDX_API_KEY:-}"
CLICKHOUSE_CLUSTER="${CLICKHOUSE_CLUSTER:-default}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[[ -z "$HYPERDX_API_KEY" ]] && { echo "Error: HYPERDX_API_KEY is required" >&2; exit 1; }
command -v jq  > /dev/null || { echo "Error: jq is required" >&2; exit 1; }
command -v curl > /dev/null || { echo "Error: curl is required" >&2; exit 1; }

provision_dashboard() {
    local file="$1"

    # Substitute __CLUSTER__ placeholder with actual cluster name
    local json
    json=$(sed "s/__CLUSTER__/$CLICKHOUSE_CLUSTER/g" "$file")

    local name
    name=$(echo "$json" | jq -r '.name')

    # Check if a dashboard with this name already exists
    local existing_id
    existing_id=$(curl -sf \
        -H "Authorization: Bearer $HYPERDX_API_KEY" \
        "$HYPERDX_URL/api/dashboards" \
        | jq -r ".data[] | select(.name == \"$name\") | ._id // empty" \
        | head -1)

    if [[ -n "$existing_id" ]]; then
        echo "  Updating : $name (id: $existing_id)"
        curl -sf -X PUT \
             -H "Authorization: Bearer $HYPERDX_API_KEY" \
             -H "Content-Type: application/json" \
             -d "$json" \
             "$HYPERDX_URL/api/dashboards/$existing_id" > /dev/null
    else
        echo "  Creating : $name"
        curl -sf -X POST \
             -H "Authorization: Bearer $HYPERDX_API_KEY" \
             -H "Content-Type: application/json" \
             -d "$json" \
             "$HYPERDX_URL/api/dashboards" > /dev/null
    fi
}

json_files=("$SCRIPT_DIR"/*.json)
echo "Provisioning ${#json_files[@]} dashboard(s) to $HYPERDX_URL ..."
echo "  Cluster : $CLICKHOUSE_CLUSTER"
echo ""

for f in "${json_files[@]}"; do
    provision_dashboard "$f"
done

echo ""
echo "Done — ${#json_files[@]} dashboard(s) provisioned."
```

- [ ] **Step 2: Make executable and verify syntax**

```bash
chmod +x clickhouse/dashboards/provision.sh
bash -n clickhouse/dashboards/provision.sh
```

Expected: no output (syntax OK).

- [ ] **Step 3: Commit**

```bash
git add clickhouse/dashboards/provision.sh
git commit -m "feat(clickstack): add provision.sh — idempotent HyperDX dashboard provisioner"
```

---

## Task 3: `provision.ps1`

**Files:**
- Create: `clickhouse/dashboards/provision.ps1`

- [ ] **Step 1: Write `clickhouse/dashboards/provision.ps1`**

```powershell
# provision.ps1 — Idempotent HyperDX dashboard provisioner (PowerShell).
#
# Usage:
#   $env:HYPERDX_API_KEY = "your-key"; .\clickhouse\dashboards\provision.ps1
#   $env:HYPERDX_URL = "http://ch-monitor:8080"
#   $env:CLICKHOUSE_CLUSTER = "mycluster"
#   .\clickhouse\dashboards\provision.ps1

$HYPERDX_URL     = if ($env:HYPERDX_URL)        { $env:HYPERDX_URL }        else { "http://localhost:8080" }
$HYPERDX_API_KEY = if ($env:HYPERDX_API_KEY)     { $env:HYPERDX_API_KEY }    else { "" }
$CH_CLUSTER      = if ($env:CLICKHOUSE_CLUSTER)  { $env:CLICKHOUSE_CLUSTER } else { "default" }
$ScriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $HYPERDX_API_KEY) {
    Write-Error "HYPERDX_API_KEY is required"
    exit 1
}

$headers = @{
    "Authorization" = "Bearer $HYPERDX_API_KEY"
    "Content-Type"  = "application/json"
}

function Invoke-ProvisionDashboard {
    param([string]$File)

    # Substitute __CLUSTER__ placeholder
    $json = (Get-Content $File -Raw) -replace '__CLUSTER__', $CH_CLUSTER
    $name = ($json | ConvertFrom-Json).name

    # Fetch existing dashboards, find by name
    $existing = Invoke-RestMethod -Uri "$HYPERDX_URL/api/dashboards" `
        -Headers @{ "Authorization" = "Bearer $HYPERDX_API_KEY" } `
        -Method Get
    $existingId = ($existing.data | Where-Object { $_.name -eq $name } | Select-Object -First 1)._id

    if ($existingId) {
        Write-Host "  Updating : $name (id: $existingId)"
        Invoke-RestMethod -Uri "$HYPERDX_URL/api/dashboards/$existingId" `
            -Headers $headers -Method Put -Body $json | Out-Null
    } else {
        Write-Host "  Creating : $name"
        Invoke-RestMethod -Uri "$HYPERDX_URL/api/dashboards" `
            -Headers $headers -Method Post -Body $json | Out-Null
    }
}

$jsonFiles = Get-ChildItem -Path $ScriptDir -Filter "*.json"
Write-Host "Provisioning $($jsonFiles.Count) dashboard(s) to $HYPERDX_URL ..."
Write-Host "  Cluster : $CH_CLUSTER"
Write-Host ""

foreach ($f in $jsonFiles) {
    Invoke-ProvisionDashboard -File $f.FullName
}

Write-Host ""
Write-Host "Done — $($jsonFiles.Count) dashboard(s) provisioned."
```

- [ ] **Step 2: Verify PowerShell syntax**

```powershell
$null = [System.Management.Automation.Language.Parser]::ParseFile(
    "clickhouse/dashboards/provision.ps1", [ref]$null, [ref]$null
)
Write-Host "Syntax OK"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add clickhouse/dashboards/provision.ps1
git commit -m "feat(clickstack): add provision.ps1 — PowerShell dashboard provisioner"
```

---

## Task 4: `overview.json`

**Files:**
- Create: `clickhouse/dashboards/overview.json`

- [ ] **Step 1: Write `clickhouse/dashboards/overview.json`**

```json
{
  "name": "ClickHouse — DBA Overview",
  "charts": [
    {
      "id": "ch-ov-01",
      "name": "Top 5 Users (last 24h)",
      "x": 0, "y": 0, "w": 12, "h": 4,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT user, count() AS query_count, formatReadableTimeDelta(round(avg(query_duration_ms)/1000)) AS avg_duration, formatReadableQuantity(sum(read_rows)) AS total_read_rows FROM clusterAllReplicas('__CLUSTER__', system.query_log) WHERE event_time >= now() - interval 24 hour AND type = 'QueryFinish' GROUP BY user ORDER BY query_count DESC LIMIT 5"
      }]
    },
    {
      "id": "ch-ov-02",
      "name": "Users With Errors (last 24h)",
      "x": 12, "y": 0, "w": 12, "h": 4,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT user, count() AS error_count, max(event_time) AS last_error_time, anyLast(exception) AS last_exception FROM clusterAllReplicas('__CLUSTER__', system.query_log) WHERE event_time >= now() - interval 24 hour AND type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing') GROUP BY user ORDER BY error_count DESC"
      }]
    },
    {
      "id": "ch-ov-03",
      "name": "Replication Queue Total",
      "x": 0, "y": 4, "w": 8, "h": 3,
      "series": [{
        "type": "number",
        "dataSource": "clickhouse",
        "sql": "SELECT count() AS total_queue_entries FROM clusterAllReplicas('__CLUSTER__', system.replication_queue)"
      }]
    },
    {
      "id": "ch-ov-04",
      "name": "Active Merges",
      "x": 8, "y": 4, "w": 8, "h": 3,
      "series": [{
        "type": "number",
        "dataSource": "clickhouse",
        "sql": "SELECT count() AS active_merges FROM clusterAllReplicas('__CLUSTER__', system.merges)"
      }]
    },
    {
      "id": "ch-ov-05",
      "name": "Async Insert Queue Entries",
      "x": 16, "y": 4, "w": 8, "h": 3,
      "series": [{
        "type": "number",
        "dataSource": "clickhouse",
        "sql": "SELECT count() AS pending_entries FROM clusterAllReplicas('__CLUSTER__', system.asynchronous_inserts)"
      }]
    },
    {
      "id": "ch-ov-06",
      "name": "Tables With WARNING / CRITICAL Parts",
      "x": 0, "y": 7, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT database, table, count() AS total_parts, CASE WHEN count() > 1000 THEN 'CRITICAL' WHEN count() > 500 THEN 'WARNING' WHEN count() > 100 THEN 'CAUTION' ELSE 'OK' END AS parts_assessment FROM system.parts WHERE active = 1 AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') GROUP BY database, table HAVING parts_assessment IN ('CRITICAL', 'WARNING', 'CAUTION') ORDER BY total_parts DESC"
      }]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON syntax**

```bash
python3 -m json.tool clickhouse/dashboards/overview.json > /dev/null && echo "JSON valid"
```

Expected: `JSON valid`

- [ ] **Step 3: Commit**

```bash
git add clickhouse/dashboards/overview.json
git commit -m "feat(clickstack): add overview dashboard — top users, errors, queue, merges, parts alerts"
```

---

## Task 5: `user_activity.json`

**Files:**
- Create: `clickhouse/dashboards/user_activity.json`

- [ ] **Step 1: Write `clickhouse/dashboards/user_activity.json`**

```json
{
  "name": "ClickHouse — User Activity",
  "charts": [
    {
      "id": "ch-ua-01",
      "name": "Query Activity Per User (last 24h)",
      "x": 0, "y": 0, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT user, count() AS query_count, formatReadableTimeDelta(round(sum(query_duration_ms)/1000)) AS total_duration, formatReadableTimeDelta(round(avg(query_duration_ms)/1000)) AS avg_duration, formatReadableQuantity(sum(read_rows)) AS total_read_rows, formatReadableSize(sum(read_bytes)) AS total_read_bytes FROM clusterAllReplicas('__CLUSTER__', system.query_log) WHERE event_time >= now() - interval 24 hour AND type = 'QueryFinish' GROUP BY user ORDER BY query_count DESC"
      }]
    },
    {
      "id": "ch-ua-02",
      "name": "Errors Per User (last 24h)",
      "x": 0, "y": 5, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT user, count() AS error_count, countIf(type = 'ExceptionBeforeStart') AS errors_before_start, countIf(type = 'ExceptionWhileProcessing') AS errors_while_processing, max(event_time) AS last_error_time, anyLast(exception) AS last_exception_message FROM clusterAllReplicas('__CLUSTER__', system.query_log) WHERE event_time >= now() - interval 24 hour AND type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing') GROUP BY user ORDER BY error_count DESC"
      }]
    },
    {
      "id": "ch-ua-03",
      "name": "Top Tables Per User (last 24h)",
      "x": 0, "y": 10, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT user, t AS table_name, count() AS query_count FROM clusterAllReplicas('__CLUSTER__', system.query_log) ARRAY JOIN tables AS t WHERE event_time >= now() - interval 24 hour AND type = 'QueryFinish' AND t NOT LIKE 'system.%' AND t != '' GROUP BY user, table_name ORDER BY user ASC, query_count DESC LIMIT 50"
      }]
    },
    {
      "id": "ch-ua-04",
      "name": "Query Count Over Time By User (last 24h)",
      "x": 0, "y": 15, "w": 24, "h": 5,
      "series": [{
        "type": "time",
        "dataSource": "clickhouse",
        "sql": "SELECT toStartOfHour(event_time) AS t, user, count() AS query_count FROM clusterAllReplicas('__CLUSTER__', system.query_log) WHERE event_time >= now() - interval 24 hour AND type = 'QueryFinish' GROUP BY t, user ORDER BY t ASC"
      }]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON syntax**

```bash
python3 -m json.tool clickhouse/dashboards/user_activity.json > /dev/null && echo "JSON valid"
```

Expected: `JSON valid`

- [ ] **Step 3: Commit**

```bash
git add clickhouse/dashboards/user_activity.json
git commit -m "feat(clickstack): add user_activity dashboard — query volume, errors, top tables per user"
```

---

## Task 6: `replication_merges.json`

**Files:**
- Create: `clickhouse/dashboards/replication_merges.json`

- [ ] **Step 1: Write `clickhouse/dashboards/replication_merges.json`**

```json
{
  "name": "ClickHouse — Replication & Merges",
  "charts": [
    {
      "id": "ch-rm-01",
      "name": "Replication Queue Per Table",
      "x": 0, "y": 0, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT hostName() AS hostname, database, table, count() AS queue_depth, countIf(last_exception != '') AS entries_with_errors, max(toUnixTimestamp(now()) - toUnixTimestamp(create_time)) AS oldest_entry_age_seconds, anyIf(last_exception, last_exception != '') AS last_error FROM clusterAllReplicas('__CLUSTER__', system.replication_queue) GROUP BY hostname, database, table ORDER BY queue_depth DESC"
      }]
    },
    {
      "id": "ch-rm-02",
      "name": "Active Merges",
      "x": 0, "y": 5, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT hostName() AS hostname, database, table, round(elapsed, 1) AS elapsed_seconds, round(progress * 100, 1) AS progress_pct, formatReadableSize(total_size_bytes_compressed) AS total_compressed, is_mutation, result_part_name FROM clusterAllReplicas('__CLUSTER__', system.merges) ORDER BY elapsed DESC"
      }]
    },
    {
      "id": "ch-rm-03",
      "name": "Incomplete Mutations",
      "x": 0, "y": 10, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT hostName() AS hostname, database, table, mutation_id, command, create_time, parts_to_do, is_done, latest_fail_reason FROM clusterAllReplicas('__CLUSTER__', system.mutations) WHERE is_done = 0 ORDER BY create_time ASC"
      }]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON syntax**

```bash
python3 -m json.tool clickhouse/dashboards/replication_merges.json > /dev/null && echo "JSON valid"
```

Expected: `JSON valid`

- [ ] **Step 3: Commit**

```bash
git add clickhouse/dashboards/replication_merges.json
git commit -m "feat(clickstack): add replication_merges dashboard — queue depth, active merges, mutations"
```

---

## Task 7: `parts_health.json`

**Files:**
- Create: `clickhouse/dashboards/parts_health.json`

- [ ] **Step 1: Write `clickhouse/dashboards/parts_health.json`**

```json
{
  "name": "ClickHouse — Parts Health",
  "charts": [
    {
      "id": "ch-ph-01",
      "name": "All Tables — Parts Assessment",
      "x": 0, "y": 0, "w": 24, "h": 7,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT database, table, count() AS total_parts, formatReadableQuantity(sum(rows)) AS total_rows, round(avg(rows), 0) AS avg_rows_per_part, formatReadableSize(sum(bytes_on_disk)) AS total_size, CASE WHEN count() > 1000 THEN 'CRITICAL - Too many parts (>1000)' WHEN count() > 500 THEN 'WARNING  - Many parts (>500)' WHEN count() > 100 THEN 'CAUTION  - Getting many parts (>100)' ELSE 'OK       - Reasonable part count' END AS parts_assessment, CASE WHEN avg(rows) < 1000 THEN 'POOR      - Very small parts' WHEN avg(rows) < 10000 THEN 'FAIR      - Small parts' WHEN avg(rows) < 100000 THEN 'GOOD      - Medium parts' ELSE 'EXCELLENT - Large parts' END AS part_size_assessment FROM system.parts WHERE active = 1 AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') GROUP BY database, table ORDER BY total_parts DESC LIMIT 30"
      }]
    },
    {
      "id": "ch-ph-02",
      "name": "WARNING / CRITICAL Tables Only",
      "x": 0, "y": 7, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT database, table, count() AS total_parts, formatReadableQuantity(sum(rows)) AS total_rows, round(avg(rows), 0) AS avg_rows_per_part, formatReadableSize(sum(bytes_on_disk)) AS total_size, CASE WHEN count() > 1000 THEN 'CRITICAL' WHEN count() > 500 THEN 'WARNING' WHEN count() > 100 THEN 'CAUTION' ELSE 'OK' END AS parts_assessment FROM system.parts WHERE active = 1 AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') GROUP BY database, table HAVING parts_assessment IN ('CRITICAL', 'WARNING') ORDER BY total_parts DESC"
      }]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON syntax**

```bash
python3 -m json.tool clickhouse/dashboards/parts_health.json > /dev/null && echo "JSON valid"
```

Expected: `JSON valid`

- [ ] **Step 3: Commit**

```bash
git add clickhouse/dashboards/parts_health.json
git commit -m "feat(clickstack): add parts_health dashboard — fragmentation assessment"
```

---

## Task 8: `async_inserts.json`

**Files:**
- Create: `clickhouse/dashboards/async_inserts.json`

- [ ] **Step 1: Write `clickhouse/dashboards/async_inserts.json`**

```json
{
  "name": "ClickHouse — Async Inserts & Insert Rates",
  "charts": [
    {
      "id": "ch-ai-01",
      "name": "Insert Rates Per Table (last 24h)",
      "x": 0, "y": 0, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT t AS table_name, count() AS insert_count, formatReadableQuantity(sum(written_rows)) AS total_rows, formatReadableSize(sum(written_bytes)) AS total_bytes, formatReadableQuantity(round(avg(written_rows))) AS avg_rows_per_insert FROM clusterAllReplicas('__CLUSTER__', system.query_log) ARRAY JOIN tables AS t WHERE event_time >= now() - interval 24 hour AND type = 'QueryFinish' AND query_kind = 'Insert' AND t != '' GROUP BY table_name ORDER BY sum(written_rows) DESC LIMIT 20"
      }]
    },
    {
      "id": "ch-ai-02",
      "name": "Async Insert Queue",
      "x": 0, "y": 5, "w": 24, "h": 5,
      "series": [{
        "type": "table",
        "dataSource": "clickhouse",
        "sql": "SELECT hostName() AS hostname, database, table, count() AS pending_entries, formatReadableSize(sum(bytes)) AS total_bytes_queued, max(toUnixTimestamp(now()) - toUnixTimestamp(first_update)) AS oldest_entry_age_seconds, min(first_update) AS oldest_entry_time FROM clusterAllReplicas('__CLUSTER__', system.asynchronous_inserts) GROUP BY hostname, database, table ORDER BY pending_entries DESC"
      }]
    }
  ]
}
```

- [ ] **Step 2: Validate JSON syntax**

```bash
python3 -m json.tool clickhouse/dashboards/async_inserts.json > /dev/null && echo "JSON valid"
```

Expected: `JSON valid`

- [ ] **Step 3: Commit**

```bash
git add clickhouse/dashboards/async_inserts.json
git commit -m "feat(clickstack): add async_inserts dashboard — insert rates and async queue"
```

---

## Task 9: End-to-end provision + verify

**Files:** none (integration test only)

- [ ] **Step 1: Run the full provision against live ClickStack**

```bash
HYPERDX_URL=http://localhost:8080 \
HYPERDX_API_KEY=your-api-key \
CLICKHOUSE_CLUSTER=default \
bash clickhouse/dashboards/provision.sh
```

Expected output:
```
Provisioning 5 dashboard(s) to http://localhost:8080 ...
  Cluster : default

  Creating : ClickHouse — DBA Overview
  Creating : ClickHouse — User Activity
  Creating : ClickHouse — Replication & Merges
  Creating : ClickHouse — Parts Health
  Creating : ClickHouse — Async Inserts & Insert Rates

Done — 5 dashboard(s) provisioned.
```

- [ ] **Step 2: Verify dashboards appear in the HyperDX API**

```bash
curl -sf \
  -H "Authorization: Bearer $HYPERDX_API_KEY" \
  "${HYPERDX_URL:-http://localhost:8080}/api/dashboards" \
  | jq '[.data[] | select(.name | startswith("ClickHouse")) | .name]'
```

Expected:
```json
[
  "ClickHouse — DBA Overview",
  "ClickHouse — User Activity",
  "ClickHouse — Replication & Merges",
  "ClickHouse — Parts Health",
  "ClickHouse — Async Inserts & Insert Rates"
]
```

- [ ] **Step 3: Open HyperDX UI and verify each dashboard renders panels without errors**

Open `http://localhost:8080` → Dashboards. Click each of the 5 new dashboards and confirm panels load (may show empty results if cluster is idle — that is OK, errors in the panel indicate a schema or SQL issue).

- [ ] **Step 4: Run provision again to verify idempotency**

```bash
HYPERDX_URL=http://localhost:8080 \
HYPERDX_API_KEY=your-api-key \
CLICKHOUSE_CLUSTER=default \
bash clickhouse/dashboards/provision.sh
```

Expected: all 5 lines show `Updating :` instead of `Creating :`. No duplicate dashboards created.

- [ ] **Step 5: Commit final state and push**

```bash
git add -A
git status  # confirm only expected files, no accidental additions
git commit -m "feat(clickstack): complete ClickStack dashboard integration" \
  --allow-empty
git push origin main
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All 5 dashboards implemented (overview, user_activity, replication_merges, parts_health, async_inserts). Both provision.sh and provision.ps1 written. `__CLUSTER__` substitution in both scripts. No preset duplication — CPU, memory, disk I/O, query latency, slowest queries not included.
- [x] **Placeholder scan:** No TBD/TODO. All JSON has complete SQL. All script steps show complete code.
- [x] **Type consistency:** `provision_dashboard()` in bash and `Invoke-ProvisionDashboard` in PowerShell both use `._id` to match the HyperDX API response field. All JSON files use `"dataSource": "clickhouse"` consistently. All chart IDs are unique across all JSON files (`ch-ov-*`, `ch-ua-*`, `ch-rm-*`, `ch-ph-*`, `ch-ai-*`).
- [x] **Schema note:** Task 1 Step 3 instructs the implementer to verify the actual HyperDX chart schema against a live instance before writing JSON files. If `series[].sql` or `series[].dataSource` fields differ from the expected schema, Task 1 surfaces this before any JSON is written.
- [x] **Non-time-windowed panels:** Active merges (ch-rm-02) and incomplete mutations (ch-rm-03) have no time filter — correct, they are live snapshots.
