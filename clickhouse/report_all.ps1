# report_all.ps1 — Run the full ClickHouse cluster monitoring report.
#
# Usage:
#   .\clickhouse\report_all.ps1
#   $env:LOOKBACK_HOURS = 168; .\clickhouse\report_all.ps1    # last 7 days
#   $env:LOOKBACK_HOURS = 720; .\clickhouse\report_all.ps1    # last 30 days
#   $env:CLICKHOUSE_HOST = "ch-node1"; .\clickhouse\report_all.ps1
#   $env:CLICKHOUSE_PASSWORD = "secret"; .\clickhouse\report_all.ps1

$host_val    = if ($env:CLICKHOUSE_HOST)    { $env:CLICKHOUSE_HOST }    else { "localhost" }
$port_val    = if ($env:CLICKHOUSE_PORT)    { $env:CLICKHOUSE_PORT }    else { "9000" }
$cluster_val = if ($env:CLICKHOUSE_CLUSTER) { $env:CLICKHOUSE_CLUSTER } else { "default" }
$lookback    = if ($env:LOOKBACK_HOURS)     { $env:LOOKBACK_HOURS }     else { "24" }

Write-Host "========================================================"
Write-Host " ClickHouse Cluster Report"
Write-Host " $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host " Host:     ${host_val}:${port_val}"
Write-Host " Cluster:  $cluster_val"
Write-Host " Lookback: ${lookback}h"
Write-Host "========================================================"

& "$PSScriptRoot\cluster\report.ps1"
& "$PSScriptRoot\disk\report.ps1"
& "$PSScriptRoot\queries\report.ps1"
& "$PSScriptRoot\users\report.ps1"
& "$PSScriptRoot\merges\report.ps1"
& "$PSScriptRoot\inserts\report.ps1"
& "$PSScriptRoot\system_metrics\report.ps1"

Write-Host ""
Write-Host "========================================================"
Write-Host " Report complete — $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "========================================================"
