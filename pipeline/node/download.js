// download.js - download tournament replays listed in data/match_ids.csv.
//
//   node download.js                         download every ID not already in data/raw (free, from Epic)
//   node download.js --limit 5               only the first 5 missing (good for a first test)
//   node download.js --via api-fortnite      download through api-fortnite.com instead
//                                            (2 credits per match; use if the Epic route fails)
//   node download.js --with-checkpoints      also download checkpoints (see below)
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
const ids = fs.readFileSync(CSV, 'utf8').split('\n').slice(1)
  .map((l) => l.trim()).filter((l) => l && !l.startsWith('#'))
  .map((l) => l.split(',')[0].trim())
  .filter((id) => /^[0-9a-f]{32}$/i.test(id));

const todo = ids.filter((id) => !fs.existsSync(path.join(RAW, `${id}.replay`))).slice(0, limit);
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
