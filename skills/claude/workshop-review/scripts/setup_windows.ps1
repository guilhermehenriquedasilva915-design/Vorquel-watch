param(
    [string]$PackageVersion = "0.10.5"
)

$ErrorActionPreference = "Stop"

$Root = Join-Path $env:LOCALAPPDATA "VorquelWorkshopSkill"
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Crv = Join-Path $Venv "Scripts\crv.exe"

Write-Host "Vorquel Workshop Review — local runtime setup"
Write-Host "Runtime root: $Root"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install Python 3.10+ first."
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "ffmpeg was not found on PATH."
    Write-Host "If you want to install it with winget, run manually:"
    Write-Host "  winget install Gyan.FFmpeg"
    throw "ffmpeg is required. Nothing was installed automatically."
}

New-Item -ItemType Directory -Force -Path $Root | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "runs") | Out-Null

if (-not (Test-Path $Python)) {
    & py -3 -m venv $Venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the isolated Python environment."
    }
}

& $Python -c "import sys; assert sys.version_info >= (3,10), sys.version"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.10+ is required."
}

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to update pip."
}

$Spec = "claude-real-video[fast]==$PackageVersion"
Write-Host "Installing pinned runtime: $Spec"
& $Python -m pip install $Spec
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install claude-real-video runtime."
}

if (-not (Test-Path $Crv)) {
    throw "crv executable was not created in the isolated environment."
}

& $Crv --help | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "crv installation check failed."
}

& ffmpeg -version | Select-Object -First 1

Write-Host ""
Write-Host "Setup complete."
Write-Host "No browser cookies or API keys were configured."
Write-Host "Runtime: $Crv"
