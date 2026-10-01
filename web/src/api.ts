import type { AnalysisInfo, AnalysisResult, DatasetKey, Facets, Filters, Health, MatchList, PageInfo } from './types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

/** Strip empty values so the API applies its defaults. */
function cleanFilters(f: Filters): Filters {
  return {
    ...f,
    date_from: f.date_from || null,
    date_to: f.date_to || null,
    playlist_contains: f.playlist_contains?.trim() || null,
  };
}

export const api = {
  health: () => request<Health>('/api/health'),
  facets: (dataset: DatasetKey) => request<Facets>(`/api/facets?dataset=${dataset}`),
  analyses: () => request<AnalysisInfo[]>('/api/analyses'),
  pages: () => request<PageInfo[]>('/api/pages'),
  matches: (filters: Filters, limit = 200) =>
    request<MatchList>('/api/matches', { method: 'POST', body: JSON.stringify({ filters: cleanFilters(filters), limit }) }),
  run: (id: string, filters: Filters, params: Record<string, unknown>, compare?: Filters) =>
    request<AnalysisResult>(`/api/analyses/${id}/run`, {
      method: 'POST',
      body: JSON.stringify({ filters: cleanFilters(filters), params, compare: compare ? cleanFilters(compare) : null }),
    }),
};
