. "$PSScriptRoot\..\lib\common.ps1"

Write-Host ""
Write-Host "=== INSERT PERFORMANCE (lookback: $($script:LOOKBACK_HOURS)h) ==="

Write-Host ""
Write-Host "--- Insert Rates Per Table ---"
Invoke-CHQuery "$PSScriptRoot\insert_rates.sql"

Write-Host ""
Write-Host "--- Async Insert Queue ---"
Invoke-CHQuery "$PSScriptRoot\async_inserts.sql"
