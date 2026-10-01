# setup.ps1 - one-time setup on Windows. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File .\pipeline\setup.ps1
#   powershell -ExecutionPolicy Bypass -File .\pipeline\setup.ps1 -FromSource   # build parser from latest GitHub source
param([switch]$FromSource)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)   # repo root

function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Write-Host "Missing: $cmd. $hint" -ForegroundColor Red; exit 1 }
}
Need node   "Install Node.js LTS from https://nodejs.org"
Need python "Install Python 3.10+ from https://python.org (tick 'Add to PATH')"
Need dotnet "Install the .NET 10 SDK from https://dotnet.microsoft.com/download"

Write-Host "`n[1/4] Node packages" -ForegroundColor Cyan
Push-Location pipeline/node; npm install; Pop-Location

Write-Host "`n[2/4] Python packages" -ForegroundColor Cyan
node scripts/setup-python.mjs   # project .venv, shared with the API

Write-Host "`n[3/4] Replay extractor (.NET)" -ForegroundColor Cyan
function Build-FromSource {
    Need git "Install Git from https://git-scm.com"
    $vendor = "pipeline/extractor/vendor/FortniteReplayDecompressor"
    if (Test-Path $vendor) { git -C $vendor pull --ff-only | Out-Host }
    else { git clone --depth 1 https://github.com/Shiqan/FortniteReplayDecompressor.git $vendor | Out-Host }
    dotnet build pipeline/extractor/source/fn-extract.source.csproj -c Release | Out-Host
    return ($LASTEXITCODE -eq 0)
}
$built = $false
if (-not $FromSource) {
    dotnet build pipeline/extractor/fn-extract.csproj -c Release | Out-Host
    $built = ($LASTEXITCODE -eq 0)
    if (-not $built) { Write-Host "`nThe NuGet build failed. Building from the latest GitHub source instead..." -ForegroundColor Yellow }
}
if (-not $built) { $built = Build-FromSource }
if (-not $built) {
    Write-Host "`nThe replay parser did not build. Copy the error lines above and send them over." -ForegroundColor Red
    exit 1
}
Write-Host "Replay parser built." -ForegroundColor Green

Write-Host "`n[4/4] Config" -ForegroundColor Cyan
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env (only needed for the api-fortnite.com fallback)." }
Write-Host "`nSetup done. Next: .\pipeline\run.ps1 local   (parse your own replays)" -ForegroundColor Green
