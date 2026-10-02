# From real data to conclusions

This guide takes you from an empty dashboard to findings you can defend:

1. Get tournament replays in.
2. Check they're sound.
3. Work through the pages in the right order.
4. Decide what counts as a finding.

Everything on Windows runs from **`Vantage-Data.bat`** (double-click it in the project folder for a numbered menu) and **`Start-Vantage.bat`** (the dashboard).

## Using the Data page (recommended)

Everything below can be done from the dashboard: open Vantage (`Start-Vantage.bat`) and click **Data** at the top right.

- **Status cards** show your Epic sign-in, the replay parser, matches collected, waiting and ready, Power Rankings, map names and storage.
- **Sign in to Epic** appears when you're signed out: open Epic's page, paste the code, done.
- **Weekly tier-1 collection** runs the weekly routine in one click, optionally refreshing Power Rankings first.
- **Quick actions:** refresh Power Rankings, update map names, rebuild tables, re-process every match.
- **Tournaments:** refresh the list from Epic, search it, then **Collect matches** and **Download** for any window. Each row shows how many matches are collected and downloaded.
- **Download collected matches** with a lobby-strength filter, and a preview of what's next.
- **Import replay files** from a team's archive or your own folder.
- **Settings and advanced tools:** data folder, keeping or deleting raw replays, parallel downloads (1–3 at a time; 2 by default), re-processing one match, the season survey and parser rebuild, the api-fortnite.com fallback, and signing out.

Jobs run one at a time with a live progress bar, the current step, time remaining and a log, and can be cancelled. When a job finishes, every page refreshes. `Vantage-Data.bat` still works and does the same things from a menu.

## 1. One-time setup

1. Install the **.NET 10 SDK** from https://dotnet.microsoft.com/download. It's needed to read replay files.
2. Have a **secondary Epic account** ready (a free one is fine). Match IDs come straight from Epic's tournament leaderboards, which only answer to a logged-in account. Epic hasn't published these endpoints for outside use, so keep your main account out of it.
3. In `Vantage-Data.bat`:
   - choose **1** (installs the replay parser);
   - then choose **2** to log in. In your browser, log in at https://www.epicgames.com/account/personal with the secondary account, then open the link the menu prints. Epic shows a short block of text containing `"authorizationCode"`. Copy it all (Ctrl+A, Ctrl+C), paste it back and press Enter. This works once and expires after 5 minutes.
   - The login is saved in `.epic-auth.json` in the project folder, never uploaded to GitHub, and reused from then on. Option **L** revokes it at Epic and deletes it.

## 2. Importing replay files

Option **6** imports `.replay` files into your data from any folder: a pro team's archive of past tournaments, or your own matches (press Enter for the Fortnite replay folder). They're processed like downloaded matches.

Replays recorded on a player's own PC only see players near them; tournament (server) replays see everyone. For strategy questions, use the dashboard's **Server replays only** filter.

