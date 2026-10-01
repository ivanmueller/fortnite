// find_matches.js - find tournament match IDs, straight from Epic's tournament service.
//
//   node find_matches.js test
//       Checks the Epic login works (log in first with: node epic_auth.js login).
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
//   node find_matches.js weekly [--days 7] [--region NAC,EU] [--include fncs,div,final,cash,official]
//                              [--exclude mobile,_zb,creative] [--pages 10]
//       Collects match IDs from every high-tier tournament window that ended in the last
//       --days days (FNCS, division cups, finals, cash cups, official events; mobile and
//       Zero Build cups left out by default). Same as running 'window' on each.
//
//   node find_matches.js powerrankings [--pages all|N] [--event <eventId> --window <windowId>]
//       Downloads Epic's Power Rankings (top 10,000 players, 25 per page) to
//       power_rankings.csv: real account IDs with PR rank and rating. Epic publishes PR
//       as a tournament leaderboard (event epicgames_dreamyparadox, window dreamyparadox,
//       as of Oct 2026; found in fortnite.com/competitive/power-rankings page data).
//
// Source: Epic directly (default; needs the one-time login from ZoneLab-Data.bat option 2).
// Add --source api-fortnite to use api-fortnite.com instead (Pro plan, FORTNITE_API_KEY in .env).
import dotenv from 'dotenv';
import { EpicError, session } from './epic_auth.js';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..'); // repo root
dotenv.config({ path: path.join(ROOT, '.env') });
const DATA = process.env.ZONELAB_DATA_DIR || path.join(ROOT, 'data'); // ZoneLab-Data.bat option D changes this
const CSV = path.join(DATA, 'match_ids.csv');
const TOURNAMENTS_CSV = path.join(DATA, 'tournaments.csv');
const RANKS_CSV = path.join(DATA, 'player_ranks.csv'); // leaderboard rank of every player read, per window
const RANKS_HEADER = 'event_window_id,account_id,rank,points,ranks_read';
const PR_CSV = path.join(DATA, 'power_rankings.csv');
const SEEN_CSV = path.join(DATA, 'match_seen_players.csv'); // which leaderboard players appeared in which match
const PR_EVENT = 'epicgames_dreamyparadox';
const PR_WINDOW = 'dreamyparadox';
const HEADER = 'match_id,event_window_id,region,session_date,is_server_replay,source';
const BASE = process.env.FORTNITE_API_BASE || 'https://prod.api-fortnite.com';
const KEY = process.env.FORTNITE_API_KEY;
const EVENTS_BASE = process.env.EPIC_EVENTS_BASE || 'https://events-public-service-live.ol.epicgames.com';
const SOURCE = (process.argv.includes('--source') ? process.argv[process.argv.indexOf('--source') + 1] : process.env.MATCH_SOURCE) || 'epic';
const PAUSE_MS = SOURCE === 'epic' ? 1000 : 250; // be gentle with Epic: one request per second

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
      401: 'The key was rejected. Save it again with ZoneLab-Data.bat option 9.',
      403: 'This endpoint is not on your plan.',
      429: 'Rate limit reached. Wait a while and run it again.',
    }[res.status];
    throw new ApiError(res.status, `${res.status} on ${url.pathname}: ${detail}${hint ? `\n  ${hint}` : ''}`);
  }
  const left = res.headers.get('x-ratelimit-remaining');
  if (left !== null && Number(left) < 50) console.log(`  (requests left today: ${left})`);
  return text ? JSON.parse(text) : null;
}

// Epic's events service, with one token refresh on 401 and patient retries on 429.
async function epicGet(apiPath) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const s = await session({ fresh: attempt > 0 });
    const url = `${EVENTS_BASE}${apiPath.replaceAll('{me}', s.accountId)}`;
    const res = await fetch(url, { headers: { Authorization: `bearer ${s.accessToken}`, accept: 'application/json' } });
    const text = await res.text();
    if (res.ok) return text ? JSON.parse(text) : null;
    let body = {};
    try { body = JSON.parse(text); } catch { /* not JSON */ }
    if (res.status === 401 && attempt === 0) continue;
    if (res.status === 429 && attempt < 3) {
      const wait = 30 * (attempt + 1);
      console.log(`  Epic asked us to slow down; waiting ${wait}s...`);
      await sleep(wait * 1000);
      continue;
    }
    throw new ApiError(res.status, `Epic ${res.status} ${body.errorCode || ''}: ${body.errorMessage || text.slice(0, 200)}`);
  }
  throw new ApiError(429, 'Epic kept rate-limiting. Wait 10 minutes and try again.');
}

