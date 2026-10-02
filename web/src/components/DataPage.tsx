import { useEffect, useRef, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import type { DataStatus, JobDetail, JobSummary } from '../types';

const REGIONS = ['', 'EU', 'NAC', 'NAW', 'BR', 'ASIA', 'OCE', 'ME'];
const ACTIVE = new Set(['queued', 'running']);

function ago(date: string | null | undefined): string {
  if (!date) return 'never';
  const days = Math.floor((Date.now() - new Date(date).getTime()) / 86400000);
  return days <= 0 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`;
}

function eta(s: number | null): string {
  if (s === null || !isFinite(s)) return '';
  if (s < 60) return 'under a minute left';
  const m = Math.round(s / 60);
  return m < 60 ? `about ${m} min left` : `about ${Math.floor(m / 60)} h ${m % 60} min left`;
}

function elapsed(j: JobSummary): string {
  if (!j.started) return '';
  const s = Math.round(((j.ended ?? Date.now() / 1000) - j.started));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

/** Start a job, then let the job panel take over. */
function useStart() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, params }: { kind: string; params?: Record<string, unknown> }) => api.startJob(kind, params),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['jobs'] }); qc.invalidateQueries({ queryKey: ['data-status'] }); },
  });
}

function ProgressBar({ job }: { job: JobSummary }) {
  const pct = Math.round((job.progress ?? 0) * 100);
  const unknown = job.status === 'running' && job.step_frac === null;
  return (
    <div className={`progress ${unknown ? 'progress--busy' : ''} progress--${job.status}`} role="progressbar"
         aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className="progress__fill" style={{ width: `${job.status === 'done' ? 100 : pct}%` }} />
    </div>
  );
}

function JobCard({ job, onCancel }: { job: JobSummary; onCancel: () => void }) {
  const [showLog, setShowLog] = useState(false);
  const detail = useQuery({
    queryKey: ['job', job.id], queryFn: () => api.job(job.id),
    refetchInterval: ACTIVE.has(job.status) ? 1000 : false, enabled: showLog || ACTIVE.has(job.status),
  });
  const logRef = useRef<HTMLPreElement>(null);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [detail.data?.log.length]);
  // The job list is refreshed every second; once it says the job has finished, trust it over an older detailed snapshot.
  const j: JobSummary | JobDetail = detail.data && ACTIVE.has(job.status) && ACTIVE.has(detail.data.status) ? detail.data : job;
  const label = j.status === 'queued' ? 'Waiting for the current job to finish'
    : j.status === 'running' ? `${j.step_label}${j.detail && j.detail !== j.step_label ? `: ${j.detail}` : ''}`
    : j.status === 'done' ? (j.detail || 'Finished') : j.status === 'cancelled' ? 'Cancelled' : j.error ?? 'Failed';
  return (
    <div className={`job job--${j.status}`}>
      <div className="job__head">
        <div>
          <span className={`job__state job__state--${j.status}`}>{{ queued: 'Queued', running: 'Running', done: 'Done', failed: 'Failed', cancelled: 'Cancelled' }[j.status]}</span>
          <strong className="job__title">{j.title}</strong>
        </div>
        <div className="job__meta">
          {j.status === 'running' && <span>{eta(j.eta_s)}</span>}
          <span>{elapsed(j)}</span>
          {ACTIVE.has(j.status) && <button className="btn btn--ghost" onClick={onCancel}>Cancel</button>}
        </div>
      </div>
      <ProgressBar job={j} />
      <p className="job__label">{label}</p>
      {j.steps.length > 1 && (
        <ol className="job__steps">
          {j.steps.map((s, i) => (
            <li key={s} className={i < j.step || j.status === 'done' ? 'is-done' : i === j.step && j.status === 'running' ? 'is-now' : ''}>{s}</li>
          ))}
        </ol>
      )}
      <button className="link" onClick={() => setShowLog(!showLog)}>{showLog ? 'Hide log' : 'Show log'}</button>
      {showLog && <pre className="job__log" ref={logRef}>{(detail.data?.log ?? []).join('\n') || 'No output yet.'}</pre>}
    </div>
  );
}

function StatusCards({ s }: { s: DataStatus }) {
  const cards = [
    { k: 'Epic account', v: s.epic.logged_in ? (s.epic.name ?? 'Signed in') : 'Not signed in', ok: s.epic.logged_in },
    { k: 'Replay parser', v: s.parser.ready ? 'Ready' : s.parser.dotnet ? 'Needs building' : '.NET SDK missing', ok: s.parser.ready },
    { k: 'Matches', v: `${s.counts.in_tables.toLocaleString()} ready`, sub: `${s.counts.collected.toLocaleString()} collected · ${s.counts.waiting.toLocaleString()} waiting`, ok: s.counts.in_tables > 0 },
    { k: 'Power Rankings', v: s.power_rankings.players ? `${s.power_rankings.players.toLocaleString()} players` : 'Not downloaded', sub: s.power_rankings.fetched ? `updated ${ago(s.power_rankings.fetched)}` : undefined, ok: !!s.power_rankings.players },
    { k: 'Map names', v: s.pois.places ? `${s.pois.places} places` : 'Not downloaded', sub: s.pois.fetched ? `updated ${ago(s.pois.fetched)}` : undefined, ok: !!s.pois.places },
    { k: 'Storage', v: `${s.size_gb.toLocaleString()} GB`, sub: s.keep_raw ? 'keeping raw replays' : 'deleting raw replays after processing', ok: true },
  ];
  return (
    <div className="status-cards">
      {cards.map((c) => (
        <div key={c.k} className={`status-card ${c.ok ? '' : 'status-card--todo'}`}>
          <span className="status-card__k">{c.k}</span>
          <span className="status-card__v">{c.v}</span>
          {c.sub && <span className="status-card__sub">{c.sub}</span>}
        </div>
      ))}
    </div>
  );
}

function SignIn({ s }: { s: DataStatus }) {
  const start = useStart();
  const [code, setCode] = useState('');
  return (
    <section className="dcard dcard--signin">
      <h2>Sign in to Epic</h2>
      <p className="muted">Needed to find tournaments and download replays. Use a secondary Epic account.</p>
      <ol className="signin-steps">
        <li>Sign in at <a href="https://www.epicgames.com/account/personal" target="_blank" rel="noreferrer">epicgames.com</a> with the secondary account.</li>
        <li>In the same browser, <a href={s.login_url} target="_blank" rel="noreferrer">open this link</a>. It shows a short block of text.</li>
        <li>Copy all of it and paste it here. It works once and expires after 5 minutes.</li>
      </ol>
      <div className="row">
        <input className="input input--grow" placeholder='{"authorizationCode":"…"}' value={code} onChange={(e) => setCode(e.target.value)} />
        <button className="btn" disabled={!code.trim() || start.isPending} onClick={() => { start.mutate({ kind: 'login', params: { code } }); setCode(''); }}>Sign in</button>
      </div>
      {start.isError && <p className="notice notice--error">{(start.error as Error).message}</p>}
    </section>
  );
}

function Weekly({ busy }: { busy: boolean }) {
  const start = useStart();
  const [p, setP] = useState({ days: 7, region: '', limit: 100, min_top: 3, refresh_pr: true });
  return (
    <section className="dcard dcard--primary">
      <h2>Weekly tier-1 collection</h2>
      <p className="muted">Finds every high-tier tournament from the last days (FNCS, division cups, finals, cash cups, official events),
        downloads only strong lobbies, strongest first, and processes them. Replays expire after about 30 days, so run this weekly.</p>
      <div className="form-grid">
        <label>Last<select value={p.days} onChange={(e) => setP({ ...p, days: +e.target.value })}>{[3, 7, 14, 30].map((d) => <option key={d} value={d}>{d} days</option>)}</select></label>
        <label>Region<select value={p.region} onChange={(e) => setP({ ...p, region: e.target.value })}>{REGIONS.map((r) => <option key={r} value={r}>{r || 'All regions'}</option>)}</select></label>
        <label>Up to<select value={p.limit} onChange={(e) => setP({ ...p, limit: +e.target.value })}>{[25, 50, 100, 200, 400].map((n) => <option key={n} value={n}>{n} matches</option>)}</select></label>
        <label>Lobby strength<select value={p.min_top} onChange={(e) => setP({ ...p, min_top: +e.target.value })}>
          {[0, 1, 3, 5, 10].map((n) => <option key={n} value={n}>{n ? `${n}+ top-1,000 players` : 'Any lobby'}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={p.refresh_pr} onChange={(e) => setP({ ...p, refresh_pr: e.target.checked })} /> Refresh Power Rankings first</label>
      </div>
      <button className="btn btn--big" disabled={start.isPending} onClick={() => start.mutate({ kind: 'weekly', params: p })}>
        {busy ? 'Queue weekly collection' : 'Start weekly collection'}
      </button>
    </section>
  );
}

function QuickActions() {
  const start = useStart();
  const actions = [
    { kind: 'powerrankings', title: 'Refresh Power Rankings', text: 'Top 10,000 players; updates weekly. About 7 minutes.' },
    { kind: 'pois', title: 'Update map names', text: 'Named places for the Drops page. Run once per season.' },
    { kind: 'analyze', title: 'Rebuild tables', text: 'Re-reads processed matches into the dashboard. About a minute.' },
    { kind: 'reprocess', title: 'Re-process every match', text: 'Re-reads every downloaded replay. Needed after a parser update.' },
  ];
  return (
    <div className="quick">
      {actions.map((a) => (
        <button key={a.kind} className="quick__btn" onClick={() => start.mutate({ kind: a.kind })}>
          <strong>{a.title}</strong><span>{a.text}</span>
        </button>
      ))}
    </div>
  );
}

function Tournaments() {
  const start = useStart();
  const [q, setQ] = useState({ search: '', region: '', days: 30, upcoming: false });
  const [pages, setPages] = useState('10');
  const list = useQuery({ queryKey: ['tournaments', q], queryFn: () => api.tournaments(q), placeholderData: keepPreviousData });
  return (
    <section className="dcard">
      <div className="dcard__head">
        <h2>Tournaments</h2>
        <button className="btn btn--ghost" onClick={() => start.mutate({ kind: 'tournaments', params: { days: 30 } })}>Refresh list from Epic</button>
      </div>
      <div className="row">
        <input className="input input--grow" placeholder="Search, e.g. FNCS, cash, MannekenPis" value={q.search} onChange={(e) => setQ({ ...q, search: e.target.value })} />
        <select className="input" value={q.region} onChange={(e) => setQ({ ...q, region: e.target.value })}>{REGIONS.map((r) => <option key={r} value={r}>{r || 'All regions'}</option>)}</select>
        <select className="input" value={q.upcoming ? 'up' : String(q.days)} onChange={(e) => setQ(e.target.value === 'up' ? { ...q, upcoming: true } : { ...q, upcoming: false, days: +e.target.value })}>
          <option value="7">Last 7 days</option><option value="14">Last 14 days</option><option value="30">Last 30 days</option><option value="up">Upcoming</option>
        </select>
        <label className="inline">Leaderboard pages
          <select className="input" value={pages} onChange={(e) => setPages(e.target.value)}>{['1', '2', '5', '10', '25', 'all'].map((n) => <option key={n} value={n}>{n}</option>)}</select>
        </label>
      </div>
      {!list.data?.total && <p className="muted">No tournaments listed yet{q.search ? ' for this search' : ''}. Use “Refresh list from Epic”.</p>}
      {!!list.data?.total && (
        <div className="table-wrap">
          <table className="dtable">
            <thead><tr><th>Ended</th><th>Region</th><th>Tournament</th><th className="num">Matches</th><th className="num">Downloaded</th><th /></tr></thead>
            <tbody>
              {list.data.rows.map((r) => (
                <tr key={r.window}>
                  <td>{r.end.slice(0, 10)}</td>
                  <td>{r.region}</td>
                  <td><div>{r.name}</div><div className="muted small mono">{r.window}</div></td>
                  <td className="num">{r.collected || '–'}</td>
                  <td className="num">{r.collected ? `${r.downloaded} / ${r.collected}` : '–'}</td>
                  <td className="actions">
                    {!q.upcoming && <button className="btn btn--ghost btn--sm" onClick={() => start.mutate({ kind: 'collect', params: { window: r.window, pages } })}>{r.collected ? 'Collect again' : 'Collect matches'}</button>}
                    {r.collected > r.downloaded && <button className="btn btn--sm" onClick={() => start.mutate({ kind: 'download', params: { window: r.window, limit: 1000 } })}>Download {r.collected - r.downloaded}</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {list.data.total > list.data.rows.length && <p className="muted small">Showing {list.data.rows.length} of {list.data.total}. Narrow the search to see more.</p>}
        </div>
      )}
    </section>
  );
}

function Downloads({ s }: { s: DataStatus }) {
  const start = useStart();
  const [limit, setLimit] = useState(50);
  const [minTop, setMinTop] = useState(0);
  const plan = useQuery({ queryKey: ['plan', minTop], queryFn: () => api.plan(10, minTop), enabled: s.counts.waiting > 0 });
  return (
    <section className="dcard">
      <h2>Download collected matches</h2>
      <p className="muted">{s.counts.waiting.toLocaleString()} collected matches haven't been downloaded yet. The strongest lobbies go first,
        {s.parallel > 1 ? ` ${s.parallel} at a time` : ' one at a time'} (change under Settings).</p>
      <div className="row">
        <label className="inline">Up to<select className="input" value={limit} onChange={(e) => setLimit(+e.target.value)}>{[10, 25, 50, 100, 200].map((n) => <option key={n} value={n}>{n} matches</option>)}</select></label>
        <label className="inline">Lobby strength<select className="input" value={minTop} onChange={(e) => setMinTop(+e.target.value)}>
          {[0, 1, 3, 5, 10].map((n) => <option key={n} value={n}>{n ? `${n}+ top-1,000 players` : 'Any lobby'}</option>)}</select></label>
        <button className="btn" disabled={!s.counts.waiting} onClick={() => start.mutate({ kind: 'download', params: { limit, min_top: minTop } })}>Download and process</button>
      </div>
      {!!plan.data?.rows.length && (
        <details className="more">
          <summary>Next up ({plan.data.waiting.toLocaleString()} waiting)</summary>
          <div className="more__body">
            <p className="muted small">{plan.data.note}</p>
            <table className="dtable">
              <thead><tr><th>Match</th><th className="num">PR top-1,000 players</th><th className="num">PR top-10,000</th><th className="num">Leaderboard players seen</th></tr></thead>
              <tbody>{plan.data.rows.map((r) => <tr key={r.match_id}><td className="mono small">{r.match_id}</td><td className="num">{r.top1000}</td><td className="num">{r.top10000}</td><td className="num">{r.seen}</td></tr>)}</tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}

function Import() {
  const start = useStart();
  const [folder, setFolder] = useState('');
  const [count, setCount] = useState(0);
  return (
    <section className="dcard">
      <h2>Import replay files</h2>
      <p className="muted">From a team's archive of past tournaments, or your own matches. Leave the folder empty for the Fortnite replay folder.</p>
      <div className="row">
        <input className="input input--grow" placeholder="Folder, e.g. D:\TeamReplays" value={folder} onChange={(e) => setFolder(e.target.value)} />
        <select className="input" value={count} onChange={(e) => setCount(+e.target.value)}>{[0, 5, 20, 100].map((n) => <option key={n} value={n}>{n ? `Newest ${n}` : 'All files'}</option>)}</select>
        <button className="btn" onClick={() => start.mutate({ kind: 'import', params: { folder: folder.trim() || undefined, count } })}>Import</button>
      </div>
    </section>
  );
}

function Advanced({ s }: { s: DataStatus }) {
  const start = useStart();
  const qc = useQueryClient();
  const [dir, setDir] = useState(s.data_dir);
  const [key, setKey] = useState('');
  const [mid, setMid] = useState('');
  const save = useMutation({ mutationFn: api.saveSettings, onSuccess: () => qc.invalidateQueries() });
  return (
    <details className="dcard more">
      <summary>Settings and advanced tools</summary>
      <div className="more__body adv">
        <div>
          <h3>Data folder</h3>
          <p className="muted small">Where downloads and tables live. Use another drive if this one is filling up.</p>
          <div className="row"><input className="input input--grow" value={dir} onChange={(e) => setDir(e.target.value)} />
            <button className="btn btn--ghost" onClick={() => save.mutate({ data_dir: dir })}>Save</button></div>
          <label className="check"><input type="checkbox" checked={!s.keep_raw} onChange={(e) => save.mutate({ keep_raw: !e.target.checked })} />
            Delete raw replays after processing (saves about 95% of the space; re-processing then needs a fresh download)</label>
        </div>
        <div>
          <h3>Parallel downloads</h3>
          <p className="muted small">How many matches download at once. 2–3 is roughly 2–3× faster. If Epic starts limiting requests,
            downloads drop to one at a time automatically and retry.</p>
          <select className="input" value={s.parallel} onChange={(e) => save.mutate({ parallel: +e.target.value })}>
            <option value={1}>1 at a time</option><option value={2}>2 at a time</option><option value={3}>3 at a time</option>
          </select>
        </div>
        <div>
          <h3>Re-process one match</h3>
          <div className="row"><input className="input input--grow mono" placeholder="Match ID" value={mid} onChange={(e) => setMid(e.target.value)} />
            <button className="btn btn--ghost" disabled={!mid.trim()} onClick={() => start.mutate({ kind: 'reprocess', params: { match_id: mid } })}>Re-process</button></div>
        </div>
        <div>
          <h3>Replay parser</h3>
          <p className="muted small">After a Fortnite season change: survey a new match, update the definitions, then rebuild the parser.</p>
          <div className="row wrap">
            <button className="btn btn--ghost" onClick={() => start.mutate({ kind: 'survey', params: {} })}>Survey newest replay</button>
            <button className="btn btn--ghost" onClick={() => start.mutate({ kind: 'genexports' })}>Update definitions from survey</button>
            <button className="btn btn--ghost" onClick={() => start.mutate({ kind: 'rebuild_parser' })}>Rebuild the replay parser</button>
          </div>
        </div>
        <div>
          <h3>api-fortnite.com fallback</h3>
          <p className="muted small">Downloads through api-fortnite.com (2 credits per match) if Epic downloads fail. {s.api_key_set ? 'A key is saved.' : 'No key saved.'}</p>
          <div className="row"><input className="input input--grow" type="password" placeholder="API key" value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn btn--ghost" disabled={!key.trim()} onClick={() => { save.mutate({ fortnite_api_key: key }); setKey(''); }}>Save key</button>
            <button className="btn btn--ghost" disabled={!s.api_key_set} onClick={() => start.mutate({ kind: 'download', params: { limit: 25, via: 'api-fortnite' } })}>Download 25 via api-fortnite</button></div>
        </div>
        {s.epic.logged_in && (
          <div>
            <h3>Epic account</h3>
            <p className="muted small">Signed in as {s.epic.name}. Signing out revokes the saved login at Epic.</p>
            <button className="btn btn--ghost" onClick={() => start.mutate({ kind: 'logout' })}>Sign out</button>
          </div>
        )}
        {start.isError && <p className="notice notice--error">{(start.error as Error).message}</p>}
      </div>
    </details>
  );
}

export function DataPage() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ['data-status'], queryFn: api.dataStatus, refetchInterval: 5000 });
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: api.jobs, refetchInterval: (q) => (q.state.data?.some((j) => ACTIVE.has(j.status)) ? 1000 : 4000) });
  const cancel = useMutation({ mutationFn: api.cancelJob, onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }) });

  // When a job finishes, refresh everything: new matches, tables and counts.
  const finished = useRef<Set<string>>(new Set());
  useEffect(() => {
    const done = (jobs.data ?? []).filter((j) => !ACTIVE.has(j.status)).map((j) => j.id);
    const fresh = done.filter((id) => !finished.current.has(id));
    if (finished.current.size && fresh.length) qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'jobs' });
    done.forEach((id) => finished.current.add(id));
  }, [jobs.data, qc]);

  const s = status.data;
  const active = (jobs.data ?? []).filter((j) => ACTIVE.has(j.status));
  const recent = (jobs.data ?? []).filter((j) => !ACTIVE.has(j.status)).slice(0, 5);
  return (
    <div className="datapage">
      <div className="intro">
        <h1>Data</h1>
        <p>Collect tournament matches, keep them up to date, and process them for the dashboard.</p>
      </div>
      {status.isError && <p className="notice notice--error">{(status.error as Error).message}</p>}
      {s && <StatusCards s={s} />}

      {(active.length > 0 || recent.length > 0) && (
        <section className="jobs">
          {active.map((j) => <JobCard key={j.id} job={j} onCancel={() => cancel.mutate(j.id)} />)}
          {recent.length > 0 && (
            <details className="more" open={active.length === 0}>
              <summary>Recent jobs</summary>
              <div className="more__body">{recent.map((j) => <JobCard key={j.id} job={j} onCancel={() => undefined} />)}</div>
            </details>
          )}
        </section>
      )}

      {s && !s.epic.logged_in && <SignIn s={s} />}
      {s && !s.parser.ready && (
        <p className="notice notice--warn">The replay parser isn't built yet. Open “Settings and advanced tools” below and choose “Rebuild the replay parser”
          {s.parser.dotnet ? '.' : ' after installing the .NET 10 SDK from dotnet.microsoft.com.'}</p>
      )}
      <Weekly busy={!!s?.busy} />
      <QuickActions />
      <Tournaments />
      {s && <Downloads s={s} />}
      <Import />
      {s && <Advanced s={s} />}
    </div>
  );
}
