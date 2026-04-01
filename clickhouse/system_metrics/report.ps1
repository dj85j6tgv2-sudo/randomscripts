. "$PSScriptRoot\..\lib\common.ps1"

Write-Host ""
Write-Host "=== SYSTEM METRICS ==="

Write-Host ""
Write-Host "--- Current Live Metrics ---"
Invoke-CHQuery "$PSScriptRoot\current_metrics.sql"

Write-Host ""
Write-Host "--- Cumulative Events Since Restart ---"
Invoke-CHQuery "$PSScriptRoot\events_summary.sql"
