$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Vorquel Watch virtual environment not found. Run scripts/windows/setup.ps1 first."
}

& $Python -m vorquel_watch.worker
