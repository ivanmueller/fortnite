import { useEffect, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../api';
import type { AnalysisInfo, AnalysisResult, Conclusion, Filters, SectionInfo, TableSpec } from '../types';
import { ChartView } from '../charts/ChartView';
import { ConclusionPanel } from './ConclusionPanel';
import { DataTable } from './DataTable';
import { EvidenceStrip } from './EvidenceStrip';
import { GuidePanel } from './GuidePanel';
import { InfoTip } from './InfoTip';
import { PlanCompare } from './PlanCompare';

const ALPHA = 0.005;

/** Plain-language confidence for people without a statistics background. */
export function confidence(c: Conclusion | null): { label: string; tone: string } | null {
  if (!c) return null;
  if (c.status === 'found') {
    // A pattern on a small or uncertain sample shouldn't read as settled to someone who only sees the badge.
    if (c.reliability.some((r) => !r.ok)) return { label: 'Promising: needs more data', tone: 'some' };
    return c.evidence.some((e) => e.strength === 'strong')
      ? { label: 'Strong evidence', tone: 'strong' } : { label: 'Some evidence', tone: 'some' };
  }
  if (c.status === 'none') return { label: 'No clear pattern', tone: 'none' };
  if (c.status === 'insufficient') return { label: 'Not enough data yet', tone: 'insufficient' };
  return null;
}

const TOP_ROWS = 10;

/** The featured version of a table: its key columns and first rows (the full table stays under Details). */
function featured(t: TableSpec, columns: string[] | null): TableSpec {
  const idx = columns ? columns.map((c) => t.columns.indexOf(c)).filter((i) => i >= 0) : t.columns.map((_, i) => i);
  return { title: t.title, columns: idx.map((i) => t.columns[i]), rows: t.rows.slice(0, TOP_ROWS).map((r) => idx.map((i) => r[i])) };
}

interface Props {
  section: SectionInfo;
  info?: AnalysisInfo;
  filters: Filters;
  compare?: Filters;
  bootId?: string;
  enabled: boolean;
  scope: string;
  sharedText?: string;
  onSharedText?: (v: string) => void;
}

export function SectionView({ section, info, filters, compare, bootId, enabled, scope, sharedText, onSharedText }: Props) {
  // A section can offer a match picker (a parameter of kind "match"), e.g. the zone replay map.
  const matchParam = info?.params.find((p) => p.kind === 'match');
  const [match, setMatch] = useState('');
  const matchList = useQuery({
    queryKey: ['match-list', filters, bootId], queryFn: () => api.matches(filters, 500), enabled: !!matchParam && enabled,
  });
  const zoneParam = info?.params.find((p) => p.kind === 'zone');
  const textParam = info?.params.find((p) => p.kind === 'text');
  const plansParam = info?.params.find((p) => p.kind === 'plans');
  const [plans, setPlans] = useState('');
  const [localText, setLocalText] = useState('');
  const text = onSharedText ? (sharedText ?? '') : localText;
  const setText = onSharedText ?? setLocalText;
  const [draft, setDraft] = useState(text);
  useEffect(() => { setDraft(text); }, [text]);
  const [zone, setZone] = useState<string>('');
  const params: Record<string, unknown> = {
    ...(matchParam && match ? { [matchParam.name]: match } : {}),
    ...(zoneParam && zone ? { [zoneParam.name]: Number(zone) } : {}),
    ...(textParam && text ? { [textParam.name]: text } : {}),
    ...(plansParam && plans ? { [plansParam.name]: plans } : {}),
  };
  const run = useQuery({
    queryKey: ['run', section.analysis, filters, compare ?? null, params, bootId],
    queryFn: () => api.run(section.analysis, filters, params, compare),
    enabled,
    placeholderData: keepPreviousData,
  });
  const r: AnalysisResult | undefined = run.data?.status === 'ok' ? run.data : undefined;
  const terms = info?.guide?.terms ?? {};
  const featuredCharts = r ? section.charts.map((t) => r.charts.find((c) => c.title === t)).filter((c) => c !== undefined) : [];
  const otherCharts = r ? r.charts.filter((c) => !section.charts.includes(c.title)) : [];
  const featuredTable = r && section.table ? r.tables.find((t) => t.title === section.table!.title) : undefined;
  const metrics = r ? section.metrics.map((m) => r.metrics.find((x) => x.label === m)).filter((m) => m !== undefined) : [];
  const badge = confidence(r?.conclusion ?? null);
  const takeaway = r?.conclusion && r.conclusion.status !== 'descriptive' ? r.conclusion.summary : r?.headline;

  return (
    <section className={`section ${run.isFetching ? 'is-busy' : ''}`} aria-busy={run.isFetching}>
      <header className="section__head">
        <h2>{section.title}</h2>
        <p className="section__q">{section.question}</p>
      </header>

      {run.isPending && <p className="muted">Working it out…</p>}
      {run.isError && <p className="notice notice--error">{(run.error as Error).message}</p>}
      {run.data?.status === 'empty' && <p className="notice">{run.data.message}</p>}

      {plansParam && <PlanCompare initial={plans || String(plansParam.default)} onCompare={setPlans} />}
      {textParam && (
        <form className="row text-pick" onSubmit={(e) => { e.preventDefault(); setText(draft.trim()); }}>
          <label className="inline">{textParam.label}</label>
          <input className="input input--grow" value={draft} placeholder="e.g. clix, rapid" onChange={(e) => setDraft(e.target.value)} />
          <button className="btn" type="submit">Show</button>
        </form>
      )}
      {matchParam && matchList.data && (
        <label className="inline match-pick">{matchParam.label}
          <select className="input" value={match} onChange={(e) => setMatch(e.target.value)}>
            <option value="">Most recent match</option>
            {(() => {
              const c = matchList.data.columns;
              const iId = c.indexOf('match_id'), iDate = c.indexOf('match_date'), iWin = c.indexOf('event_window_id');
              return matchList.data.rows.map((row) => (
                <option key={String(row[iId])} value={String(row[iId])}>
                  {`${String(row[iDate] ?? '').slice(0, 10)} · ${row[iWin] ?? ''} · ${String(row[iId]).slice(0, 8)}`}
                </option>
              ));
            })()}
          </select>
          {zoneParam && (
            <select className="input" value={zone || String(zoneParam.default)} onChange={(e) => setZone(e.target.value)}>
              {(zoneParam.options ?? []).map((o) => <option key={o.value} value={String(o.value)}>{o.label}</option>)}
            </select>
          )}
        </label>
      )}

      {r && (
        <>
          <div className="takeaway">
            {badge && <span className={`confidence confidence--${badge.tone}`}>{badge.label}</span>}
            <p className="takeaway__text">{takeaway}</p>
            {r.conclusion && r.conclusion.status !== 'descriptive' && r.headline && <p className="takeaway__sub">{r.headline}</p>}
          </div>
          {r.warnings.map((w) => <p key={w} className="notice notice--warn">{w}</p>)}

          {metrics.length > 0 && (
            <dl className="metrics">
              {metrics.map((m) => (
                <div key={m.label} className="metric">
                  <dt>{m.label} <InfoTip label={m.label} text={terms[m.label] ?? m.detail} /></dt>
                  <dd>{m.value}</dd>
                </div>
              ))}
            </dl>
          )}

          {featuredCharts.length > 0 && (
            <div className={`charts ${featuredCharts.length === 1 ? 'charts--single' : ''}`}>
              {featuredCharts.map((c, i) => <ChartView key={`${c.title}-${i}`} spec={c} help={info?.guide?.charts[c.title]} />)}
            </div>
          )}
          {featuredTable && <DataTable table={featured(featuredTable, section.table!.columns)} />}
          {featuredTable && featuredTable.rows.length > TOP_ROWS && (
            <p className="muted small">Showing the first {TOP_ROWS} of {featuredTable.rows.length} rows. The full table is under Details.</p>
          )}

          <details className="more">
            <summary>Details and method</summary>
            <div className="more__body">
              {r.conclusion && <ConclusionPanel conclusion={r.conclusion} title={section.title} scope={scope} />}
              <EvidenceStrip tests={r.tests} alpha={ALPHA} terms={terms} />
              {otherCharts.length > 0 && (
                <div className="charts">
                  {otherCharts.map((c, i) => <ChartView key={`${c.title}-${i}`} spec={c} help={info?.guide?.charts[c.title]} />)}
                </div>
              )}
              {r.tables.map((t) => <DataTable key={t.title} table={t} />)}
              {r.metrics.length > metrics.length && (
                <dl className="metrics">
                  {r.metrics.filter((m) => !section.metrics.includes(m.label)).map((m) => (
                    <div key={m.label} className="metric">
                      <dt>{m.label} <InfoTip label={m.label} text={terms[m.label] ?? m.detail} /></dt>
                      <dd>{m.value}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {r.notes.length > 0 && (
                <aside className="notes"><h3>Notes</h3><ul>{r.notes.map((n) => <li key={n}>{n}</li>)}</ul></aside>
              )}
              {info?.guide && <GuidePanel guide={info.guide} summary={info.summary} open onToggle={() => undefined} />}
            </div>
          </details>
        </>
      )}
    </section>
  );
}
