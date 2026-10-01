# run.ps1 - run pipeline stages on Windows.
#   .\pipeline\run.ps1 demo                         synthetic data through the whole analysis (no keys, no downloads)
#   .\pipeline\run.ps1 local [-Count 3]             parse your own newest replays from the Fortnite Demos folder
#   .\pipeline\run.ps1 login                        log in to Epic once (saved to .epic-auth.json)
#   .\pipeline\run.ps1 logout                       revoke and delete the saved Epic login
#   .\pipeline\run.ps1 test [-Source api-fortnite]  check the Epic login (or the api-fortnite.com key)
#   .\pipeline\run.ps1 tournaments [-Region EU] [-Search cash] [-Days 30]   recent tournament windows
#   .\pipeline\run.ps1 find -Window <eventWindowId> [-Pages 10|all]          collect match IDs into data/match_ids.csv
#   .\pipeline\run.ps1 pilot [-Limit 10] [-Via api-fortnite]                 download -> extract -> flatten -> validate
#   .\pipeline\run.ps1 analyze [-Season v37.10]     re-run flatten, validate and zone analysis only
#   .\pipeline\run.ps1 reparse                      re-read every downloaded replay (after parser updates), then analyze
#   .\pipeline\run.ps1 datadir -Path D:\ZoneLabData move downloaded data to another folder/drive and use it from now on
#   .\pipeline\run.ps1 keepraw -Value yes|no        keep raw .replay files after processing (default yes)
#   .\pipeline\run.ps1 pr [-Pages all]               download Epic Power Rankings (top 10,000), then rebuild tables
param(
    [Parameter(Position = 0)][string]$Stage = "help",
    [int]$Limit = 10, [int]$Count = 3, [string]$Window, [string]$Season, [string]$Pages = "10",
    [string]$Region, [string]$Search, [int]$Days = 30, [string]$Via = "epic", [string]$Source = "epic", [string]$Path, [string]$Value
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)   # repo root

