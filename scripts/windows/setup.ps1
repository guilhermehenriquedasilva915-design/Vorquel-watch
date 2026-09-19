param(
    [string]$SupabaseUrl = "https://xnygwzuckijaxzlszfpn.supabase.co"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Venv = Join-Path $RepoRoot ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

Write-Host "Vorquel Watch alpha setup"
Write-Host "Repository: $RepoRoot"

if (-not (Test-Path $Python)) {
    $BasePython = $null
    $BasePythonArgs = @()

    if (Get-Command py -ErrorAction SilentlyContinue) {
        $BasePython = "py"
        $BasePythonArgs = @("-3.12")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $BasePython = (Get-Command python).Source
    } elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
        $BasePython = (Get-Command python3).Source
    }

    if (-not $BasePython) {
        Write-Host ""
        Write-Host "Python 3.12 was not found."
        Write-Host "Install it, then rerun setup. With winget:"
        Write-Host "  winget install -e --id Python.Python.3.12"
        throw "Python 3.12 is required."
    }

    & $BasePython @BasePythonArgs -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "A Python command exists, but it is not Python 3.12."
        Write-Host "Install Python 3.12, then rerun setup. With winget:"
        Write-Host "  winget install -e --id Python.Python.3.12"
        throw "Python 3.12 is required."
    }

    & $BasePython @BasePythonArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Vorquel Watch virtual environment."
    }
}

& $Python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "The existing .venv is not Python 3.12. Remove only .venv and rerun setup."
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
