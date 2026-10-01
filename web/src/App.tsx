import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import { describe, emptyFilters, usePersistent } from './state';
import type { AnalysisInfo, DatasetKey, Filters } from './types';
import { SelectionPanel } from './components/SelectionPanel';
import { ParamsForm } from './components/ParamsForm';
import { ResultView } from './components/ResultView';
import { MatchesView } from './components/MatchesView';
import { GuidePanel } from './components/GuidePanel';

const MATCHES_TAB = '__matches';

export function App() {
  const qc = useQueryClient();
  const [filters, setFilters] = usePersistent<Filters>('filters', emptyFilters('demo'));
  const [compare, setCompare] = usePersistent<Filters>('compare', emptyFilters('demo'));
  const [tab, setTab] = usePersistent<{ id: string }>('tab', { id: 'overview' });
  const [paramsById, setParamsById] = usePersistent<Record<string, Record<string, unknown>>>('params', {});
  const [reloadedAt, setReloadedAt] = useState<string | null>(null);
  const [guideOpen, setGuideOpen] = usePersistent<{ open: boolean }>('guide', { open: true });

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

  const dataset: DatasetKey = filters.dataset;
  const datasets = health.data
    ? (Object.entries(health.data.datasets) as [DatasetKey, { label: string; available: boolean; matches: number }][])
        .map(([key, d]) => ({ key, label: d.label, available: d.available, matches: d.matches }))
    : undefined;
  const datasetReady = health.data?.datasets[dataset]?.available ?? false;

  const facets = useQuery({ queryKey: ['facets', dataset, bootId], queryFn: () => api.facets(dataset), enabled: datasetReady });
  const analyses = useQuery({ queryKey: ['analyses', bootId], queryFn: api.analyses, enabled: !!bootId });
  const current: AnalysisInfo | undefined = analyses.data?.find((a) => a.id === tab.id);
  const params = { ...Object.fromEntries((current?.params ?? []).map((p) => [p.name, p.default])), ...(paramsById[tab.id] ?? {}) };
  const compareFilters = { ...compare, dataset };

  const run = useQuery({
    queryKey: ['run', tab.id, filters, params, current?.needs_compare ? compareFilters : null, bootId],
    queryFn: () => api.run(tab.id, filters, params, current?.needs_compare ? compareFilters : undefined),
    enabled: datasetReady && !!current,
    placeholderData: keepPreviousData,
  });
  const counts = useQuery({
    queryKey: ['count', filters, bootId], queryFn: () => api.matches(filters, 1), enabled: datasetReady,
    placeholderData: keepPreviousData,
  });
  const compareCount = useQuery({
    queryKey: ['count', compareFilters, bootId], queryFn: () => api.matches(compareFilters, 1),
    enabled: datasetReady && !!current?.needs_compare, placeholderData: keepPreviousData,
  });

  const alpha = Number(params.alpha ?? 0.005);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
            <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="2.5" />
            <circle cx="19.5" cy="13.5" r="6" fill="var(--violet)" />
          </svg>
          <span>Zone Lab</span>
        </div>
        <div className="status" aria-live="polite">
          {health.isError && <span className="status__bad">API not reachable on port 8000. Start it with npm run dev.</span>}
          {health.data && <span className="status__ok">API connected</span>}
          {reloadedAt && <span className="status__reload">Code change picked up at {reloadedAt}</span>}
        </div>
      </header>

      <aside className="rail">
        <SelectionPanel title={current?.needs_compare ? 'Selection A' : 'Selection'} filters={filters} onChange={setFilters}
                        facets={facets.data} count={counts.data?.total} showDataset datasets={datasets} />
        {current?.needs_compare && (
          <SelectionPanel title="Compare with B" filters={compareFilters} onChange={(f) => setCompare(f)}
                          facets={facets.data} count={compareCount.data?.total} />
        )}
      </aside>

      <main className="main">
        <nav className="tabs" aria-label="Analyses">
          {analyses.data?.map((a) => (
            <button key={a.id} className={tab.id === a.id ? 'is-on' : ''} aria-current={tab.id === a.id ? 'page' : undefined}
                    onClick={() => setTab({ id: a.id })}>{a.title}</button>
          ))}
          <button className={tab.id === MATCHES_TAB ? 'is-on' : ''} onClick={() => setTab({ id: MATCHES_TAB })}>Matches</button>
        </nav>

        {!health.data && !health.isError && <p className="muted">Connecting to the API…</p>}
        {health.data && !datasetReady && <EmptyDataset dataset={dataset} />}

        {datasetReady && tab.id === MATCHES_TAB && <MatchesView filters={filters} bootId={bootId} />}

        {datasetReady && current && (
          <>
            <div className="intro">
              <h1>{current.title}</h1>
              <p>{current.summary}</p>
            </div>
            {current.guide && <GuidePanel guide={current.guide} summary={current.summary} open={guideOpen.open} onToggle={(open) => setGuideOpen({ open })} />}
            <ParamsForm params={current.params} values={params}
                        onChange={(v) => setParamsById({ ...paramsById, [tab.id]: v })} />
            <div className={`run ${run.isFetching ? 'is-busy' : ''}`} aria-busy={run.isFetching}>
              {run.isError && <p className="notice notice--error">{(run.error as Error).message}</p>}
              {run.data?.status === 'empty' && <p className="notice">{run.data.message}</p>}
              {run.data?.status === 'ok' && (
                <ResultView result={run.data} alpha={alpha} guide={current.guide} title={current.title}
                            scope={current.needs_compare ? `A: ${describe(filters)}. B: ${describe(compareFilters)}` : describe(filters)} />
              )}
              {run.isPending && <p className="muted">Running…</p>}
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function EmptyDataset({ dataset }: { dataset: DatasetKey }) {
  const copy: Record<DatasetKey, { title: string; body: React.ReactNode }> = {
    demo: {
      title: 'No demo data yet',
      body: <>Create 240 synthetic matches across three seasons with <code>npm run demo-data</code>. This page fills in on its own when it finishes.</>,
    },
    local: {
      title: 'No replays of your own yet',
      body: <>Double-click <code>ZoneLab-Data.bat</code> in the project folder and choose 6 (after the one-time setup, option 1).
        Your newest replays from the Fortnite Demos folder appear here as soon as they're processed.</>,
    },
    real: {
      title: 'No tournament data yet',
      body: <>Double-click <code>ZoneLab-Data.bat</code> in the project folder and work through options 1 to 5: setup, your
        api-fortnite.com key, pick a tournament, collect its match IDs, then download and process them. This page fills in as soon
        as the first matches are processed. The full walkthrough is in <code>docs/REAL_DATA.md</code>.</>,
    },
  };
  return (
    <div className="empty">
      <h1>{copy[dataset].title}</h1>
      <p>{copy[dataset].body}</p>
    </div>
  );
}