function prettyEventName(eventId) {
  return eventId.replace(/^epicgames_/, '').replace(/_/g, ' ');
}

/** All tournament windows the source knows, past and current: {name, region, eventId, eventWindowId, begin, end, round}. */
async function listAllWindows({ upcoming = false } = {}) {
  if (SOURCE === 'api-fortnite') {
    return flattenWindows(await get(upcoming ? '/api/v1/events/global' : '/api/v1/events/global/history'));
  }
  const data = await epicGet('/api/v1/events/Fortnite/data/{me}?showPastEvents=true');
  const rows = [];
  for (const ev of data?.events || []) {
    const region = (ev.regions && ev.regions[0]) || (ev.eventId.match(/_(NAC|NAE|NAW|EU|BR|ASIA|OCE|ME)$/i) || [])[1] || '';
    for (const w of ev.eventWindows || []) {
      rows.push({ name: prettyEventName(ev.eventId), region, eventId: ev.eventId, eventWindowId: w.eventWindowId,
                  begin: w.beginTime, end: w.endTime, round: w.round });
    }
  }
  return rows.filter((r) => r.eventId && r.eventWindowId);
}

/** One leaderboard page: {totalPages, entries[{sessionHistory[{sessionId, endTime}]}]}. */
async function leaderboardPage(eventId, eventWindowId, page) {
  if (SOURCE === 'api-fortnite') {
    return get('/api/v1/events/global/leaderboard', { eventId, eventWindowId, page });
  }
  const q = new URLSearchParams({ page, rank: 0, teamAccountIds: '', appId: 'Fortnite', showLiveSessions: 'false' });
  return epicGet(`/api/v1/leaderboards/Fortnite/${encodeURIComponent(eventId)}/${encodeURIComponent(eventWindowId)}/{me}?${q}`);
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
  const now = Date.now();
  const since = now - days * 86400000;
  let rows = await listAllWindows({ upcoming });
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
  console.log(`Source: ${SOURCE === 'epic' ? 'Epic' : 'api-fortnite.com'}\n`);
  console.log('ended (UTC)        region  window ID  |  tournament');
  for (const r of rows.slice(0, 80)) {
    console.log(`${(r.end || '').slice(0, 16).replace('T', ' ').padEnd(18)} ${r.region.padEnd(7)} ${r.eventWindowId}  |  ${r.name}`);
  }
  if (rows.length > 80) console.log(`... and ${rows.length - 80} more (narrow with --region or --search).`);
  console.log(`\nCopy a window ID into option 4. Full list saved to ${path.relative(ROOT, TOURNAMENTS_CSV)}.`);
}

// ---------------------------------------------------------------- one window
async function findEventId(windowId) {
  const hit = (await listAllWindows()).find((r) => r.eventWindowId === windowId);
  return hit ? { eventId: hit.eventId, region: hit.region } : null;
}

function readExisting() {
  fs.mkdirSync(DATA, { recursive: true });
  if (!fs.existsSync(CSV)) fs.writeFileSync(CSV, `${HEADER}\n`);
  return new Set(fs.readFileSync(CSV, 'utf8').split('\n').slice(1)
    .filter((l) => l && !l.startsWith('#')).map((l) => l.split(',')[0].trim().toLowerCase()));
}

async function weekly() {
  const days = Number(opt('days', 7));
  const list = (v, d) => (typeof v === 'string' ? v : d).split(',').map((x) => x.trim().toLowerCase()).filter(Boolean);
  const include = list(opt('include'), 'fncs,div,final,cash,official,champion');
  const exclude = list(opt('exclude'), 'mobile,_zb,zb_,creative,ranked');
  const regions = list(opt('region'), '').map((r) => r.toUpperCase());
  const now = Date.now();
  const wins = (await listAllWindows()).filter((w) => {
    const end = Date.parse(w.end);
    const id = `${w.eventId} ${w.eventWindowId}`.toLowerCase();
    return Number.isFinite(end) && end <= now && end >= now - days * 86400000
      && include.some((k) => id.includes(k)) && !exclude.some((k) => id.includes(k))
      && (!regions.length || regions.includes(String(w.region).toUpperCase()));
  });
  const unique = [...new Map(wins.map((w) => [w.eventWindowId, w])).values()]
    .sort((a, b) => Date.parse(a.end) - Date.parse(b.end));
  if (!unique.length) {
    console.log(`No high-tier windows ended in the last ${days} days (include: ${include.join(', ')}; exclude: ${exclude.join(', ')}).`);
    return;
  }
  console.log(`${unique.length} high-tier windows ended in the last ${days} days:`);
  for (const w of unique) console.log(`  ${w.end.slice(0, 10)}  ${String(w.region).padEnd(5)} ${w.eventWindowId}`);
  for (const w of unique) {
    console.log(`\n== ${w.eventWindowId}`);
    try {
      await collectWindow(w.eventWindowId, w.eventId);
    } catch (e) {
      console.log(`  skipped: ${e.message}`);
    }
  }
}