If processing fails on the current season, the parser hasn't caught up with the latest patch yet. Build it from its newest source instead. In PowerShell, from the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\pipeline\setup.ps1 -FromSource
```

## 3. Pull tournament data

1. **Choose 3** to list tournament windows that finished in the last 30 days. You can filter by region (EU, NAC…) and by name (cash, fncs…). Start narrow: **one season, one region**. Mixing seasons or regions early makes patterns harder to see.
2. **Choose 4** for each window you want, pasting its ID. It reads the window's leaderboard: every team's entry lists the matches it played, and their IDs are added to `data/match_ids.csv`. The default reads 10 pages (the top teams' lobbies); answer `all` to reach every lobby.
3. **Choose 5** to download and process. Start with **10 matches** to check the data. Then read the quality report (section 4) and run larger batches of 50–100. Each run skips matches it already has, so you can stop and resume safely.

**Storage.** Each tournament replay is about 130 MB (100 matches ≈ 13 GB). To keep them on another drive, choose **D** in `Vantage-Data.bat` and give a **new or empty** folder such as `D:\ZoneLabData`. Everything downloaded so far moves there, and the pipeline and dashboard use it from then on. The folder is saved in `.env` as `ZONELAB_DATA_DIR`.

**Smaller downloads.** Replays are downloaded without *checkpoints*, the periodic full snapshots a replay viewer uses to jump around the timeline. The parser never reads them, so results are identical. To watch a match in Fortnite's own replay viewer, download it with `node download.js --with-checkpoints` from `pipeline\node`.

**Deleting raw replays.** Option **K** can delete each raw replay once it's processed, keeping only the processed data (about 3 MB per match). Leave raw replays on while the pipeline is still changing: re-processing after an update needs them, and Epic only keeps tournament replays for about 30 days.

**After an update** that changes how replays are read, choose **7** to re-process everything you've already downloaded. Nothing is downloaded again.

**Strongest lobbies first.** Option 4 records which ranked players appeared in each match, so option 5 downloads the most stacked lobbies first: those with the most Power Rankings top-1,000 players (or, before Power Rankings are downloaded, the most top-leaderboard players). Option **V** previews the order. Download Power Rankings (option P) and re-run option 4 on your windows before big downloads, so the ordering has the best information.

**Drop spot names.** Choose **M** to download the current map's named places from fortnite-api.com (free, no key). The Drops and loot page uses them to name each landing area. Re-run M at the start of each season, because the map changes.

**Weekly tier-1 routine.** Replays expire after about 30 days, so collect every week:
1. **P**: refresh Power Rankings (they update weekly).
2. **T**: weekly collection. It finds every high-tier window that ended in the last 7 days (FNCS, division cups, finals, cash cups, official events; mobile and Zero Build cups are skipped), collects their match IDs, downloads only lobbies with at least 3 Power Rankings top-1,000 players (strongest first), and processes them. LAN events whose players use event accounts won't pass the lobby filter: download those with **W**.

**In-match events.** Processed matches now include health and shields, damage (who hit whom, how hard), chest opens, every item spawned and picked up, the weapon in each player's hands, and player-built pieces. When a new season adds new build pieces, weapons or chests, run **S** on a new match, then **G**, then **1**.

**Power Rankings (recommended).** Choose **P** to download Epic's Power Rankings: the official cross-event skill rating for the top 10,000 players, read from the same Epic leaderboard service as match IDs (about 7 minutes). Every player then gets their PR rank and rating, and lobby strength becomes the share of each lobby in the PR top 1,000, which is comparable across rounds, regions and weeks. Rankings update weekly, so re-run P now and then.

**Session ranks (fallback).** Option 4 also saves every player's rank from the leaderboard pages it reads (`player_ranks.csv`). Each match then gets a *lobby strength*: the share of its players ranked in that tournament's top 1,000. The dashboard's **Lobby strength** filter keeps only strong lobbies. Use it for strategy questions (positioning, fights, height), where mixed-skill lobbies blur decisions with skill gaps; storm and bus questions can use every lobby. Read at least 10 pages so the top 1,000 is covered. To add ranks to matches you already have, run option 4 again on their window, then option 7.

**Pull tournaments within about 30 days.** Epic deletes tournament replays after roughly a month; option 4 warns when a window's matches are getting close to that.

If option 5 fails for every match, Epic has changed something on the free route. **Option 8** downloads the same replays through api-fortnite.com instead, at 2 credits per match. It needs a free api-fortnite.com key saved with **option 9** (the free tier gives 15 credits a day; paid credit packs and plans add more).

How much data each page needs before its conclusions are trustworthy (the dashboard checks this for you, and a section shows **Promising: needs more data** until it's met):

| Page | Recommended |
| --- | --- |
| Storm | 100–200+ matches from one season |
| Drops and loot, Rotations, Fights | 100+ strong-lobby matches per mode |
| Surge | 20+ matches where surge triggered (later rounds and finals) |
| Compare | 100+ matches on each side for small differences; large ones show sooner |

## 4. Check quality before reading results

1. Open `data/reports/validation.md`. Every match is marked PASS, WARN or FAIL, with the reason. If more than 1 in 10 fail, stop and send me the report before adding more.
2. Open the dashboard on **Overview**: **Server replays** should be 100%. Under **Details and method**, *Storm phases recorded per match* should show nearly every match at the same high count.
3. Check three matches by eye in Fortnite's own replay viewer. Confirm that the storm circles and one elimination per match match what the dashboard shows. This catches problems no automated check will.

## 5. Reading the pages

Each page answers one question in sections. Every section leads with:

- a plain-language **takeaway** and a **confidence badge**: *Strong evidence*, *Some evidence*, *Promising: needs more data* (a pattern on a small or uncertain sample), *No clear pattern*, or *Not enough data yet*;
- a few **key numbers** and the **charts** that show it best.

**Details and method** holds the rest: each test and its strength, a **Can you trust it?** checklist (sample size, single season, replay source, lobby strength), full tables, notes and how everything is measured. Hover the **i** beside any number for its definition.

Suggested order: **Overview** → **Storm** (zone rules and placement) → **Drops and loot** → **Rotations** → **Fights** → **Surge**. Use the **Lobby strength** filter for tier-1 questions, and **Compare** to check a pattern on a different season, region or event before treating it as a rule.

## 6. What counts as a finding

Treat a result as a **finding** only when all four hold:

1. The conclusion shows **Strong** or **Clear** evidence at the page's threshold (p < 0.005 by default).
2. Every **Can you trust it?** check passes.
3. The effect is big enough to matter, not just detectable. For example, a direction concentration (R) below 0.1 or a correlation (rho) below 0.1 is real but faint.
4. It **replicates** on a different season or period.

Anything short of that is a **lead**: worth re-testing with more data, not worth changing how a team plays.

**Keep a findings log.** Use **Copy as text** on the conclusion panel and paste it into a running document with the date. A finding that replicates across several seasons is the kind of result worth showing a pro team.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| "Not logged in to Epic" or "The saved login no longer works" | Run option 2 again (a password change revokes saved logins) |
| Option 2 says the authorization code was not found | The code expired or was already used. Open the link again for a fresh one |
| Option 2 says the client "has been disabled" | Epic switched off that login client. Close the menu, run `set EPIC_CLIENT=switch` in a Command Prompt in the project folder, start `Vantage-Data.bat` from that same window, and log in again. Tell me too, so the default can be updated |
| "Epic asked us to slow down" | The script waits and retries by itself. If it keeps happening, wait 10 minutes |
| Option 4 finds no match IDs | The window may not have been played yet, or its replays have expired |
| Option 5 shows "request failed" for every match | Use option 8, and send me the last lines of `data/raw/failed.txt` |
| Processing fails on new replays | Rebuild the parser from source (section 2) |
| Dashboard still shows "No tournament data yet" | Make sure option 5 finished, then refresh the page. If you used option D, restart `Start-Vantage.bat` |
| "The replay parser needs rebuilding after an update" | Run option 1, then option 7 |
