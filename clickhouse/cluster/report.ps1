. "$PSScriptRoot\..\lib\common.ps1"

Write-Host ""
Write-Host "=== CLUSTER HEALTH ==="

Write-Host ""
Write-Host "--- Node Status ---"
Invoke-CHQuery "$PSScriptRoot\node_status.sql"

Write-Host ""
Write-Host "--- Replication Queue ---"
Invoke-CHQuery "$PSScriptRoot\replication_lag.sql"
