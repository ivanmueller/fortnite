// find_matches.js - find tournament match IDs with api-fortnite.com.
//
//   node find_matches.js test
//       Checks your key works (one free request).
//
//   node find_matches.js tournaments [--region EU] [--search cash] [--days 30] [--upcoming]
//       Lists tournament windows that finished in the last --days days (default 30,
//       because Epic keeps tournament replays for about 30 days). Newest first.
//       Also saves the full list to data/tournaments.csv.
//
//   node find_matches.js window <eventWindowId> [--event <eventId>] [--pages 10 | --pages all]
//       Reads that window's leaderboard. Every team's sessionHistory lists the match
//       IDs (sessionId) it played; new IDs are appended to data/match_ids.csv.
//       Top pages hold the top teams. Their matches cover the strongest lobbies;
//       --pages all also reaches lower lobbies, at one request per page.
//
// Key: FORTNITE_API_KEY in the project's .env (ZoneLab-Data.bat option 2 saves it).
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..'); // repo root
dotenv.config({ path: path.join(ROOT, '.env') });
const CSV = path.join(ROOT, 'data', 'match_ids.csv');
const TOURNAMENTS_CSV = path.join(ROOT, 'data', 'tournaments.csv');
const HEADER = 'match_id,event_window_id,region,session_date,is_server_replay,source';
const BASE = process.env.FORTNITE_API_BASE || 'https://prod.api-fortnite.com';
const KEY = process.env.FORTNITE_API_KEY;
const PAUSE_MS = 250; // gentle pacing between requests

const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  if (i < 0) return dflt;
  const v = args[i + 1];
  return v && !v.startsWith('--') ? v : true;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class ApiError extends Error {
  constructor(status, msg) { super(msg); this.status = status; }
}

async function get(apiPath, query = {}) {
  const url = new URL(apiPath, BASE);
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null) url.searchParams.set(k, v);
  const res = await fetch(url, { headers: { 'x-api-key': KEY, accept: 'application/json' } });
  const text = await res.text();
  if (!res.ok) {
    let detail = text.slice(0, 300);
    try { const j = JSON.parse(text); detail = j.detail || j.title || j.message || detail; } catch { /* not JSON */ }
    const hint = {
      401: 'The key was rejected. Save it again with ZoneLab-Data.bat option 2.',
      403: 'This endpoint is not on your plan.',
      429: 'Rate limit reached. Wait a while and run it again.',
    }[res.status];
    throw new ApiError(res.status, `${res.status} on ${url.pathname}: ${detail}${hint ? `\n  ${hint}` : ''}`);
  }
  const left = res.headers.get('x-ratelimit-remaining');
  if (left !== null && Number(left) < 50) console.log(`  (requests left today: ${left})`);
  return text ? JSON.parse(text) : null;
}

// ---------------------------------------------------------------- tournaments
function flattenWindows(events) {
  const rows = [];
  for (const ev of events || []) {
    for (const [regionKey, list] of Object.entries(ev.regions || {})) {
      for (const epic of list || []) {
        for (const w of epic.eventWindows || []) {
          rows.push({
            name: ev.name || ev.shortTitle || ev.titleLine1 || epic.eventId,
            region: regionKey,
            eventId: epic.eventId,
            eventWindowId: w.eventWindowId,
            begin: w.beginTime,
            end: w.endTime,
            round: w.round,
          });
        }
      }
    }
  }
  return rows.filter((r) => r.eventId && r.eventWindowId);
}

async function listTournaments() {
  const days = Number(opt('days', 30));
  const region = opt('region');
  const search = opt('search');
  const upcoming = opt('upcoming', false) === true;
  const events = await get(upcoming ? '/api/v1/events/global' : '/api/v1/events/global/history');
  const now = Date.now();
  const since = now - days * 86400000;
  let rows = flattenWindows(events);
  fs.mkdirSync(path.dirname(TOURNAMENTS_CSV), { recursive: true });
  fs.writeFileSync(TOURNAMENTS_CSV, ['end,region,name,event_id,event_window_id,round',
    ...rows.map((r) => [r.end, r.region, `"${(r.name || '').replace(/"/g, "'")}"`, r.eventId, r.eventWindowId, r.round].join(','))].join('\n') + '\n');

  rows = rows.filter((r) => {
    const end = Date.parse(r.end);
    if (upcoming) return !Number.isFinite(end) || end >= now;
    return Number.isFinite(end) && end <= now && end >= since;
  });
  if (typeof region === 'string') rows = rows.filter((r) => r.region.toUpperCase() === region.toUpperCase());
  if (typeof search === 'string') {
    const q = search.toLowerCase();
    rows = rows.filter((r) => `${r.name} ${r.eventId} ${r.eventWindowId}`.toLowerCase().includes(q));
  }
  rows.sort((a, b) => Date.parse(b.end) - Date.parse(a.end));
  if (!rows.length) {
    console.log(upcoming ? 'No upcoming windows match.' : `No windows finished in the last ${days} days match. Try --days 60 or drop --region/--search.`);
    return;
  }
  console.log('ended (UTC)        region  window ID  |  tournament');
  for (const r of rows.slice(0, 80)) {
    console.log(`${(r.end || '').slice(0, 16).replace('T', ' ').padEnd(18)} ${r.region.padEnd(7)} ${r.eventWindowId}  |  ${r.name}`);
  }
  if (rows.length > 80) console.log(`... and ${rows.length - 80} more (narrow with --region or --search).`);
  console.log(`\nCopy a window ID into option 4. Full list saved to ${path.relative(ROOT, TOURNAMENTS_CSV)}.`);
}