function Extractor {
    foreach ($p in "pipeline/extractor/bin/source/ZoneLab.ReplayReader.dll", "pipeline/extractor/bin/nuget/ZoneLab.ReplayReader.dll") { if (Test-Path $p) { return $p } }
    throw "The replay parser needs rebuilding after an update. Run ZoneLab-Data.bat option 1."
}
# Where downloaded tournament data lives: ZONELAB_DATA_DIR in .env, or data\ in the project.
function DataDir {
    if (Test-Path .env) {
        $line = Get-Content .env | Where-Object { $_ -match '^\s*ZONELAB_DATA_DIR\s*=' } | Select-Object -First 1
        if ($line) { $v = ($line -split '=', 2)[1].Trim().Trim('"'); if ($v) { return $v } }
    }
    return "data"
}
$Data = DataDir
function EnvValue([string]$Key) {
    if (-not (Test-Path .env)) { return $null }
    $line = Get-Content .env | Where-Object { $_ -match "^\s*$Key\s*=" } | Select-Object -First 1
    if ($line) { return ($line -split '=', 2)[1].Trim().Trim('"') } else { return $null }
}
function SetEnvValue([string]$Key, [string]$Val) {
    $lines = @(); if (Test-Path .env) { $lines = @(Get-Content .env | Where-Object { $_ -notmatch "^\s*$Key\s*=" }) }
    $lines += "$Key=$Val"
    [IO.File]::WriteAllLines((Join-Path (Get-Location).Path ".env"), [string[]]$lines)
}
# Delete raw replays that were processed successfully, if the user chose to (ZONELAB_KEEP_RAW=no).
function PruneRaw {
    if ((EnvValue "ZONELAB_KEEP_RAW") -ne "no") { return }
    $freed = 0; $n = 0
    foreach ($f in Get-ChildItem "$Data/raw" -Filter *.replay -ErrorAction SilentlyContinue) {
        $parsed = Join-Path "$Data/parsed" ($f.BaseName + ".json")
        if ((Test-Path $parsed) -and (Get-Item $parsed).Length -gt 1000) { $freed += $f.Length; $n++; Remove-Item $f.FullName }
    }
    if ($n) { Write-Host ("Deleted {0} processed raw replays, freeing {1:N1} GB (ZONELAB_KEEP_RAW=no)." -f $n, ($freed / 1GB)) -ForegroundColor Yellow }
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
    "login" { Push-Location pipeline/node; node epic_auth.js login; if ($LASTEXITCODE -eq 0) { node epic_auth.js status }; Pop-Location }
    "logout" { Push-Location pipeline/node; node epic_auth.js logout; Pop-Location }
    "test" { Push-Location pipeline/node; node find_matches.js test --source $Source; Pop-Location }
    "tournaments" {
        $a = @("tournaments", "--days", $Days, "--source", $Source)
        if ($Region) { $a += @("--region", $Region) }
        if ($Search) { $a += @("--search", $Search) }
        Push-Location pipeline/node; node find_matches.js @a; Pop-Location
    }
    "find" { if (-not $Window) { throw "Use: .\pipeline\run.ps1 find -Window <eventWindowId>" }; Push-Location pipeline/node; node find_matches.js window $Window --pages $Pages --source $Source; Pop-Location }
    "pilot" {
        Push-Location pipeline/node; node download.js --limit $Limit --via $Via; Pop-Location
        dotnet (Extractor) "$Data/raw" "$Data/parsed" --mode full
        PruneRaw
        Analyze $Data
    }
    "analyze" { Analyze $Data }
    "pr" {
        Push-Location pipeline/node; node find_matches.js powerrankings --pages $(if ($Pages -eq "10") { "all" } else { $Pages }); Pop-Location
        Analyze $Data
    }
    "keepraw" {
        if ($Value -notin @("yes", "no")) { throw "Use: .\pipeline\run.ps1 keepraw -Value yes|no" }
        SetEnvValue "ZONELAB_KEEP_RAW" $Value
        if ($Value -eq "no") {
            Write-Host "Raw replays will be deleted after they're processed. Re-processing after a future update will then need a fresh download (Epic keeps tournament replays about 30 days)." -ForegroundColor Yellow
            PruneRaw
        } else { Write-Host "Raw replays will be kept after processing." -ForegroundColor Green }
    }
    "reparse" {
        dotnet (Extractor) "$Data/raw" "$Data/parsed" --mode full --overwrite
        Analyze $Data
    }
    "datadir" {
        if (-not $Path) { throw "Use: .\pipeline\run.ps1 datadir -Path D:\ZoneLabData" }
        # Resolve against PowerShell's folder (.NET's working folder can differ).
        $full = { param($p) if ([IO.Path]::IsPathRooted($p)) { [IO.Path]::GetFullPath($p) } else { [IO.Path]::GetFullPath((Join-Path (Get-Location).Path $p)) } }
        $new = & $full $Path
        $old = & $full $Data
        if ($new -eq $old) { Write-Host "Already using $new"; break }
        if ((Test-Path $new) -and (Get-ChildItem $new -Force | Where-Object { $_.Name -notin @("raw", "parsed", "tables", "reports", "match_ids.csv", "tournaments.csv") })) {
            throw "$new already contains other files. Choose a new or empty folder (for example D:\ZoneLabData) so nothing gets mixed together."
        }
        New-Item -ItemType Directory -Force $new | Out-Null
        foreach ($d in "raw", "parsed", "tables", "reports") {
            if (Test-Path "$old\$d") { robocopy "$old\$d" "$new\$d" /E /MOVE /XF .gitkeep /NFL /NDL /NJH /NJS | Out-Null }
            New-Item -ItemType Directory -Force "$new\$d" | Out-Null
        }
        foreach ($f in "match_ids.csv", "tournaments.csv") { if (Test-Path "$old\$f") { Move-Item "$old\$f" "$new\$f" -Force } }
        SetEnvValue "ZONELAB_DATA_DIR" $new
        Write-Host "Downloaded data now lives in $new (moved from $old). Saved in .env." -ForegroundColor Green
        Write-Host "Restart Start-ZoneLab.bat so the dashboard reads the new location." -ForegroundColor Yellow
    }
    default { Get-Content $PSCommandPath | Select-Object -Skip 1 -First 7 }
}
