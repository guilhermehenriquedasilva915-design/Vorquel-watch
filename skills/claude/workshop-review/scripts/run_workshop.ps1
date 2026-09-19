param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [string]$Intent = "study the workshop and compare spoken content with what is shown on screen",

    [string]$From = "",

    [string]$To = "",

    [ValidateRange(640, 1920)]
    [int]$FrameWidth = 1280,

    [ValidateRange(0, 600)]
    [int]$MaxFrames = 0
)

$ErrorActionPreference = "Stop"

$Root = Join-Path $env:LOCALAPPDATA "VorquelWorkshopSkill"
$Crv = Join-Path $Root ".venv\Scripts\crv.exe"
$Runs = Join-Path $Root "runs"

if (-not (Test-Path $Crv)) {
    throw "Temporary workshop runtime is not installed. Run setup_windows.ps1 first."
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg is not available on PATH."
}

$NormalizedSource = $Source

if ($Source -match '^https?://') {
    try {
        $Uri = [System.Uri]$Source
    } catch {
        throw "Invalid URL."
    }

    if ($Uri.Scheme -notin @("http", "https")) {
        throw "Only HTTP/HTTPS URLs are accepted."
    }

    $HostName = $Uri.Host.ToLowerInvariant()
    $AllowedHosts = @(
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be"
    )

    if ($HostName -notin $AllowedHosts) {
        throw "This temporary skill accepts only public YouTube URLs."
    }
} else {
    try {
        $Resolved = Resolve-Path -LiteralPath $Source -ErrorAction Stop
    } catch {
        throw "Local media file was not found."
    }

    if (-not (Test-Path -LiteralPath $Resolved.Path -PathType Leaf)) {
        throw "Source must be a regular local file."
    }

    $NormalizedSource = $Resolved.Path
}

New-Item -ItemType Directory -Force -Path $Runs | Out-Null

$RunId = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$OutDir = Join-Path $Runs $RunId
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$Arguments = @(
    $NormalizedSource,
    "-o", $OutDir,
    "--grid",
    "--viewer",
    "--adaptive",
    "--frame-width", $FrameWidth.ToString(),
    "--why", $Intent
)

if ($From) {
    $Arguments += @("--from", $From)
}

if ($To) {
    $Arguments += @("--to", $To)
}

if ($MaxFrames -gt 0) {
    $Arguments += @("--max-frames", $MaxFrames.ToString())
}

& $Crv @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Video processing failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Output "VORQUEL_WORKSHOP_RUN=$OutDir"
