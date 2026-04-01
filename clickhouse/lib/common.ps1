# common.ps1 — ClickHouse connection config for PowerShell.
# Usage: dot-source this file at the top of each report script:
#   . "$PSScriptRoot\..\lib\common.ps1"

$script:CH_HOST    = if ($env:CLICKHOUSE_HOST)     { $env:CLICKHOUSE_HOST }     else { "localhost" }
$script:CH_PORT    = if ($env:CLICKHOUSE_PORT)     { $env:CLICKHOUSE_PORT }     else { "9000" }
$script:CH_USER    = if ($env:CLICKHOUSE_USER)     { $env:CLICKHOUSE_USER }     else { "default" }
$script:CH_PASS    = if ($env:CLICKHOUSE_PASSWORD) { $env:CLICKHOUSE_PASSWORD } else { "" }  # Empty = passwordless auth
$script:CH_CLUSTER = if ($env:CLICKHOUSE_CLUSTER)  { $env:CLICKHOUSE_CLUSTER }  else { "default" }

# LOOKBACK_HOURS controls how far back time-windowed queries look.
# Default: 24 (last 24 hours). Override: $env:LOOKBACK_HOURS = 168 (7 days), 720 (30 days)
$script:LOOKBACK_HOURS = if ($env:LOOKBACK_HOURS) { $env:LOOKBACK_HOURS } else { "24" }

if ($script:LOOKBACK_HOURS -notmatch '^\d+$') {
    Write-Error "LOOKBACK_HOURS must be a positive integer"
    exit 1
}

function Invoke-CHQuery {
    param([string]$SqlFile)
    if (-not (Test-Path $SqlFile)) {
        Write-Error "SQL file not found: $SqlFile"
        return
    }
    $chArgs = @(
        "--host",   $script:CH_HOST,
        "--port",   $script:CH_PORT,
        "--user",   $script:CH_USER,
        "--param_lookback_hours=$($script:LOOKBACK_HOURS)",
        "--param_cluster=$($script:CH_CLUSTER)",
        "--multiquery",
        "--format", "PrettyCompact",
        "--queries-file", $SqlFile
    )
    if ($script:CH_PASS) {
        $chArgs += @("--password", $script:CH_PASS)
    }
    & clickhouse-client @chArgs
}
