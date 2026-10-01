// download.js - download tournament replays listed in data/match_ids.csv.
//
//   node download.js                         download every ID not already in data/raw (free, from Epic)
//   node download.js --limit 5               only the first 5 missing (good for a first test)
//   node download.js --via api-fortnite      download through api-fortnite.com instead
//                                            (2 credits per match; use if the Epic route fails)
//   node download.js --with-checkpoints      also download checkpoints (see below)
//   node download.js --plan                  show the download order and stop
//   node download.js --order list            download in list order instead of strongest lobbies first
//   node download.js --min-top1000 3         only matches with at least 3 Power Rankings top-1,000 players seen
//   node download.js --window <eventWindowId> download every match from one tournament window, in any order
//                                            (for events whose players aren't in Power Rankings, e.g. LAN accounts)
//
// By default the strongest lobbies come first: matches are ranked by how many Power
// Rankings top-1,000 players the leaderboards showed in them (or, before Power Rankings
// are downloaded, by how many top-leaderboard players appeared).
//
// Checkpoints are periodic full snapshots of the game that a replay viewer uses to jump
// around the timeline. The parser skips them entirely, so by default they aren't
// downloaded: smaller files, same results. Fortnite's own replay viewer may not open
// files without them; use --with-checkpoints if you want to watch a match in-game.
//
// Saves <id>.replay and <id>.meta.json to data/raw. Safe to re-run: finished
// matches are skipped, failures are logged to data/raw/failed.txt.
//
// No Epic account is needed: fortnite-replay-downloader v3 requests its own
// access token. If Epic ever revokes that, downloads will fail with an auth error.
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';
import downloader from 'fortnite-replay-downloader';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..'); // repo root
dotenv.config({ path: path.join(ROOT, '.env') });
const DATA = process.env.ZONELAB_DATA_DIR || path.join(ROOT, 'data'); // ZoneLab-Data.bat option D changes this
const RAW = path.join(DATA, 'raw');
const CSV = path.join(DATA, 'match_ids.csv');
fs.mkdirSync(RAW, { recursive: true });

const args = process.argv.slice(2);
const li = args.indexOf('--limit');
const limit = li >= 0 ? Number(args[li + 1]) : Infinity;
const PAUSE_MS = 3000;
const WITH_CHECKPOINTS = args.includes('--with-checkpoints');
const via = args.includes('--via') ? args[args.indexOf('--via') + 1] : 'epic';
const API_BASE = process.env.FORTNITE_API_BASE || 'https://prod.api-fortnite.com';
if (via === 'api-fortnite' && !process.env.FORTNITE_API_KEY) {
  console.error('FORTNITE_API_KEY is missing. Run ZoneLab-Data.bat option 2 first.');
  process.exit(1);
}

async function viaApiFortnite(matchId) {
  const res = await fetch(`${API_BASE}/api/v1/replays/${matchId}`, { headers: { 'x-api-key': process.env.FORTNITE_API_KEY } });
  if (!res.ok) {
    const body = (await res.text()).slice(0, 200);
    throw new Error(`api-fortnite ${res.status}: ${body}${res.status === 429 ? ' (daily credits used up)' : ''}`);
  }
  return Buffer.from(await res.arrayBuffer());
}

if (!fs.existsSync(CSV)) {
  console.error('match_ids.csv not found. Run find_matches.js first, or add IDs by hand.');
  process.exit(1);
}
const onlyWindow = args.includes('--window') ? args[args.indexOf('--window') + 1] : null;
const rowsAll = fs.readFileSync(CSV, 'utf8').split('\n').slice(1)
  .map((l) => l.trim()).filter((l) => l && !l.startsWith('#'))
  .map((l) => l.split(',').map((v) => v.trim()))
  .filter((c) => /^[0-9a-f]{32}$/i.test(c[0]));
const ids = rowsAll
  .filter((c) => !onlyWindow || (c[1] || '').toLowerCase() === onlyWindow.toLowerCase())
  .map((c) => c[0]);
if (onlyWindow) {
  const done = ids.filter((id) => fs.existsSync(path.join(RAW, `${id}.replay`))).length;
  console.log(`Window ${onlyWindow}: ${ids.length} matches in the list, ${done} already downloaded.`);
  if (!ids.length) {
    console.log('No match IDs for that window. Run option 4 on it first (the ID is case-sensitive in option 4, not here).');
    process.exit(1);
  }
}

