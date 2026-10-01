import { useEffect, useState } from 'react';
import type { DatasetKey, Filters } from './types';

export const emptyFilters = (dataset: DatasetKey = 'demo'): Filters => ({
  dataset, date_from: null, date_to: null, seasons: [], regions: [], event_windows: [],
  playlist_contains: null, server_only: false, min_lobby_strength: null,
});

/** useState that survives page reloads (the dashboard is local, so localStorage is fine). */
export function usePersistent<T>(key: string, initial: T): [T, (v: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(`zonelab:${key}`);
      return raw ? { ...initial, ...JSON.parse(raw) } as T : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(`zonelab:${key}`, JSON.stringify(value)); } catch { /* storage full or blocked */ }
  }, [key, value]);
  return [value, setValue];
}

export function describe(f: Filters): string {
  const parts: string[] = [];
  if (f.date_from || f.date_to) parts.push(`${f.date_from ?? 'start'} to ${f.date_to ?? 'now'}`);
  if (f.seasons.length) parts.push(f.seasons.join(', '));
  if (f.regions.length) parts.push(f.regions.join(', '));
  if (f.event_windows.length) parts.push(`${f.event_windows.length} event window${f.event_windows.length > 1 ? 's' : ''}`);
  if (f.server_only) parts.push('server replays');
  if (f.min_lobby_strength) parts.push(`lobbies ${Math.round(f.min_lobby_strength * 100)}%+ top-1,000`);
  return parts.length ? parts.join('; ') : 'All matches';
}