// ---------------------------------------------------------------- one window
async function findEventId(windowId) {
  const events = await get('/api/v1/events/global/history');
  const hit = flattenWindows(events).find((r) => r.eventWindowId === windowId);
  return hit ? { eventId: hit.eventId, region: hit.region } : null;
}

function readExisting() {
  if (!fs.existsSync(CSV)) fs.writeFileSync(CSV, `${HEADER}\n`);
  return new Set(fs.readFileSync(CSV, 'utf8').split('\n').slice(1)
    .filter((l) => l && !l.startsWith('#')).map((l) => l.split(',')[0].trim().toLowerCase()));
}

async function collectWindow(windowId) {
  let eventId = typeof opt('event') === 'string' ? opt('event') : null;
  let region = null;
  if (!eventId) {
    const found = await findEventId(windowId);
    if (!found) throw new Error(`Window ${windowId} not found in the tournament list. Pass its event too: --event <eventId>`);
    ({ eventId, region } = found);
  }
  region = region || (windowId.match(/_(NAC|NAE|NAW|EU|BR|ASIA|OCE|ME)$/i) || [])[1] || '';
  const pagesOpt = opt('pages', '10');
  const maxPages = pagesOpt === 'all' ? Infinity : Number(pagesOpt);

  const sessions = new Map(); // sessionId -> { end, teams }
  let page = 0;
  let total = 1;
  while (page < total && page < maxPages) {
    const board = await get('/api/v1/events/global/leaderboard', { eventId, eventWindowId: windowId, page });
    total = board?.totalPages ?? 1;
    for (const entry of board?.entries || []) {
      for (const s of entry.sessionHistory || []) {
        if (!s.sessionId) continue;
        const cur = sessions.get(s.sessionId) || { end: s.endTime, teams: 0 };
        cur.teams += 1;
        sessions.set(s.sessionId, cur);
      }
    }
    page += 1;
    process.stdout.write(`  leaderboard page ${page}/${Number.isFinite(maxPages) ? Math.min(total, maxPages) : total}, matches found: ${sessions.size}\r`);
    await sleep(PAUSE_MS);
  }
  console.log();
  if (!sessions.size) {
    console.log('No match IDs in this leaderboard. The window may not have started, or the response has no sessionHistory.');
    return;
  }

  const existing = readExisting();
  const rows = [];
  for (const [id, s] of sessions) {
    if (existing.has(id.toLowerCase())) continue;
    rows.push([id, windowId, region, (s.end || '').slice(0, 10), 1, 'api-fortnite'].join(','));
  }
  if (rows.length) fs.appendFileSync(CSV, `${rows.join('\n')}\n`);
  const teams = [...sessions.values()].map((s) => s.teams).sort((a, b) => a - b);
  console.log(`window ${windowId}: ${sessions.size} matches across ${Math.min(page, total)} of ${total} leaderboard pages;`
    + ` added ${rows.length} new IDs to data/match_ids.csv.`);
  console.log(`teams seen per match on these pages: median ${teams[Math.floor(teams.length / 2)]}.`
    + (page < total ? ' Use --pages all to reach every lobby.' : ''));
  const oldest = Math.min(...[...sessions.values()].map((s) => Date.parse(s.end)).filter(Number.isFinite));
  if (Number.isFinite(oldest) && Date.now() - oldest > 25 * 86400000) {
    console.log('Note: some matches are over 25 days old. Epic keeps tournament replays for about 30 days, so download them soon.');
  }
}

// ---------------------------------------------------------------- main
if (!KEY) {
  console.error('FORTNITE_API_KEY is missing. Run ZoneLab-Data.bat option 2 to save your api-fortnite.com key.');
  process.exit(1);
}
try {
  if (args[0] === 'test') {
    const season = await get('/api/v1/season');
    console.log('Key works. Current season:', JSON.stringify(season).slice(0, 200));
  } else if (args[0] === 'tournaments') await listTournaments();
  else if (args[0] === 'window' && args[1] && !args[1].startsWith('--')) await collectWindow(args[1]);
  else {
    console.log('usage:\n  node find_matches.js test\n  node find_matches.js tournaments [--region EU] [--search text] [--days 30] [--upcoming]\n'
      + '  node find_matches.js window <eventWindowId> [--event <eventId>] [--pages 10|all]');
  }
} catch (e) {
  console.error(e instanceof ApiError ? e.message : `Error: ${e.message}`);
  process.exit(1);
}
