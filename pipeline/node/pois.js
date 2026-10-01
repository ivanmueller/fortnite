// pois.js - download the current map's named places from fortnite-api.com (free, no key)
// into pois.csv in the data folder. Re-run at the start of each season: maps change.
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
dotenv.config({ path: path.join(ROOT, '.env') });
const DATA = process.env.ZONELAB_DATA_DIR || path.join(ROOT, 'data');
const OUT = path.join(DATA, 'pois.csv');
const URL = process.env.FORTNITE_MAP_URL || 'https://fortnite-api.com/v1/map';

const res = await fetch(URL);
if (!res.ok) {
  console.error(`fortnite-api.com answered ${res.status}. Try again later.`);
  process.exit(1);
}
const pois = (await res.json())?.data?.pois || [];
if (!pois.length) {
  console.error('No places in the response.');
  process.exit(1);
}
const q = (s) => `"${String(s).replace(/"/g, "'")}"`;
const today = new Date().toISOString().slice(0, 10);
const rows = pois.map((p) => [q(p.name), p.location.x, p.location.y, p.location.z,
  /\.POI\./.test(p.id) ? 'poi' : 'landmark', q(p.id), today].join(','));
fs.mkdirSync(DATA, { recursive: true });
fs.writeFileSync(OUT, ['name,x,y,z,kind,id,fetched', ...rows].join('\n') + '\n');
const named = pois.filter((p) => /\.POI\./.test(p.id)).map((p) => p.name);
console.log(`saved ${pois.length} places (${named.length} named POIs) to ${path.relative(ROOT, OUT) || OUT}`);
console.log(`POIs: ${named.sort().join(', ')}`);
