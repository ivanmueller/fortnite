# run.ps1 - run pipeline stages on Windows.
#   .\pipeline\run.ps1 demo                         synthetic data through the whole analysis (no keys, no downloads)
#   .\pipeline\run.ps1 local [-Count 3]             parse your own newest replays from the Fortnite Demos folder
#   .\pipeline\run.ps1 test                         check your api-fortnite.com key
#   .\pipeline\run.ps1 tournaments [-Region EU] [-Search cash] [-Days 30]   recent tournament windows
#   .\pipeline\run.ps1 find -Window <eventWindowId> [-Pages 10|all]          collect match IDs into data/match_ids.csv
#   .\pipeline\run.ps1 pilot [-Limit 10] [-Via api-fortnite]                 download -> extract -> flatten -> validate
#   .\pipeline\run.ps1 analyze [-Season v37.10]     re-run flatten, validate and zone analysis only
param(
    [Parameter(Position = 0)][string]$Stage = "help",
    [int]$Limit = 10, [int]$Count = 3, [string]$Window, [string]$Season, [string]$Pages = "10",
    [string]$Region, [string]$Search, [int]$Days = 30, [string]$Via = "epic"
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)   # repo root

function Extractor {
    foreach ($p in "pipeline/extractor/bin/source/fn-extract.dll", "pipeline/extractor/bin/nuget/fn-extract.dll") { if (Test-Path $p) { return $p } }
    throw "Extractor not built. Run .\pipeline\setup.ps1 first."
}
function Analyze([string]$DataDir = "data") {
    node scripts/py.mjs pipeline/python/flatten.py --data-dir $DataDir
    node scripts/py.mjs pipeline/python/validate.py --data-dir $DataDir
    if ($Season) { node scripts/py.mjs pipeline/python/zone_analysis.py --data-dir $DataDir --season $Season }
    else { node scripts/py.mjs pipeline/python/zone_analysis.py --data-dir $DataDir }
    Write-Host "`nReports in $DataDir/reports (validation.md, zone_analysis.md, zone_analysis.png)" -ForegroundColor Green
}

switch ($Stage) {
    "demo" {
        node scripts/py.mjs pipeline/python/make_synthetic.py --preset demo --sample-dt 3
        Analyze "data_synthetic"
        Write-Host "Demo seasons: v96 random, v97 pulls toward the bus, v98 pulls hit the edge. Open the dashboard with npm run dev." -ForegroundColor Yellow
    }
    "local" {
        $demos = Join-Path $env:LOCALAPPDATA "FortniteGame\Saved\Demos"
        $files = Get-ChildItem $demos -Filter *.replay | Sort-Object LastWriteTime -Descending | Select-Object -First $Count
        if (-not $files) { throw "No replays found in $demos" }
        New-Item -ItemType Directory -Force data_local/raw | Out-Null
        $files | Copy-Item -Destination data_local/raw
        dotnet (Extractor) data_local/raw data_local/parsed --mode full
        Analyze "data_local"
        Write-Host "These are client replays: expect WARN on track coverage. Tournament server replays should PASS." -ForegroundColor Yellow
    }
    "test" { Push-Location pipeline/node; node find_matches.js test; Pop-Location }
    "tournaments" {
        $a = @("tournaments", "--days", $Days)
        if ($Region) { $a += @("--region", $Region) }
        if ($Search) { $a += @("--search", $Search) }
        Push-Location pipeline/node; node find_matches.js @a; Pop-Location
    }
    "find" { if (-not $Window) { throw "Use: .\pipeline\run.ps1 find -Window <eventWindowId>" }; Push-Location pipeline/node; node find_matches.js window $Window --pages $Pages; Pop-Location }
    "pilot" {
        Push-Location pipeline/node; node download.js --limit $Limit --via $Via; Pop-Location
        dotnet (Extractor) data/raw data/parsed --mode full
        Analyze "data"
    }
    "analyze" { Analyze "data" }
    default { Get-Content $PSCommandPath | Select-Object -Skip 1 -First 7 }
}
