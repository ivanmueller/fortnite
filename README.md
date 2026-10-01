# Zone Lab

Statistical research on competitive Fortnite storm behaviour. The repo has three parts:

- **Replay pipeline** (`pipeline/`): tournament match IDs → server replays → parsed JSON → research tables.
- **Analysis API** (`api/`, Python): runs statistical tests on whatever matches you select.
- **Dashboard** (`web/`, React + TypeScript): pick matches by date, season, region or event window, then read the results in Chrome.

## Quickstart

You need Node.js 20+ and Python 3.10+. The .NET 10 SDK is only needed for parsing replays. `npm run setup` puts the Python packages in a project-local `.venv`, which every script here uses automatically.

```bash
npm run setup       # once: installs JavaScript and Python dependencies
npm run demo-data   # once: 240 synthetic matches across three seasons, so every page has data
npm run dev         # starts the API (port 8000) and dashboard (port 5173), and opens your browser
```

The dashboard opens in your default browser. If that isn't Chrome, open `http://localhost:5173` in Chrome yourself.

**On Windows, double-click `Start-ZoneLab.bat` instead.** It runs setup and creates the demo data the first time, starts both servers, and opens Chrome when the dashboard is ready. If Zone Lab is already running, it just opens the dashboard. Close its window to stop everything. For a desktop shortcut, right-click the file and choose *Send to → Desktop (create shortcut)*.

### Live updates

- **Dashboard code** (`web/src`): saving a file updates the open page in place, without a reload.
- **Analysis code** (`api/`): saving a file restarts the API. The dashboard notices within two seconds, refetches, and shows "Code change picked up" in the top bar.
- **Data**: when the pipeline rewrites the tables, the next query reads the new files. There's no import step.

## Real data and conclusions

**[docs/REAL_DATA.md](docs/REAL_DATA.md)** walks through it end to end: setup, pulling tournament replays, checking quality, working through the pages, and deciding what counts as a finding. On Windows, double-click **`ZoneLab-Data.bat`** for a numbered menu of every data step.

Every page explains itself:

- **About this page** gives the question, the method, how to draw a conclusion, and the limits.
- **Every number and test** has a definition on hover.
- **Every chart** has a line on how to read it.
- **A conclusion panel** grades the evidence and checks whether the data behind it can be trusted.

The page text lives in `api/fnlab/analyses/guides.py`, so it can be edited in one place.

## Datasets

Switch between them in the left rail.

| Dataset | Folder | How to fill it |
| --- | --- | --- |
| Tournaments | `data/tables` | `pipeline/run.ps1 find` then `pilot` (see `pipeline/README.md`) |
| My replays | `data_local/tables` | `pipeline/run.ps1 local`, which parses your newest replays from the Fortnite Demos folder |
| Demo | `data_synthetic/tables` | `npm run demo-data` |

The demo seasons have planted behaviour, so you can check the analyses find what's there:

- **v96.10:** random zones.
- **v97.10:** pulls lean toward the bus heading.
- **v98.10:** every pull hits the edge of the circle.

In the demo, teams that finish lower also tend to rotate late, and better teams build height from phase 2 onward, so the higher player wins most fights late in the game. The demo effects are exaggerated so they're easy to see; real ones will be smaller.

## Analyses

| Page | Question it answers |
| --- | --- |
| Dataset overview | What's in this selection: matches over time, seasons, regions, phases recorded |
| Is the storm random? | Do pulls depart from randomness in distance, compass direction, relation to the bus, or persistence? |
| Storm pull geometry | How far and how much each phase's circle moves, and where next circles land |
| Compare two periods | Did storm behaviour change between selection A and selection B (e.g. two seasons, before and after a patch)? |
| Position vs placement | Where were teams, relative to the closing circle, when it started to shrink, and how did they finish? |
| Where eliminations happen | How far eliminations happen from the circle the storm is closing to, by phase |
| High ground | Does the higher player win fights, do teams holding height finish better, and from which storm phase? |
| Matches | The selected matches as a table (CSV download) |

Each analysis shows a headline, key numbers, and the evidence: one p-value track per statistical test, with the threshold marked. Below that come charts, tables and notes on how to read the results.

Tests are designed so linked observations don't inflate significance. They use one summary per match, or one pull per phase per match.

## Project layout

```
api/                     Python analysis service (FastAPI + DuckDB)
  fnlab/main.py          HTTP endpoints
  fnlab/filters.py       match selection (dates, seasons, regions, event windows)
  fnlab/store.py         DuckDB views over the Parquet tables
  fnlab/stats.py         statistical tests (Rayleigh, KS, permutation, Spearman)
  fnlab/result.py        result format, including the neutral chart spec
  fnlab/analyses/        one file per analysis
  tests/                 pytest: builds a small synthetic dataset and runs everything
web/                     dashboard (React + TypeScript + Vite)
  src/charts/            chart renderer registry and the Plotly adapter
  src/components/        selection rail, evidence strip, result and table views
pipeline/                replay download, parsing and table building (see pipeline/README.md)
data/                    tournament data (contents git-ignored; match_ids.csv is tracked)
scripts/py.mjs           runs Python as python3, python or py, whichever this machine has
```

## Adding an analysis

1. Create `api/fnlab/analyses/my_analysis.py`:

   ```python
   from ..result import Result
   from ..store import df
   from . import Context, Param, register

   @register("my_analysis", "My question", "One sentence on what it answers.",
             params=[Param("phase", "Phase", "number", 3)])
   def run(ctx: Context) -> Result:
       r = Result()
       z = df(ctx.con, "SELECT * FROM zone_offsets JOIN sel USING (match_id) WHERE phase = ?",
              [ctx.params["phase"]])
       r.headline = f"{len(z):,} pulls in phase {ctx.params['phase']}."
       r.chart("histogram", "Distance moved ÷ radius",
               [dict(name="Pulls", values=z["offset_ratio"].tolist())], bins=20)
       return r
   ```

2. Add `my_analysis` to the import line at the bottom of `api/fnlab/analyses/__init__.py`.

Save, and it appears as a new tab with a settings form built from `params`. The temp table `sel` always holds the selected match IDs. Tables available:

- `matches`
- `players`
- `teams`
- `zones`
- `zone_offsets`
- `bus`
- `positions`
- `kills`
- `eliminations`

## Swapping or adding chart libraries

Analyses never produce Plotly code. They return a neutral spec, a chart kind plus series:

- `bar`
- `stacked_bar`
- `line`
- `histogram`
- `polar_histogram`
- `box`
- `map_points`

`web/src/charts/ChartView.tsx` maps each kind to a renderer, currently `plotlyRenderer.tsx` for all of them. To use another library for some charts, for example deck.gl for maps or ECharts for everything, write a renderer with the same props and change the mapping. No analysis code changes.

## Scaling it into a platform

The current structure already separates the pieces a hosted version would need:

- **Data access.** DuckDB reads Parquet files today. The same SQL runs against MotherDuck, or Parquet in S3, when the data outgrows one machine.
- **API.** FastAPI serves typed JSON, which another client (mobile, coach tools, a bot) can use unchanged.
- **Dashboard.** It's a static build (`npm run build` → `web/dist`), hostable on any CDN.

What it doesn't have yet:

- user accounts;
- caching of results;
- a job queue for slow analyses;
- hosted storage for replays.

## Tests

```bash
npm test     # API tests on a synthetic dataset + dashboard typecheck
```

GitHub Actions runs the same tests on every push (`.github/workflows/ci.yml`).