async function collectWindow(windowId, knownEventId) {
  let eventId = knownEventId || (typeof opt('event') === 'string' ? opt('event') : null);
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
  const ranks = new Map();    // accountId -> { rank, points }
  let page = 0;
  let total = 1;
  while (page < total && page < maxPages) {
    const board = await leaderboardPage(eventId, windowId, page);
    total = board?.totalPages ?? 1;
    for (const entry of board?.entries || []) {
      for (const acc of entry.teamAccountIds || []) {
        const id = String(acc).toLowerCase();
        if (!ranks.has(id) || entry.rank < ranks.get(id).rank) {
          ranks.set(id, { rank: entry.rank, points: entry.pointsEarned ?? entry.score ?? '' });
        }
      }
      for (const s of entry.sessionHistory || []) {
        if (!s.sessionId) continue;
        const cur = sessions.get(s.sessionId) || { end: s.endTime, teams: 0, players: new Set() };
        cur.teams += 1;
        for (const acc of entry.teamAccountIds || []) cur.players.add(String(acc).toLowerCase());
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

  saveRanks(windowId, ranks);
  saveSeen(windowId, sessions);
  const existing = readExisting();
  const rows = [];
  for (const [id, s] of sessions) {
    if (existing.has(id.toLowerCase())) continue;
    rows.push([id, windowId, region, (s.end || '').slice(0, 10), 1, SOURCE].join(','));
  }
  if (rows.length) fs.appendFileSync(CSV, `${rows.join('\n')}\n`);
  console.log(`Match list: ${CSV}`);
  const teams = [...sessions.values()].map((s) => s.teams).sort((a, b) => a - b);
  console.log(`window ${windowId}: ${sessions.size} matches across ${Math.min(page, total)} of ${total} leaderboard pages;`
    + ` added ${rows.length} new IDs to ${path.relative(ROOT, CSV) || CSV}.`);
  console.log(`teams seen per match on these pages: median ${teams[Math.floor(teams.length / 2)]}.`
    + (page < total ? ' Use --pages all to reach every lobby.' : ''));
  const oldest = Math.min(...[...sessions.values()].map((s) => Date.parse(s.end)).filter(Number.isFinite));
  if (Number.isFinite(oldest) && Date.now() - oldest > 25 * 86400000) {
    console.log('Note: some matches are over 25 days old. Epic keeps tournament replays for about 30 days, so download them soon.');
  }
}

/** Download Epic's Power Rankings leaderboard to power_rankings.csv. */
async function downloadPowerRankings() {
  const eventId = typeof opt('event') === 'string' ? opt('event') : PR_EVENT;
  const windowId = typeof opt('window') === 'string' ? opt('window') : PR_WINDOW;
  const pagesOpt = opt('pages', 'all');
  const maxPages = pagesOpt === 'all' || pagesOpt === true ? Infinity : Number(pagesOpt);
  const rows = new Map(); // accountId -> { rank, score, points }
  let page = 0;
  let total = 1;
  while (page < total && page < maxPages) {
    const board = await leaderboardPage(eventId, windowId, page);
    total = board?.totalPages ?? 1;
    const entries = board?.entries || [];
    if (page === 0 && entries.length && entries[0].score === undefined && entries[0].pointsEarned === undefined) {
      console.log('Unexpected entry format; first entry:', JSON.stringify(entries[0]).slice(0, 400));
    }
    for (const e of entries) {
      for (const acc of e.teamAccountIds || []) {
        const id = String(acc).toLowerCase();
        if (!rows.has(id)) rows.set(id, { rank: e.rank, score: e.score ?? '', points: e.pointsEarned ?? '' });
      }
    }
    page += 1;
    process.stdout.write(`  power rankings page ${page}/${Number.isFinite(maxPages) ? Math.min(total, maxPages) : total}, players: ${rows.size}\r`);
    await sleep(PAUSE_MS);
  }
  console.log();
  if (!rows.size) {
    console.log('No players returned. The Power Rankings event may have changed name: open the Power Rankings page source '
      + '(Ctrl+U), search for "eventId", and run with --event <eventId> --window <eventWindowId>.');
    return;
  }
  fs.mkdirSync(DATA, { recursive: true });
  const fetched = new Date().toISOString().slice(0, 10);
  fs.writeFileSync(PR_CSV, ['account_id,pr_rank,pr_score,pr_points,fetched', ...[...rows]
    .sort((a, b) => a[1].rank - b[1].rank)
    .map(([id, r]) => [id, r.rank, r.score, r.points, fetched].join(','))].join('\n') + '\n');
  const top = [...rows.values()].sort((a, b) => a.rank - b.rank).slice(0, 3);
  console.log(`saved ${rows.size} players to ${path.relative(ROOT, PR_CSV) || PR_CSV}`);
  console.log('top 3 (compare with fortnite.com/competitive/power-rankings):',
    top.map((r) => `#${r.rank} rating ${r.score || r.points}`).join(', '));
}

/** Replace this window's rows in match_seen_players.csv: who from the leaderboard played in each match. */
function saveSeen(windowId, sessions) {
  const keep = fs.existsSync(SEEN_CSV)
    ? fs.readFileSync(SEEN_CSV, 'utf8').split('\n').slice(1).filter((l) => l && l.split(',')[2] !== windowId)
    : [];
  const rows = [];
  for (const [id, s] of sessions) for (const acc of s.players) rows.push([id.toLowerCase(), acc, windowId].join(','));
  fs.writeFileSync(SEEN_CSV, ['match_id,account_id,event_window_id', ...keep, ...rows].join('\n') + '\n');
}

/** Replace this window's rows in player_ranks.csv with the ranks just read. */
function saveRanks(windowId, ranks) {
  if (!ranks.size) return;
  fs.mkdirSync(DATA, { recursive: true });
  const keep = fs.existsSync(RANKS_CSV)
    ? fs.readFileSync(RANKS_CSV, 'utf8').split('\n').slice(1).filter((l) => l && l.split(',')[0] !== windowId)
    : [];
  const read = Math.max(...[...ranks.values()].map((r) => r.rank));
  const rows = [...ranks].map(([id, r]) => [windowId, id, r.rank, r.points, read].join(','));
  fs.writeFileSync(RANKS_CSV, [RANKS_HEADER, ...keep, ...rows].join('\n') + '\n');
  console.log(`saved leaderboard ranks for ${ranks.size} players (ranks 1-${read}) to ${path.relative(ROOT, RANKS_CSV) || RANKS_CSV}`);
}

// ---------------------------------------------------------------- main
if (SOURCE === 'api-fortnite' && !KEY) {
  console.error('FORTNITE_API_KEY is missing. Save your api-fortnite.com key with ZoneLab-Data.bat option 9.');
  process.exit(1);
}
try {
  if (args[0] === 'test') {
    if (SOURCE === 'api-fortnite') {
      const season = await get('/api/v1/season');
      console.log('api-fortnite.com key works. Current season:', JSON.stringify(season).slice(0, 200));
    } else {
      const s = await session({ fresh: true });
      console.log(`Epic login works: ${s.displayName || s.accountId}.`);
    }
  } else if (args[0] === 'tournaments') await listTournaments();
  else if (args[0] === 'powerrankings') await downloadPowerRankings();
  else if (args[0] === 'weekly') await weekly();
  else if (args[0] === 'window' && args[1] && !args[1].startsWith('--')) await collectWindow(args[1]);
  else {
    console.log('usage:\n  node find_matches.js test\n  node find_matches.js tournaments [--region EU] [--search text] [--days 30] [--upcoming]\n'
      + '  node find_matches.js window <eventWindowId> [--event <eventId>] [--pages 10|all]\n'
      + '  node find_matches.js powerrankings [--pages all|N]');
  }
} catch (e) {
  console.error(e instanceof ApiError || e instanceof EpicError ? e.message : `Error: ${e.message}`);
  process.exit(1);
}
