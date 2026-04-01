. "$PSScriptRoot\..\lib\common.ps1"

Write-Host ""
Write-Host "=== DISK & STORAGE ==="

Write-Host ""
Write-Host "--- Free Space Per Node ---"
Invoke-CHQuery "$PSScriptRoot\free_space.sql"

Write-Host ""
Write-Host "--- Top Tables By Size ---"
Invoke-CHQuery "$PSScriptRoot\table_sizes.sql"

Write-Host ""
Write-Host "--- Parts Health ---"
Invoke-CHQuery "$PSScriptRoot\parts_health.sql"
