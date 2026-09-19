param(
    [string]$SupabaseUrl = "https://xnygwzuckijaxzlszfpn.supabase.co"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Venv = Join-Path $RepoRoot ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

Write-Host "Vorquel Watch alpha setup"
Write-Host "Repository: $RepoRoot"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher py not found. Install Python 3.12 first, then rerun this script."
}

if (-not (Test-Path $Python)) {
    & py -3.12 -m venv $Venv
}

& $Python -m pip install --require-hashes -r "$RepoRoot\backend\requirements.lock"
& $Python -m pip install -e "$RepoRoot\backend" --no-deps

if (-not $SupabaseUrl) {
    $SupabaseUrl = Read-Host "Supabase project URL"
}

& $Python -m vorquel_watch.cli configure --url $SupabaseUrl
& $Python -m vorquel_watch.cli doctor

$DataDir = Join-Path $env:LOCALAPPDATA "VorquelWatch"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$ClaudeSnippet = Join-Path $DataDir "claude-mcp.json"
& $Python -m vorquel_watch.cli print-claude-config | Set-Content -Encoding UTF8 $ClaudeSnippet

Write-Host ""
Write-Host "Setup complete."
Write-Host "Claude MCP snippet written to: $ClaudeSnippet"
Write-Host "Start the worker with scripts\windows\start-worker.ps1"
Write-Host "Merge the generated mcpServers entry into Claude Desktop MCP config and restart Claude Desktop."
