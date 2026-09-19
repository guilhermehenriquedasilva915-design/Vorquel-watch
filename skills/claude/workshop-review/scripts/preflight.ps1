$ErrorActionPreference = "Stop"

$Root = Join-Path $env:LOCALAPPDATA "VorquelWorkshopSkill"
$Crv = Join-Path $Root ".venv\Scripts\crv.exe"

$status = [ordered]@{
    ffmpeg = $false
    crv = $false
    runtime_root = $Root
}

$status.ffmpeg = $null -ne (Get-Command ffmpeg -ErrorAction SilentlyContinue)
$status.crv = Test-Path $Crv

$status | ConvertTo-Json

if (-not $status.ffmpeg -or -not $status.crv) {
    exit 2
}

exit 0
