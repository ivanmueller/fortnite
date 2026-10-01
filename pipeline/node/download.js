// download.js - download tournament replays listed in data/match_ids.csv.
//
//   node download.js            download every ID not already in data/raw
//   node download.js --limit 5  only the first 5 missing (good for a first test)
//
// Saves <id>.replay and <id>.meta.json to data/raw. Safe to re-run: finished
// matches are skipped, failures are logged to data/raw/failed.txt.
//
// No Epic account is needed: fortnite-replay-downloader v3 requests its own
// access token. If Epic ever revokes that, downloads will fail with an auth error.
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import downloader from 'fortnite-replay-downloader';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..'); // repo root
const RAW = path.join(ROOT, 'data', 'raw');
const CSV = path.join(ROOT, 'data', 'match_ids.csv');
fs.mkdirSync(RAW, { recursive: true });

const args = process.argv.slice(2);
const li = args.indexOf('--limit');
const limit = li >= 0 ? Number(args[li + 1]) : Infinity;
const PAUSE_MS = 3000;

if (!fs.existsSync(CSV)) {
  console.error('match_ids.csv not found. Run find_matches.js first, or add IDs by hand.');
  process.exit(1);
}
const ids = fs.readFileSync(CSV, 'utf8').split('\n').slice(1)
  .map((l) => l.trim()).filter((l) => l && !l.startsWith('#'))
  .map((l) => l.split(',')[0].trim())
  .filter((id) => /^[0-9a-f]{32}$/i.test(id));

const todo = ids.filter((id) => !fs.existsSync(path.join(RAW, `${id}.replay`))).slice(0, limit);
console.log(`${ids.length} IDs listed, ${todo.length} to download`);

let ok = 0;
for (const [n, matchId] of todo.entries()) {
  const t0 = Date.now();
  try {
    const meta = await downloader.downloadMetadata({ matchId, chunkDownloadLinks: false });
    if (!meta) throw new Error('no metadata (match has no server replay, or it expired)');
    fs.writeFileSync(path.join(RAW, `${matchId}.meta.json`), JSON.stringify(meta, null, 2));
    if (meta.bIsLive) throw new Error('match is still live; try again later');

    const buf = await downloader.downloadReplay({
      matchId,
      maxConcurrentDownloads: 5,
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
