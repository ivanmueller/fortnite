import { useEffect, useRef, useState } from 'react';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import { describe, emptyFilters, usePersistent } from './state';
import type { Filters, PageInfo } from './types';
import { SelectionPanel } from './components/SelectionPanel';
import { SectionView } from './components/SectionView';
import { MatchesView } from './components/MatchesView';
import { DataPage } from './components/DataPage';
import { ExportPanel } from './components/ExportPanel';
import { GamePlanView } from './components/GamePlanView';
import { MatchRoom } from './components/MatchRoom';

export const APP_NAME = 'Vantage';
export const APP_TAGLINE = 'Competitive Fortnite analytics';

export function App() {
  const qc = useQueryClient();
  const [filters, setFilters] = usePersistent<Filters>('filters', emptyFilters('real'));
  const [compare, setCompare] = usePersistent<Filters>('compare', emptyFilters('real'));
  useEffect(() => {
    // Only tournament data is shown; move any saved selection on the old demo or "My replays" data.
    if (filters.dataset !== 'real') setFilters(emptyFilters('real'));
    if (compare.dataset !== 'real') setCompare(emptyFilters('real'));
  }, [filters.dataset, compare.dataset, setFilters, setCompare]);
  const [tab, setTab] = usePersistent<{ id: string }>('page', { id: 'overview' });
  const [reloadedAt, setReloadedAt] = useState<string | null>(null);

  // Live reload: the API gets a new boot id every time uvicorn restarts after a code change.
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 2000, retry: false });
  const bootId = health.data?.boot_id;
  const lastBoot = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!bootId) return;
    if (lastBoot.current && lastBoot.current !== bootId) {
      qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'health' });
      setReloadedAt(new Date().toLocaleTimeString());
    }
    lastBoot.current = bootId;
  }, [bootId, qc]);

  const dataset = filters.dataset;
  const datasetReady = health.data?.datasets[dataset]?.available ?? false;
  const facets = useQuery({ queryKey: ['facets', dataset, bootId], queryFn: () => api.facets(dataset), enabled: datasetReady });
  const pages = useQuery({ queryKey: ['pages', bootId], queryFn: api.pages, enabled: !!bootId });
  const analyses = useQuery({ queryKey: ['analyses', bootId], queryFn: api.analyses, enabled: !!bootId });
  const onData = tab.id === 'data';
  // Typed names (e.g. a team) are shared by every section on a page.
  const [pageText, setPageText] = useState<Record<string, string>>({});
  const page: PageInfo | undefined = onData ? undefined : pages.data?.find((p) => p.id === tab.id) ?? pages.data?.[0];
  const compareFilters = { ...compare, dataset };
  const counts = useQuery({
    queryKey: ['count', filters, bootId], queryFn: () => api.matches(filters, 1), enabled: datasetReady,
    placeholderData: keepPreviousData,
  });
  const compareCount = useQuery({
    queryKey: ['count', compareFilters, bootId], queryFn: () => api.matches(compareFilters, 1),
    enabled: datasetReady && !!page?.needs_compare, placeholderData: keepPreviousData,
  });
  const scope = page?.needs_compare ? `A: ${describe(filters)}. B: ${describe(compareFilters)}` : describe(filters);

  return (
    <div className={`app ${onData ? 'app--wide' : ''}`}>
      <header className="topbar">
        <div className="brand">
          <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden>
            <path d="M4 26 L16 6 L28 26 Z" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round" />
            <path d="M11 26 L16 17 L21 26 Z" fill="var(--accent-bright)" />
          </svg>
          <span className="brand__name">{APP_NAME}</span>
          <span className="brand__tag">{APP_TAGLINE}</span>
        </div>
        <div className="status" aria-live="polite">
          {health.isError && <span className="status__bad">Can't reach the analysis service on port 8000. Start it with Start-Vantage.bat.</span>}
          {health.data && <span className="status__ok">Connected</span>}
          {reloadedAt && <span className="status__reload">Updated at {reloadedAt}</span>}
        </div>
      </header>

      {!onData && <aside className="rail">
        <SelectionPanel title={page?.needs_compare ? 'Group A' : 'Selection'} filters={filters} onChange={setFilters}
                        facets={facets.data} count={counts.data?.total} />
        {page?.needs_compare && (
          <SelectionPanel title="Group B" filters={compareFilters} onChange={(f) => setCompare(f)}
                          facets={facets.data} count={compareCount.data?.total} />
        )}
        {datasetReady && <ExportPanel filters={filters} count={counts.data?.total} />}
      </aside>}

      <main className="main">
        <nav className="tabs" aria-label="Pages">
          {pages.data?.map((p) => (
            <button key={p.id} className={page?.id === p.id ? 'is-on' : ''} aria-current={page?.id === p.id ? 'page' : undefined}
                    onClick={() => setTab({ id: p.id })}>{p.title}</button>
          ))}
          <button className={`tabs__data ${onData ? 'is-on' : ''}`} aria-current={onData ? 'page' : undefined}
                  onClick={() => setTab({ id: 'data' })}>Data</button>
        </nav>

        {!health.data && !health.isError && <p className="muted">Connecting…</p>}
        {onData && health.data && <DataPage />}
        {!onData && health.data && !datasetReady && (
          <div className="empty">
            <h1>No tournament data yet</h1>
            <p>Open the <button className="link" onClick={() => setTab({ id: 'data' })}>Data</button> tab to sign in to Epic, pick
              tournaments and download them. This page fills in as soon as the first matches are processed.</p>
          </div>
        )}

        {!onData && datasetReady && page && (
          <>
            <div className="intro">
              <h1>{page.title}</h1>
              <p>{page.question}</p>
            </div>
            {page.id === 'gameplan' && (
              <GamePlanView info={analyses.data?.find((a) => a.id === 'gameplan')} filters={filters} bootId={bootId} enabled={datasetReady}
                            team={pageText[page.id] ?? ''} onTeam={(v) => setPageText((p) => ({ ...p, [page.id]: v }))} />
            )}
            {page.id === 'match_room' && (
              <MatchRoom info={analyses.data?.find((a) => a.id === 'match_room')} filters={filters} bootId={bootId} enabled={datasetReady}
                         team={pageText[page.id] ?? ''} onTeam={(v) => setPageText((p) => ({ ...p, [page.id]: v }))} />
            )}
            {page.id !== 'gameplan' && page.id !== 'match_room' && page.sections.map((s) => (
              <SectionView key={`${page.id}-${s.analysis}`} section={s} info={analyses.data?.find((a) => a.id === s.analysis)}
                           filters={filters} compare={page.needs_compare ? compareFilters : undefined} bootId={bootId}
                           enabled={datasetReady} scope={scope}
                           sharedText={pageText[page.id] ?? ''} onSharedText={(v) => setPageText((p) => ({ ...p, [page.id]: v }))} />
            ))}
            {page.id === 'overview' && (
              <details className="more more--page">
                <summary>All matches in the selection</summary>
                <div className="more__body"><MatchesView filters={filters} bootId={bootId} /></div>
              </details>
            )}
          </>
        )}
      </main>
    </div>
  );
}
