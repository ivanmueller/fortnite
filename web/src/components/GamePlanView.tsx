import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../api';
import type { AnalysisInfo, AnalysisResult, Filters, TableSpec } from '../types';
import { DataTable } from './DataTable';
import { GuidePanel } from './GuidePanel';

interface Props {
  info?: AnalysisInfo;
  filters: Filters;
  bootId?: string;
  enabled: boolean;
  team: string;
  onTeam: (v: string) => void;
}

/** The plan's blocks, in game order, from the analysis's "Plan" table: name, the call, and the tables behind it. */
function blocks(r: AnalysisResult): { name: string; call: string; tables: TableSpec[] }[] {
  const plan = r.tables.find((t) => t.title === 'Plan');
  if (!plan) return [];
  const [iB, iC, iT] = ['Block', 'The call', 'Tables'].map((c) => plan.columns.indexOf(c));
  return plan.rows.map((row) => ({
    name: String(row[iB]),
    call: String(row[iC]),
    tables: String(row[iT] ?? '').split('|').map((t) => r.tables.find((x) => x.title === t)).filter((t): t is TableSpec => !!t),
  }));
}

const metric = (r: AnalysisResult, label: string) => r.metrics.find((m) => m.label === label)?.value;

/** One team's pre-tournament plan: inputs, a briefing band, then one block per phase of the game. */
export function GamePlanView({ info, filters, bootId, enabled, team, onTeam }: Props) {
  const schemeParam = info?.params.find((p) => p.kind === 'choice');
  const [teamDraft, setTeamDraft] = useState(team);
  const [spot, setSpot] = useState('');
  const [spotDraft, setSpotDraft] = useState('');
  const [asOf, setAsOf] = useState('');
  const [scheme, setScheme] = useState('');
  const params: Record<string, unknown> = {
    ...(team ? { team } : {}), ...(spot ? { spot } : {}), ...(asOf ? { as_of: asOf } : {}), ...(scheme ? { scheme } : {}),
  };
  const run = useQuery({
    queryKey: ['run', 'gameplan', filters, params, bootId],
    queryFn: () => api.run('gameplan', filters, params),
    enabled,
    placeholderData: keepPreviousData,
  });
  const r = run.data?.status === 'ok' ? run.data : undefined;
  const plan = r ? blocks(r) : [];
  const shown = new Set(plan.flatMap((b) => b.tables.map((t) => t.title)).concat('Plan'));

  return (
    <section className={`plan ${run.isFetching ? 'is-busy' : ''}`} aria-busy={run.isFetching}>
      <form className="plan__inputs" onSubmit={(e) => { e.preventDefault(); onTeam(teamDraft.trim()); setSpot(spotDraft.trim()); }}>
        <label>Players
          <input className="input" value={teamDraft} placeholder="e.g. clix, rapid" onChange={(e) => setTeamDraft(e.target.value)} />
        </label>
        <label>Drop spot
          <input className="input" value={spotDraft} placeholder="Blank: where they usually land" onChange={(e) => setSpotDraft(e.target.value)} />
        </label>
        <label>Event's first day
          <input className="input" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
        </label>
        {schemeParam && (
          <label>Event scoring
            <select className="input" value={scheme} onChange={(e) => setScheme(e.target.value)}>
              <option value="">Default (scoring.json)</option>
              {(schemeParam.options ?? []).map((o) => <option key={String(o.value)} value={String(o.value)}>{o.label}</option>)}
            </select>
          </label>
        )}
        <div className="plan__actions">
          <button className="btn" type="submit">Build plan</button>
          {plan.length > 0 && <button className="btn btn--ghost" type="button" onClick={() => window.print()}>Print plan</button>}
        </div>
      </form>
      <p className="muted small plan__hint">
        The plan uses only the selected matches played before the event's first day. Selected matches on or after it are the
        event, and the last block checks the plan against them.
      </p>

      {run.isPending && <p className="muted">Building the plan…</p>}
      {run.isError && <p className="notice notice--error">{(run.error as Error).message}</p>}
      {run.data?.status === 'empty' && <p className="notice">{run.data.message}</p>}

      {r && plan.length === 0 && (
        <>
          <p className="plan__ask">{r.headline}</p>
          {r.warnings.map((w) => <p key={w} className="notice notice--warn">{w}</p>)}
          {r.tables.map((t) => <DataTable key={t.title} table={t} maxRows={30} />)}
        </>
      )}

      {r && plan.length > 0 && (
        <article className="plan__sheet">
          <header className="plan__band">
            <h2>{String(metric(r, 'Team') ?? team)}</h2>
            <dl>
              <div><dt>Drop</dt><dd>{String(metric(r, 'Drop spot') ?? '–')}</dd></div>
              <div><dt>Built from</dt><dd>{String(metric(r, 'History') ?? '–')}</dd></div>
              <div><dt>Scoring</dt><dd>{String(metric(r, 'Scoring') ?? '–')}</dd></div>
            </dl>
          </header>
          {r.warnings.map((w) => <p key={w} className="notice notice--warn">{w}</p>)}
          <ol className="plan__blocks">
            {plan.map((b) => (
              <li key={b.name} className={`plan__block ${b.name === 'Check' ? 'plan__block--check' : ''}`}>
                <div className="plan__call">
                  <h3>{b.name === 'Check' ? 'How the plan held up' : b.name}</h3>
                  <p>{b.call}</p>
                </div>
                <div className="plan__evidence">
                  {b.tables.map((t) => <DataTable key={t.title} table={t} />)}
                </div>
              </li>
            ))}
          </ol>
          <details className="more plan__more">
            <summary>Method and notes</summary>
            <div className="more__body">
              {r.tables.filter((t) => !shown.has(t.title)).map((t) => <DataTable key={t.title} table={t} />)}
              {r.notes.length > 0 && <aside className="notes"><h3>Notes</h3><ul>{r.notes.map((n) => <li key={n}>{n}</li>)}</ul></aside>}
              {info?.guide && <GuidePanel guide={info.guide} summary={info.summary} open onToggle={() => undefined} />}
            </div>
          </details>
        </article>
      )}
    </section>
  );
}
