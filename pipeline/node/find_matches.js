// find_matches.js - collect tournament match IDs from the Osirion API.
//
//   node find_matches.js tournaments [--season 37] [--limit 30]
//       Lists recent tournament event windows so you can pick one.
//
//   node find_matches.js window <eventWindowId> [--players 60]
//       Takes the top players from that event window, pulls their matches,
//       keeps only matches tagged with that window, and appends new IDs to
//       data/match_ids.csv. Server-recorded replays are preferred.
//
// Every Osirion call costs credits; credits are printed before and after.
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { OsirionClient } from '@osirion/api';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..'); // repo root
const CSV = path.join(ROOT, 'data', 'match_ids.csv');
dotenv.config({ path: path.join(ROOT, '.env') });
const HEADER = 'match_id,event_window_id,region,session_date,is_server_replay,source';
const REGIONS = ['NAC', 'NAE', 'NAW', 'EU', 'BR', 'ASIA', 'OCE', 'ME'];

const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : dflt;
};

if (!process.env.OSIRION_API_KEY) {
  console.error('OSIRION_API_KEY is missing. Copy .env.example to .env and add your key.');
  process.exit(1);
}
const osirion = new OsirionClient(process.env.OSIRION_API_KEY);

const regionOf = (windowId = '') => REGIONS.find((r) => windowId.toUpperCase().split('_').includes(r)) || '';

function readExisting() {
  if (!fs.existsSync(CSV)) fs.writeFileSync(CSV, `${HEADER}\n`);
  return new Set(
    fs.readFileSync(CSV, 'utf8').split('\n').slice(1)
      .filter((l) => l && !l.startsWith('#')).map((l) => l.split(',')[0]),
  );
}

async function listTournaments() {
  const season = opt('season');
  const limit = Number(opt('limit', 30));
  const res = await osirion.getTournaments({ ...(season ? { season: Number(season) } : {}), limit });
  const rows = (Array.isArray(res) ? res : res?.tournaments || [])
    .sort((a, b) => (b.startTime || 0) - (a.startTime || 0));
  console.log('start (UTC)          matches  parsed  eventWindowId');
  for (const t of rows) {
    const when = t.startTime ? new Date(t.startTime * (t.startTime < 1e12 ? 1000 : 1)).toISOString().slice(0, 16) : '?';
    const pct = t.parsingProgress != null ? `${Math.round(t.parsingProgress * (t.parsingProgress <= 1 ? 100 : 1))}%` : '-';
    console.log(`${when.padEnd(20)} ${String(t.totalMatches ?? '-').padStart(7)}  ${pct.padStart(6)}  ${t.eventWindowId}`);
  }
}

async function collectWindow(windowId) {
  const nPlayers = Number(opt('players', 60));
  const stats = await osirion.getTournamentPlayerStats({ eventWindowId: windowId, limit: nPlayers });
  const players = (Array.isArray(stats) ? stats : stats?.players || []).map((p) => p.epicId).filter(Boolean);
  console.log(`window ${windowId}: ${players.length} players`);
  if (!players.length) return;

  const existing = readExisting();
  const found = new Map();
  for (let i = 0; i < players.length; i += 10) {
    const batch = players.slice(i, i + 10);
    let matches = [];
    try {
      matches = await osirion.getMatches(batch, { limit: 100 });
    } catch (err) {
      console.error(`  getMatches failed for batch ${i / 10 + 1}: ${err.message}`);
      continue;
    }
    for (const m of matches || []) {
      const info = m.info || {};
      if (info.eventWindowId !== windowId || !info.matchId) continue;
      found.set(info.matchId, info);
    }
    process.stdout.write(`  players ${Math.min(i + 10, players.length)}/${players.length}, matches so far: ${found.size}\r`);
  }
  console.log();

  const rows = [];
  for (const [id, info] of found) {
    if (existing.has(id)) continue;
    const date = info.startTimestamp
      ? new Date(info.startTimestamp * (info.startTimestamp < 1e12 ? 1000 : 1)).toISOString().slice(0, 10) : '';
    rows.push([id, windowId, regionOf(windowId), date, info.isServerReplay ? 1 : 0, 'osirion'].join(','));
  }
  // Server replays first: they contain every player, which the research needs.
  rows.sort((a, b) => Number(b.split(',')[4]) - Number(a.split(',')[4]));
  if (rows.length) fs.appendFileSync(CSV, `${rows.join('\n')}\n`);
  const server = [...found.values()].filter((i) => i.isServerReplay).length;
  console.log(`found ${found.size} matches (${server} server-recorded), added ${rows.length} new IDs to match_ids.csv`);
}

const before = await osirion.getCredits().catch(() => null);
console.log(`credits before: ${before ?? '?'}`);
try {
  if (args[0] === 'tournaments') await listTournaments();
  else if (args[0] === 'window' && args[1]) await collectWindow(args[1]);
  else {
    console.log('usage:\n  node find_matches.js tournaments [--season N] [--limit 30]\n  node find_matches.js window <eventWindowId> [--players 60]');
  }
} finally {
  const after = await osirion.getCredits().catch(() => null);
  console.log(`credits after:  ${after ?? '?'}${before != null && after != null ? `  (used ${before - after})` : ''}`);
}
