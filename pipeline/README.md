# Replay pipeline

Tournament match IDs → server replays → parsed JSON → research tables → validation → zone-randomness experiment.

## What was tested, and what wasn't

| Part | Status |
| --- | --- |
| Replay extractor (C#) | Tested on real replays from v16.10 (2021) and v32.00 (Nov 2024). Parses in 2–5 s each. |
| `flatten.py`, `validate.py`, `zone_analysis.py` | Tested on those real replays and on 800 synthetic matches. |
| Zone analysis correctness | Tested: random zones give no false positives; planted bus, edge and persistence patterns are each detected. |
| `find_matches.js` (api-fortnite.com) | Written against its published OpenAPI spec; tested end to end against a stand-in server with the same response shapes; **not yet run against the live service**. |
| `download.js` (Epic) | Error handling tested; **real download not run** (the build machine couldn't reach Epic). |
| `setup.ps1` / `run.ps1` | Written to mirror the tested bash scripts; **not run on Windows**. |
| `run.sh local` and `run.sh demo` from the repo root | Tested after the move into the monorepo. |

Two findings shaped the design:

- **The Node parser (`fortnite-replay-parser`) is out of date.** Even its latest GitHub version crashes on a Nov 2024 replay. Parsing uses [Shiqan/FortniteReplayDecompressor](https://github.com/Shiqan/FortniteReplayDecompressor) instead (C#, maintained into Sept 2026).
- **No Epic account is needed.** `fortnite-replay-downloader` v3 gets its own access token.

## Requirements

- Node.js LTS
- Python 3.10+
- .NET 10 SDK
- A free [api-fortnite.com](https://api-fortnite.com) API key (for finding tournament match IDs)
- Git (only for `-FromSource` builds)

## Quickstart (Windows PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\pipeline\setup.ps1     # once, from the repo root
.\pipeline\run.ps1 demo                                  # 1. synthetic demo data (same as npm run demo-data)
.\pipeline\run.ps1 local -Count 3                        # 2. parse your newest replays -> "My replays" in the dashboard
# add FORTNITE_API_KEY to .env (or use ZoneLab-Data.bat option 2), then:
.\pipeline\run.ps1 tournaments                           # 3. list event windows
.\pipeline\run.ps1 find -Window <eventWindowId>          # 4. collect match IDs into data/match_ids.csv
.\pipeline\run.ps1 pilot -Limit 10                       # 5. download, parse, validate -> "Tournaments" in the dashboard
```

macOS/Linux: same stages with `./pipeline/setup.sh` and `./pipeline/run.sh demo | local <folder> | tournaments | find <id> | pilot [n] | analyze`.

All commands run from the repo root and write to the repo-level `data/`, `data_local/` and `data_synthetic/` folders, which the dashboard reads.

Start with step 5 at `-Limit 10`. Open `data/reports/validation.md` before adding more matches. Then check 3 matches against the in-game replay viewer: bus line, each storm circle, and one elimination per match.

## How it fits together

1. **`node/find_matches.js`** lists recent tournament windows from api-fortnite.com, then reads a window's leaderboard. Each team's `sessionHistory` lists the matches it played; every `sessionId` is an Epic match ID, appended to `data/match_ids.csv`.
2. **`node/download.js`** downloads each match's replay and metadata into the repo's `data/raw/`. It can be re-run safely and logs failures to `data/raw/failed.txt`.
3. **`extractor/` (`fn-extract`)** turns each `.replay` into one compact JSON in `data/parsed/`. Always use `--mode full`, because movement tracks need it.
4. **`python/flatten.py`** builds the tables in `data/tables/`.
5. **`python/validate.py`** writes `data/reports/validation.md`.
6. **`python/zone_analysis.py`** writes `data/reports/zone_analysis.md` and `zone_analysis.png`.

## Tables

| Table | One row per | Key columns |
| --- | --- | --- |
| `matches` | match | season, playlist, event window, region, length, counts |
| `players` | player | team_index, placement, team_placement, kills, death time and location, disconnected |
| `teams` | team | placement, team_placement (best member), team_kills |
| `zones` | storm phase | current center/radius → next center/radius, shrink times |
| `zone_offsets` | storm phase | `offset_ratio`, `shrink_ratio`, `u`, `angle_deg` (season-independent geometry) |
| `bus` | match | bearing, start/end, `bus_source` (`aircraft` or `derived_skydive`) |
| `positions` | player × second | x, y, z, velocity, in_storm, dbno, skydiving |
| `kills` | kill-feed entry | victim, finisher, downed, distance, location |
| `eliminations` | elim event | Epic IDs, knocked, gun type |

Notes:

- **Coordinates and time.** Positions are Unreal units (100 per metre). All times are in-game world seconds, so zones, positions and kills line up.
- **Bus path.** Neither test replay contained the bus's own flight data, so the bus line is fitted through players' first skydiving positions. On the 2021 replay this gave 237.8°, against 237.7° recorded by the game.
- **Team placement.** Disconnected players keep the placement from when they left. Use `team_placement`.

## Reading the zone analysis

`u = (distance moved / maximum allowed distance)²` is uniform between 0 and 1 if the next center is random inside the allowed area. Mean `u` near 1 means pulls hug the edge.

The tests cover distance (Kolmogorov–Smirnov), compass direction, direction relative to the bus, and persistence from one pull to the next (all three Rayleigh tests).

- **Trust the per-phase rows and the per-match row.** The pooled row treats linked pulls as independent and overstates significance. In the synthetic `persist` test it showed a fake direction effect (p < 0.001) that the per-match row correctly rejected (p = 0.106).
- **Analyse one season at a time** with `--season v37.10`. Before trusting any effect, confirm it on a held-out season.
- **Bus and persistence are confounded.** If zones pull toward the bus, consecutive pulls also look persistent.
- **Island shape can create non-randomness by itself**, such as zones avoiding water.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Extractor fails on new-season replays | `setup.ps1 -FromSource` builds against the latest GitHub source, which updates faster than NuGet. |
| `positions=0` | Run the extractor with `--mode full`. |
| Validation warns "looks like a client replay" | Normal for your own Demos-folder replays. For tournament research, use server replays (`is_server_replay=1`). |
| Download auth errors | Epic may have revoked the downloader's built-in client. Download through api-fortnite.com instead (`pilot -Via api-fortnite`, or ZoneLab-Data.bat option 8). |
| No matches found for a window | It may not have been played yet, or be older than about 30 days. Use `-Pages all` to read every lobby. |
| Epic download fails for every match | Download through api-fortnite.com instead: `pilot -Via api-fortnite` (2 credits per match). |

Read Epic's and api-fortnite.com's terms before scaling up, and keep request rates modest.
