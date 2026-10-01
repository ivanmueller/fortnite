# From real data to conclusions

This guide takes you from an empty dashboard to findings you can defend:

1. Get tournament replays in.
2. Check they're sound.
3. Work through the pages in the right order.
4. Decide what counts as a finding.

Everything on Windows runs from **`ZoneLab-Data.bat`** (double-click it in the project folder for a numbered menu) and **`Start-ZoneLab.bat`** (the dashboard).

## 1. One-time setup

1. Install the **.NET 10 SDK** from https://dotnet.microsoft.com/download. It's needed to read replay files.
2. Create a free account at https://api-fortnite.com (no card needed) and copy your API key from its dashboard. It's used only to find which matches a tournament contained.
3. In `ZoneLab-Data.bat`:
   - choose **1** (installs the replay parser);
   - then choose **2** and paste your key. It's saved to `.env`, which is never uploaded to GitHub, and checked straight away.

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

**Pull tournaments within about 30 days.** Epic deletes tournament replays after roughly a month; option 4 warns when a window's matches are getting close to that.

If option 5 fails for every match, Epic has changed something on the free route. **Option 8** downloads the same replays through api-fortnite.com instead, at 2 credits per match (the free tier gives 15 credits a day; paid credit packs and plans add more).

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
| Option 2, 3 or 4 says the key is missing or rejected | Run option 2 again and paste the key from your api-fortnite.com dashboard |
| Option 3 or 4 says an endpoint "is not on your plan" | Send me the message: api-fortnite.com moves endpoints between plans |
| Option 4 finds no match IDs | The window may not have been played yet, or its replays have expired |
| Option 5 shows "request failed" for every match | Use option 8, and send me the last lines of `data/raw/failed.txt` |
| Processing fails on new replays | Rebuild the parser from source (section 2) |
| Dashboard still shows "No tournament data yet" | Make sure option 5 finished, then refresh the page |
