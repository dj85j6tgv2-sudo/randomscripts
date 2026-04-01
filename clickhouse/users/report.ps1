. "$PSScriptRoot\..\lib\common.ps1"

Write-Host ""
Write-Host "=== USER ACTIVITY (lookback: $($script:LOOKBACK_HOURS)h) ==="

Write-Host ""
Write-Host "--- Query Activity Per User ---"
Invoke-CHQuery "$PSScriptRoot\activity.sql"

Write-Host ""
Write-Host "--- Errors Per User ---"
Invoke-CHQuery "$PSScriptRoot\errors.sql"

Write-Host ""
Write-Host "--- Top Tables Per User ---"
Invoke-CHQuery "$PSScriptRoot\top_tables.sql"
