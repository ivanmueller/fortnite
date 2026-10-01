// epic_auth.js - log in to Epic once, then reuse the login for tournament lookups.
//
//   node epic_auth.js login     open a link, paste the code Epic shows, done
//   node epic_auth.js status    check the saved login still works
//   node epic_auth.js logout    revoke the saved login at Epic and delete it from this PC
//
// How it works (the same method as the fnbr.js library):
//   1. You log in on Epic's own page; the scripts never see your password.
//   2. Epic shows a one-time 32-character "authorization code" (valid 5 minutes).
//   3. That code is exchanged for a "device auth": a saved login stored only in
//      .epic-auth.json in the project folder (never uploaded to GitHub).
//   4. Later runs use the device auth to get a short-lived access token.
//
// Use a secondary Epic account, not your main one. Epic hasn't published these
// endpoints for outside use; reading public leaderboards slowly is low-risk, but
// keep your main account out of it.
import fs from 'fs';
import path from 'path';
import readline from 'readline';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..'); // repo root
export const AUTH_FILE = path.join(ROOT, '.epic-auth.json');
const ACCOUNT_BASE = process.env.EPIC_ACCOUNT_BASE || 'https://account-public-service-prod.ol.epicgames.com';

// Fortnite iOS client: one of the clients Epic allows device auths for (from fnbr.js / EpicResearch).
const CLIENT_ID = '3446cd72694c4a4485d81b77adbb2141';
const CLIENT_SECRET = '9209d4a5e25a457fb9b07489d313b41a';
const BASIC = `basic ${Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString('base64')}`;
export const LOGIN_URL = 'https://www.fortnite.com/id/login?redirectUrl='
  + encodeURIComponent(`https://www.epicgames.com/id/api/redirect?clientId=${CLIENT_ID}&responseType=code`);

export class EpicError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

async function epic(url, init = {}) {
  const res = await fetch(url, init);
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) {
    const code = body?.errorCode || '';
    const msg = body?.errorMessage || (typeof body === 'string' ? body.slice(0, 200) : '') || res.statusText;
    throw new EpicError(res.status, code, `Epic ${res.status}${code ? ` ${code}` : ''}: ${msg}`);
  }
  return body;
}

async function token(params) {
  return epic(`${ACCOUNT_BASE}/account/api/oauth/token`, {
    method: 'POST',
    headers: { Authorization: BASIC, 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ ...params, token_type: 'eg1' }).toString(),
  });
}

export function savedAuth() {
  try { return JSON.parse(fs.readFileSync(AUTH_FILE, 'utf8')); } catch { return null; }
}

let cached = null; // { accessToken, accountId, displayName, expiresAt }

/** A valid session from the saved device auth. Throws with guidance if there's no login. */
export async function session({ fresh = false } = {}) {
  if (!fresh && cached && Date.parse(cached.expiresAt) - Date.now() > 60_000) return cached;
  const auth = savedAuth();
  if (!auth) throw new EpicError(0, 'not_logged_in', 'Not logged in to Epic. Run ZoneLab-Data.bat option 2 first.');
  try {
    const t = await token({ grant_type: 'device_auth', account_id: auth.accountId, device_id: auth.deviceId, secret: auth.secret });
    cached = { accessToken: t.access_token, accountId: t.account_id, displayName: t.displayName || auth.displayName, expiresAt: t.expires_at };
    return cached;
  } catch (e) {
    if (e.code?.includes('invalid_grant') || e.status === 400) {
      throw new EpicError(e.status, e.code, `${e.message}\n  The saved login no longer works (revoked, or the password changed). Log in again with option 2.`);
    }
    throw e;
  }
}

function ask(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => rl.question(question, (a) => { rl.close(); resolve(a.trim()); }));
}

function extractCode(input) {
  const m = input.match(/[0-9a-f]{32}/i); // accepts the bare code or the whole page Epic shows
  return m ? m[0] : null;
}

async function login() {
  if (savedAuth()) {
    const again = await ask('You are already logged in. Log in again with a different account? (y/N) ');
    if (!/^y/i.test(again)) return status();
  }
  console.log('\n1. Open this link in your browser and log in with your SECONDARY Epic account:\n');
  console.log(`   ${LOGIN_URL}\n`);
  console.log('2. The page then shows some text containing "authorizationCode":"<32 letters and numbers>".');
  console.log('   Copy that code (or the whole text) and paste it here. It expires after 5 minutes.\n');
  const code = extractCode(await ask('Authorization code: '));
  if (!code) throw new EpicError(0, 'bad_code', 'That doesn\'t contain a 32-character code. Run option 2 again.');

  const t = await token({ grant_type: 'authorization_code', code });
  const device = await epic(`${ACCOUNT_BASE}/account/api/public/account/${t.account_id}/deviceAuth`, {
    method: 'POST', headers: { Authorization: `bearer ${t.access_token}` },
  });
  const saved = { accountId: device.accountId || t.account_id, deviceId: device.deviceId, secret: device.secret, displayName: t.displayName, created: new Date().toISOString() };
  fs.writeFileSync(AUTH_FILE, JSON.stringify(saved, null, 2), { mode: 0o600 });
  console.log(`\nLogged in as ${t.displayName || saved.accountId}. Login saved to .epic-auth.json (never uploaded to GitHub).`);
}

async function status() {
  const s = await session({ fresh: true });
  console.log(`Epic login works: ${s.displayName || s.accountId}.`);
}

async function logout() {
  const auth = savedAuth();
  if (!auth) return console.log('Not logged in; nothing to remove.');
  try {
    const s = await session({ fresh: true });
    await epic(`${ACCOUNT_BASE}/account/api/public/account/${auth.accountId}/deviceAuth/${auth.deviceId}`, {
      method: 'DELETE', headers: { Authorization: `bearer ${s.accessToken}` },
    });
    console.log('Saved login revoked at Epic.');
  } catch (e) {
    console.log(`Could not revoke at Epic (${e.message}). Removing it from this PC anyway.`);
  }
  fs.rmSync(AUTH_FILE, { force: true });
  console.log('Removed .epic-auth.json.');
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
if (isMain) {
  const cmd = process.argv[2];
  try {
    if (cmd === 'login') await login();
    else if (cmd === 'status') await status();
    else if (cmd === 'logout') await logout();
    else console.log('usage: node epic_auth.js login | status | logout');
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }
}
