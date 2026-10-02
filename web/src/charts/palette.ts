// Chart colours. Categorical series use distinct hues; ordered series (zones) run
// from light teal (early) to deep navy (endgame), so order reads at a glance.
export const INK = '#18202E';
export const MUTED = '#5C6779';
export const GRID = '#D5DCE5';
export const ACCENT = '#0F766E';
export const NAVY = '#0F3B5F';
export const TEAL = '#3B5B8C';

export const CATEGORICAL = [ACCENT, '#D08A12', TEAL, '#C2504A', '#2B8CC4', '#6B8E23', '#64748B', '#8A5A2B'];

function mix(a: string, b: string, t: number): string {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return '#' + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, '0')).join('');
}

export function seriesColors(names: string[]): string[] {
  const ordered = names.length > 2 && names.every((n) => /^(Phase|Zone) \d+$/.test(n));
  if (ordered) return names.map((_, i) => mix('#7FC8C0', NAVY, names.length === 1 ? 1 : i / (names.length - 1)));
  // Height tiers are ordered too: low ground pale, high ground strongest.
  const tiers: Record<string, string> = { 'Low ground': '#A9B4C2', 'Mid ground': '#5B8DB8', 'High ground': ACCENT };
  if (names.length > 1 && names.every((n) => n in tiers)) return names.map((n) => tiers[n]);
  return names.map((_, i) => CATEGORICAL[i % CATEGORICAL.length]);
}

/** Ordered zone colour: 0 = first zone (light teal) to 1 = last zone (deep navy). */
export const zoneColor = (f: number) => mix('#7FC8C0', NAVY, Math.max(0, Math.min(1, f)));
