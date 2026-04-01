. "$PSScriptRoot\..\lib\common.ps1"

Write-Host ""
Write-Host "=== QUERY PERFORMANCE (lookback: $($script:LOOKBACK_HOURS)h) ==="

Write-Host ""
Write-Host "--- Currently Running Queries ---"
Invoke-CHQuery "$PSScriptRoot\running_now.sql"

Write-Host ""
Write-Host "--- Slowest Queries ---"
Invoke-CHQuery "$PSScriptRoot\slow_queries.sql"

Write-Host ""
Write-Host "--- Most Memory-Intensive Query Patterns ---"
Invoke-CHQuery "$PSScriptRoot\memory_heavy.sql"
