. "$PSScriptRoot\..\lib\common.ps1"

Write-Host ""
Write-Host "=== MERGES & MUTATIONS ==="

Write-Host ""
Write-Host "--- Active Merges ---"
Invoke-CHQuery "$PSScriptRoot\active_merges.sql"

Write-Host ""
Write-Host "--- Incomplete Mutations ---"
Invoke-CHQuery "$PSScriptRoot\mutations.sql"

Write-Host ""
Write-Host "--- Replication Queue Depth ---"
Invoke-CHQuery "$PSScriptRoot\queue_depth.sql"