// ---- strongest lobbies first
function readCsv(file) {
  if (!fs.existsSync(file)) return [];
  const [head, ...lines] = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean);
  const cols = head.split(',');
  return lines.map((l) => Object.fromEntries(l.split(',').map((v, i) => [cols[i], v])));
}
const prRank = new Map(readCsv(path.join(DATA, 'power_rankings.csv')).map((r) => [r.account_id, Number(r.pr_rank)]));
const strength = new Map(); // matchId -> [top1000, top10000, seen]
for (const r of readCsv(path.join(DATA, 'match_seen_players.csv'))) {
  const s = strength.get(r.match_id) || [0, 0, 0];
  const pr = prRank.get(r.account_id);
  if (pr && pr <= 1000) s[0] += 1;
  if (pr) s[1] += 1;
  s[2] += 1;
  strength.set(r.match_id, s);
}
const byStrength = !onlyWindow && (!args.includes('--order') || args[args.indexOf('--order') + 1] !== 'list');
const score = (id) => strength.get(id.toLowerCase()) || [0, 0, 0];
const waiting = ids.filter((id) => !fs.existsSync(path.join(RAW, `${id}.replay`)));
if (byStrength && strength.size) {
  // Stable sort: by PR top-1,000 count, then PR top-10,000, then leaderboard players seen.
  waiting.sort((a, b) => { const x = score(a), y = score(b); return (y[0] - x[0]) || (y[1] - x[1]) || (y[2] - x[2]); });
}
const minTop = args.includes('--min-top1000') ? Number(args[args.indexOf('--min-top1000') + 1]) : 0;
if (minTop > 0) {
  if (!prRank.size) {
    console.log('--min-top1000 needs Power Rankings: run data menu option P first. Ignoring the threshold.');
  } else {
    const before = waiting.length;
    for (let i = waiting.length - 1; i >= 0; i -= 1) if (score(waiting[i])[0] < minTop) waiting.splice(i, 1);
    console.log(`${waiting.length} of ${before} waiting matches have at least ${minTop} Power Rankings top-1,000 players.`);
  }
}
const todo = waiting.slice(0, limit);
if (onlyWindow && todo.length) {
  // Self-check: how many of each match's players are in Power Rankings? (LAN events often use event accounts.)
  console.log('match id                          leaderboard players seen  in Power Rankings  PR top-1,000');
  for (const id of todo) {
    const [a, b, c] = score(id);
    console.log(`${id}  ${String(c).padStart(24)}  ${String(b).padStart(17)}  ${String(a).padStart(12)}`);
  }
  const seen = todo.reduce((n, id) => n + score(id)[2], 0);
  const inPr = todo.reduce((n, id) => n + score(id)[1], 0);
  if (seen && inPr / seen < 0.2) {
    console.log('Few of these players are in Power Rankings: likely event accounts (common at LAN events). '
      + 'Lobby strength will read low for these matches; compare them by event window instead.');
  }
}
const basis = prRank.size ? 'Power Rankings top-1,000 players' : 'top-leaderboard players';
if (byStrength && strength.size && todo.length) {
  const pick = (id) => (prRank.size ? score(id)[0] : score(id)[2]);
  console.log(`Strongest lobbies first, by ${basis} seen in each match: this run ranges from ${pick(todo[0])} down to ${pick(todo[todo.length - 1])}.`);
} else if (byStrength && !strength.size) {
  console.log('No lobby information yet (re-run option 4 on your windows to record it); downloading in list order.');
}
if (args.includes('--plan')) {
  console.log(`\nNext ${Math.min(todo.length, 15)} of ${waiting.length} waiting matches:`);
  console.log('match id                          PR top-1,000  PR top-10,000  leaderboard players seen');
  for (const id of todo.slice(0, 15)) {
    const [a, b, c] = score(id);
    console.log(`${id}  ${String(a).padStart(12)}  ${String(b).padStart(13)}  ${String(c).padStart(24)}`);
  }
  process.exit(0);
}
console.log(`${ids.length} IDs listed, ${todo.length} to download ${via === 'api-fortnite' ? 'via api-fortnite.com (2 credits each)' : 'from Epic'}`);

let ok = 0;
for (const [n, matchId] of todo.entries()) {
  const t0 = Date.now();
  try {
    if (via === 'api-fortnite') {
      const buf = await viaApiFortnite(matchId);
      fs.writeFileSync(path.join(RAW, `${matchId}.replay`), buf);
      ok += 1;
      console.log(`[${n + 1}/${todo.length}] ok   ${matchId}  ${(buf.length / 1e6).toFixed(1)} MB  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
      if (n < todo.length - 1) await new Promise((r) => setTimeout(r, 500));
      continue;
    }
    const meta = await downloader.downloadMetadata({ matchId, chunkDownloadLinks: false });
    if (!meta) throw new Error('no metadata (match has no server replay, or it expired)');
    fs.writeFileSync(path.join(RAW, `${matchId}.meta.json`), JSON.stringify(meta, null, 2));
    if (meta.bIsLive) throw new Error('match is still live; try again later');

    const buf = await downloader.downloadReplay({
      matchId,
      maxConcurrentDownloads: 5,
      checkpointCount: WITH_CHECKPOINTS ? 1000 : 0,
      updateCallback: (d) => {
        const c = d.dataChunks || d.data || {};
        if (c.max) process.stdout.write(`  ${matchId}: data ${c.current}/${c.max}\r`);
      },
    });
    fs.writeFileSync(path.join(RAW, `${matchId}.replay`), buf);
    ok += 1;
    console.log(`[${n + 1}/${todo.length}] ok   ${matchId}  ${(buf.length / 1e6).toFixed(1)} MB  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  } catch (err) {
    const msg = err?.message || String(err);
    console.log(`[${n + 1}/${todo.length}] FAIL ${matchId}: ${msg}`);
    fs.appendFileSync(path.join(RAW, 'failed.txt'), `${new Date().toISOString()}\t${matchId}\t${msg}\n`);
  }
  if (n < todo.length - 1) await new Promise((r) => setTimeout(r, PAUSE_MS));
}
console.log(`done: ${ok}/${todo.length} downloaded`);
if (via === 'epic' && ok === 0 && todo.length) {
  console.log('Nothing downloaded from Epic. If every match failed the same way, try the fallback: '
    + 'ZoneLab-Data.bat option 8 (api-fortnite.com, 2 credits per match).');
}
