// Chart colours. Categorical series use distinct hues; ordered series (phases) run
// from safe-zone teal (early) to storm violet (late), so order reads at a glance.
export const INK = '#18202E';
export const MUTED = '#5C6779';
export const GRID = '#D5DCE5';
export const VIOLET = '#5B2E91';
export const TEAL = '#1E7F8C';

export const CATEGORICAL = [VIOLET, TEAL, '#B26B00', '#4A5A70', '#A23B5A', '#5E7A1F', '#2F6FB0', '#8A5A2B'];

function mix(a: string, b: string, t: number): string {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return '#' + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, '0')).join('');
}

export function seriesColors(names: string[]): string[] {
  const ordered = names.length > 2 && names.every((n) => /^Phase \d+$/.test(n));
  if (ordered) return names.map((_, i) => mix(TEAL, VIOLET, names.length === 1 ? 1 : i / (names.length - 1)));
  return names.map((_, i) => CATEGORICAL[i % CATEGORICAL.length]);
}
