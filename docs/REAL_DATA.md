# From real data to conclusions

This guide takes you from an empty dashboard to findings you can defend:

1. Get tournament replays in.
2. Check they're sound.
3. Work through the pages in the right order.
4. Decide what counts as a finding.

Everything on Windows runs from **`ZoneLab-Data.bat`** (double-click it in the project folder for a numbered menu) and **`Start-ZoneLab.bat`** (the dashboard).

## 1. One-time setup

1. Install the **.NET 10 SDK** from https://dotnet.microsoft.com/download. It's needed to read replay files.
2. Have a **secondary Epic account** ready (a free one is fine). Match IDs come straight from Epic's tournament leaderboards, which only answer to a logged-in account. Epic hasn't published these endpoints for outside use, so keep your main account out of it.
3. In `ZoneLab-Data.bat`:
   - choose **1** (installs the replay parser);
   - then choose **2** to log in. In your browser, log in at https://www.epicgames.com/account/personal with the secondary account, then open the link the menu prints. Epic shows a short block of text containing `"authorizationCode"`. Copy it all (Ctrl+A, Ctrl+C), paste it back and press Enter. This works once and expires after 5 minutes.
   - The login is saved in `.epic-auth.json` in the project folder, never uploaded to GitHub, and reused from then on. Option **L** revokes it at Epic and deletes it.

## 2. Test the parser on your own replays

Before pulling tournaments, check the parser reads the current season:

1. Play or spectate a match so Fortnite saves a replay.
2. In `ZoneLab-Data.bat`, choose **6**.
3. Open the dashboard and pick **My replays** in the left rail.

Expect warnings about coverage. Replays recorded on your machine only include players near you, which is why tournament server replays are the real source.

