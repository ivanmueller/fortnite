import type { AnalysisInfo, AnalysisResult, DataStatus, DatasetKey, DownloadPlan, Facets, Filters, Health, JobDetail, JobSummary,
  MatchList, PageInfo, TournamentRow } from './types';

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
  dataStatus: () => request<DataStatus>('/api/data/status'),
  jobs: () => request<JobSummary[]>('/api/data/jobs'),
  job: (id: string) => request<JobDetail>(`/api/data/jobs/${id}`),
  startJob: (kind: string, params: Record<string, unknown> = {}) =>
    request<JobSummary>('/api/data/jobs', { method: 'POST', body: JSON.stringify({ kind, params }) }),
  cancelJob: (id: string) => request<JobSummary>(`/api/data/jobs/${id}/cancel`, { method: 'POST' }),
  tournaments: (q: { search: string; region: string; days: number; upcoming: boolean }) =>
    request<{ rows: TournamentRow[]; total: number }>(`/api/data/tournaments?${new URLSearchParams({
      search: q.search, region: q.region, days: String(q.days), upcoming: String(q.upcoming) })}`),
  plan: (limit: number, minTop: number) => request<DownloadPlan>(`/api/data/plan?limit=${limit}&min_top=${minTop}`),
  saveSettings: (s: { data_dir?: string; keep_raw?: boolean; fortnite_api_key?: string; parallel?: number }) =>
    request<{ ok: boolean }>('/api/data/settings', { method: 'PUT', body: JSON.stringify(s) }),
  matches: (filters: Filters, limit = 200) =>
    request<MatchList>('/api/matches', { method: 'POST', body: JSON.stringify({ filters: cleanFilters(filters), limit }) }),
  run: (id: string, filters: Filters, params: Record<string, unknown>, compare?: Filters) =>
    request<AnalysisResult>(`/api/analyses/${id}/run`, {
      method: 'POST',
      body: JSON.stringify({ filters: cleanFilters(filters), params, compare: compare ? cleanFilters(compare) : null }),
    }),
};