If processing fails on the current season, the parser hasn't caught up with the latest patch yet. Build it from its newest source instead. In PowerShell, from the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\pipeline\setup.ps1 -FromSource
```

## 3. Pull tournament data

1. **Choose 3** to list tournament windows that finished in the last 30 days. You can filter by region (EU, NAC…) and by name (cash, fncs…). Start narrow: **one season, one region**. Mixing seasons or regions early makes patterns harder to see.
2. **Choose 4** for each window you want, pasting its ID. It reads the window's leaderboard: every team's entry lists the matches it played, and their IDs are added to `data/match_ids.csv`. The default reads 10 pages (the top teams' lobbies); answer `all` to reach every lobby.
3. **Choose 5** to download and process. Start with **10 matches** to check the data. Then read the quality report (section 4) and run larger batches of 50–100. Each run skips matches it already has, so you can stop and resume safely.

**Storage.** Each tournament replay is about 130 MB (100 matches ≈ 13 GB). To keep them on another drive, choose **D** in `ZoneLab-Data.bat` and give a **new or empty** folder such as `D:\ZoneLabData`. Everything downloaded so far moves there, and the pipeline and dashboard use it from then on. The folder is saved in `.env` as `ZONELAB_DATA_DIR`.

**Smaller downloads.** Replays are downloaded without *checkpoints*, the periodic full snapshots a replay viewer uses to jump around the timeline. The parser never reads them, so results are identical. To watch a match in Fortnite's own replay viewer, download it with `node download.js --with-checkpoints` from `pipeline\node`.

**Deleting raw replays.** Option **K** can delete each raw replay once it's processed, keeping only the processed data (about 3 MB per match). Leave raw replays on while the pipeline is still changing: re-processing after an update needs them, and Epic only keeps tournament replays for about 30 days.

**After an update** that changes how replays are read, choose **7** to re-process everything you've already downloaded. Nothing is downloaded again.

**Strongest lobbies first.** Option 4 records which ranked players appeared in each match, so option 5 downloads the most stacked lobbies first: those with the most Power Rankings top-1,000 players (or, before Power Rankings are downloaded, the most top-leaderboard players). Option **V** previews the order. Download Power Rankings (option P) and re-run option 4 on your windows before big downloads, so the ordering has the best information.

**Drop spot names.** Choose **M** to download the current map's named places from fortnite-api.com (free, no key). The Drop spots page uses them to name each landing area. Re-run M at the start of each season, because the map changes.

**Weekly tier-1 routine.** Replays expire after about 30 days, so collect every week:
1. **P**: refresh Power Rankings (they update weekly).
2. **T**: weekly collection. It finds every high-tier window that ended in the last 7 days (FNCS, division cups, finals, cash cups, official events; mobile and Zero Build cups are skipped), collects their match IDs, downloads only lobbies with at least 3 Power Rankings top-1,000 players (strongest first), and processes them. LAN events whose players use event accounts won't pass the lobby filter: download those with **W**.

**In-match events.** Processed matches now include health and shields, damage (who hit whom, how hard), chest opens, every item spawned and picked up, the weapon in each player's hands, and player-built pieces. When a new season adds new build pieces, weapons or chests, run **S** on a new match, then **G**, then **1**.

**Power Rankings (recommended).** Choose **P** to download Epic's Power Rankings: the official cross-event skill rating for the top 10,000 players, read from the same Epic leaderboard service as match IDs (about 7 minutes). Every player then gets their PR rank and rating, and lobby strength becomes the share of each lobby in the PR top 1,000, which is comparable across rounds, regions and weeks. Rankings update weekly, so re-run P now and then.

**Session ranks (fallback).** Option 4 also saves every player's rank from the leaderboard pages it reads (`player_ranks.csv`). Each match then gets a *lobby strength*: the share of its players ranked in that tournament's top 1,000. The dashboard's **Lobby strength** filter keeps only strong lobbies. Use it for strategy questions (positioning, fights, height), where mixed-skill lobbies blur decisions with skill gaps; storm and bus questions can use every lobby. Read at least 10 pages so the top 1,000 is covered. To add ranks to matches you already have, run option 4 again on their window, then option 7.

**Pull tournaments within about 30 days.** Epic deletes tournament replays after roughly a month; option 4 warns when a window's matches are getting close to that.

If option 5 fails for every match, Epic has changed something on the free route. **Option 8** downloads the same replays through api-fortnite.com instead, at 2 credits per match. It needs a free api-fortnite.com key saved with **option 9** (the free tier gives 15 credits a day; paid credit packs and plans add more).

How much data each page needs before its conclusions are trustworthy (the dashboard checks this for you):

| Page | Recommended |
| --- | --- |
| Is the storm random? | 200+ matches from one season |
| Compare two periods | 100+ matches on each side |
| Position vs placement, High ground, Where eliminations happen | 100+ matches |
| Storm pull geometry | 100+ matches from one season |

## 4. Check quality before reading results

1. Open `data/reports/validation.md`. Every match is marked PASS, WARN or FAIL, with the reason. If more than 1 in 10 fail, stop and send me the report before adding more.
2. In the dashboard, pick **Tournaments**, then open **Dataset overview**:
   - **Server replays** should be 100%.
   - **Storm phases recorded per match** should show nearly every match at the same high count.
3. Check three matches by eye in Fortnite's own replay viewer. Confirm that the storm circles and one elimination per match match what the dashboard shows. This catches problems no automated check will.

## 5. Work through the pages in order

Each page now opens with **About this page** (the question, the method, how to conclude, limits). It then shows a **Conclusion** with three parts:

- the finding;
- the strength of each piece of evidence;
- a **Can you trust it?** checklist covering sample size, single season, replay source and real data.

Hover the **i** beside any number or test for its definition.

1. **Dataset overview.** Pick the season with the most matches. Filter the left rail to it and keep it there for steps 2 and 3.
2. **Is the storm random?** Read the per-match tests first; they carry the claim. Expand the phases to see when an effect appears.
3. **Storm pull geometry.** Size whatever step 2 found, for example how far the circle moves in the phase where a pattern appears.
4. **Compare two periods.** Put the season from step 2 in A and the next season in B. The pattern counts as replicated if it holds within both (the Within rows) and the A vs B rows show no difference.
5. **Position vs placement.** Find the phase where top-5 and 16th-or-lower teams stood most differently.
6. **High ground.** Find the first phase where fights won by the higher player turn significant.
7. **Where eliminations happen.** Check whether the phases from steps 5 and 6 are also where players outside the circle get eliminated.

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
| Option 2 says the client "has been disabled" | Epic switched off that login client. Close the menu, run `set EPIC_CLIENT=switch` in a Command Prompt in the project folder, start `ZoneLab-Data.bat` from that same window, and log in again. Tell me too, so the default can be updated |
| "Epic asked us to slow down" | The script waits and retries by itself. If it keeps happening, wait 10 minutes |
| Option 4 finds no match IDs | The window may not have been played yet, or its replays have expired |
| Option 5 shows "request failed" for every match | Use option 8, and send me the last lines of `data/raw/failed.txt` |
| Processing fails on new replays | Rebuild the parser from source (section 2) |
| Dashboard still shows "No tournament data yet" | Make sure option 5 finished, then refresh the page. If you used option D, restart `Start-ZoneLab.bat` |
| "The replay parser needs rebuilding after an update" | Run option 1, then option 7 |
