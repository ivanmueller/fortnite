import { lazy, Suspense, useCallback, useMemo, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../api';
import type { AnalysisInfo, ChartSpec, Filters } from '../types';
import { DataTable } from './DataTable';

const MatchMap = lazy(() => import('../charts/MatchMap').then((m) => ({ default: m.MatchMap })));

type Weapon = { t: number; name: string; rarity: string | null };
type Pickup = { t: number; item: string; category: string | null; count: number | null };
type Player = {
  id: number; name: string; death: number | null; knocks: number[]; revives: number[];
  hp: { t: number[]; health: number[]; shield: number[] } | null; weapons: Weapon[]; pickups: Pickup[];
};
type Option = { key: string; label: string; ev: number };
type Decision = {
  t: number; zone: number; engine: string; actual: string; stake: number; followed: boolean; options: Option[];
  fight: { p_win: number; if_won: number; if_lost: number } | null;
  knew: { outside_m: number; hp: number; teams: number; members: number; seen: number; seen_close: number; surge: string; kills: number };
};
type Room = {
  t0: number; t1: number; team_name: string; match: string; scoring: string; actions: Record<string, string>;
  games: { match: string; game: number; date: string; placement: number | null; points: number | null }[];
  plans: { zone: number; t: number; reasons: string[] }[];
  hud: { players: Player[]; builds: { t: number; material: string | null }[]; has: Record<string, boolean> };
  surge: { t: number[]; zone: number[]; net: number[]; dealt: number[]; taken: number[]; lines: Record<string, number>;
           episodes: { t: number; zone: number; surged: boolean | null }[]; rule: string | null } | null;
  decisions: Decision[];
};

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const RARITY: Record<string, string> = {
  common: '#9aa3ad', uncommon: '#3fa34d', rare: '#2f80d1', epic: '#9b4fd6', legendary: '#d98a1f', mythic: '#c9a400',
};
const MATERIAL: Record<string, string> = { wood: 'Wood', stone: 'Brick', metal: 'Metal' };

function hpAt(p: Player, t: number): { health: number; shield: number } | null {
  if (!p.hp) return null;
  let i = -1;
  for (let k = 0; k < p.hp.t.length && p.hp.t[k] <= t; k++) i = k;
  return i < 0 ? null : { health: p.hp.health[i], shield: p.hp.shield[i] };
}

function statusAt(p: Player, t: number): 'alive' | 'knocked' | 'out' {
  if (p.death !== null && t >= p.death) return 'out';
  const k = [...p.knocks].reverse().find((x) => x <= t);
  if (k === undefined) return 'alive';
  return p.revives.some((r) => r > k && r <= t) ? 'alive' : 'knocked';
}

function loadoutAt(p: Player, t: number): { weapons: Weapon[]; current: Weapon | null } {
  const seen = new Map<string, Weapon>();
  let current: Weapon | null = null;
  for (const w of p.weapons) { if (w.t > t) break; seen.delete(w.name); seen.set(w.name, w); current = w; }
  return { weapons: [...seen.values()].slice(-5).reverse(), current };
}

function pickedUpAt(p: Player, t: number): { mats: [string, number][]; heals: number } {
  const mats = new Map<string, number>();
  let heals = 0;
  for (const x of p.pickups) {
    if (x.t > t) break;
    if (x.category === 'materials') {
      const s = x.item.toLowerCase();
      const name = s.includes('wood') ? 'Wood' : s.includes('stone') ? 'Brick' : s.includes('metal') ? 'Metal' : x.item;
      mats.set(name, (mats.get(name) ?? 0) + (x.count ?? 0));
    } else if (x.category === 'heal/shield') heals += x.count ?? 1;
  }
  return { mats: [...mats.entries()], heals };
}

function Bar({ value, max, kind }: { value: number; max: number; kind: 'health' | 'shield' }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className={`hpbar hpbar--${kind}`} role="meter" aria-label={kind} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}>
      <div className="hpbar__fill" style={{ width: `${pct}%` }} />
      <span className="hpbar__num">{Math.round(value)}</span>
    </div>
  );
}

function PlayerCard({ p, t }: { p: Player; t: number }) {
  const status = statusAt(p, t);
  const hp = hpAt(p, t);
  const { weapons, current } = loadoutAt(p, t);
  const picked = pickedUpAt(p, t);
  return (
    <div className={`hud__player hud__player--${status}`}>
      <div className="hud__name">
        <strong>{p.name}</strong>
        {status === 'knocked' && <span className="hud__tag hud__tag--knocked">Knocked</span>}
        {status === 'out' && <span className="hud__tag">Eliminated {p.death !== null ? fmt(p.death) : ''}</span>}
      </div>
      {status !== 'out' && (hp ? (
        <div className="hud__bars"><Bar value={hp.shield} max={100} kind="shield" /><Bar value={hp.health} max={100} kind="health" /></div>
      ) : <p className="muted small">No health data yet in this game.</p>)}
      {status !== 'out' && weapons.length > 0 && (
        <ul className="hud__loadout" aria-label="Weapons seen in hand">
          {weapons.map((w) => (
            <li key={w.name} className={current?.name === w.name ? 'is-current' : ''} style={{ borderColor: RARITY[w.rarity ?? ''] ?? 'var(--line)' }}
                title={`${w.name}${w.rarity ? `, ${w.rarity}` : ''}${current?.name === w.name ? ' (in hand)' : ''}`}>{w.name}</li>
          ))}
        </ul>
      )}
      {status !== 'out' && (picked.mats.length > 0 || picked.heals > 0) && (
        <p className="hud__picked">Picked up: {[...picked.mats.map(([m, n]) => `${n} ${m.toLowerCase()}`), ...(picked.heals ? [`${picked.heals} heal${picked.heals > 1 ? 's' : ''}`] : [])].join(', ')}</p>
      )}
    </div>
  );
}

function TeamCard({ room, t }: { room: Room; t: number }) {
  const s = room.surge;
  let net: number | null = null, zone: number | null = null, line: number | null = null;
  if (s && s.t.length) {
    const i = Math.max(0, Math.min(s.t.length - 1, Math.floor((t - s.t[0]) / Math.max(1, s.t[1] - s.t[0] || 5))));
    net = s.net[i]; zone = s.zone[i]; line = zone ? s.lines[String(zone)] ?? null : null;
  }
  const built = new Map<string, number>();
  for (const b of room.hud.builds) { if (b.t > t) break; const m = MATERIAL[b.material ?? ''] ?? 'Other'; built.set(m, (built.get(m) ?? 0) + 1); }
  const surgeNow = s?.episodes.find((e) => e.t <= t && t - e.t < 15);
  return (
    <div className="hud__team">
      <h4>Team</h4>
      {net !== null && zone ? (
        <div className={`hud__surge ${line !== null ? (net >= line ? 'is-above' : 'is-below') : ''}`}>
          <span className="hud__surge-num">{net > 0 ? '+' : ''}{net}</span>
          <span>net damage since zone {zone} appeared{line !== null ? `; safe line about ${line}` : ''}</span>
        </div>
      ) : <p className="muted small">{s ? 'Surge counts from zone 2: the score starts when it appears.' : 'No damage data in this game for a surge score.'}</p>}
      {surgeNow && <p className={`hud__alert ${surgeNow.surged ? 'is-bad' : ''}`}>Surge check, zone {surgeNow.zone}: {surgeNow.surged ? 'you were surged' : surgeNow.surged === false ? 'you were safe' : 'you were out'}</p>}
      {built.size > 0 && <p className="hud__picked">Pieces built: {[...built.entries()].map(([m, n]) => `${n} ${m.toLowerCase()}`).join(', ')}</p>}
      <p className="muted small">Material and item counts aren't in the replay data yet: weapons are the ones seen in hand.</p>
    </div>
  );
}

function DecisionCard({ room, t, onJump }: { room: Room; t: number; onJump: (t: number) => void }) {
  const ds = room.decisions;
  const d = useMemo(() => [...ds].reverse().find((x) => x.t <= t + 0.5 && t - x.t < 20) ?? null, [ds, t]);
  const next = useMemo(() => ds.find((x) => x.t > t + 0.5) ?? null, [ds, t]);
  if (!d) {
    return (
      <div className="call call--idle">
        <p className="muted">No decision point at this moment.</p>
        {next && <button className="btn btn--ghost btn--sm" onClick={() => onJump(next.t)}>Next decision at {fmt(next.t)}</button>}
      </div>
    );
  }
  const keys = new Set(d.options.map((o) => o.key));
  const youKey = keys.has(d.actual) ? d.actual : d.actual === 'rotate_alt' && keys.has('rotate') ? 'rotate' : 'hold';
  const max = Math.max(...d.options.map((o) => o.ev), 1);
  const label = (k: string) => room.actions[k] ?? k;
  return (
    <div className={`call ${d.followed ? 'call--ok' : 'call--miss'}`}>
      <div className="call__head">
        <h3>{fmt(d.t)}, zone {d.zone}</h3>
        <p className="call__verdict">{d.followed ? 'Matched the best option' : `${d.stake.toFixed(1)} points at stake`}</p>
      </div>
      <p className="call__line"><span>You</span> {label(d.actual)}</p>
      <p className="call__line"><span>Best</span> {label(d.engine)}</p>
      <ul className="call__options" aria-label="Expected points for each option">
        {d.options.map((o) => (
          <li key={o.key} className={`${o.key === d.engine ? 'is-best' : ''} ${o.key === youKey ? 'is-you' : ''}`}>
            <span className="call__opt">{o.label}{o.key === youKey ? ' (you)' : ''}</span>
            <span className="call__bar"><i style={{ width: `${Math.max(2, (o.ev / max) * 100)}%` }} /></span>
            <span className="call__ev">{o.ev.toFixed(1)}</span>
          </li>
        ))}
      </ul>
      {d.fight && (
        <p className="call__fight">Fight: {Math.round(d.fight.p_win * 100)}% to win at your health. Won: {d.fight.if_won.toFixed(1)} points
          (with the elimination). Lost: {d.fight.if_lost.toFixed(1)} (placed now, {d.knew.teams} teams left).</p>
      )}
      <p className="call__knew">What you knew: {d.knew.outside_m > 0 ? `${d.knew.outside_m} m outside the next zone` : 'inside the next zone'}, health and
        shield {d.knew.hp}, {d.knew.members} alive, {d.knew.teams} teams left, {d.knew.seen} team{d.knew.seen === 1 ? '' : 's'} within 120 m
        {d.knew.seen_close ? ` (${d.knew.seen_close} within 50 m)` : ''}, surge {d.knew.surge} the cut-off.</p>
    </div>
  );
}

interface Props { info?: AnalysisInfo; filters: Filters; bootId?: string; enabled: boolean; team: string; onTeam: (v: string) => void }

/** One team's game: the replay in the centre, both players' HUD beside it, and the decision at the current moment below. */
export function MatchRoom({ filters, bootId, enabled, team, onTeam }: Props) {
  const [draft, setDraft] = useState(team);
  const [match, setMatch] = useState('');
  const [t, setT] = useState(0);
  const [jump, setJump] = useState<number | undefined>(undefined);
  const onJump = useCallback((v: number) => setJump((j) => (j === v ? v + 0.001 : v)), []);
  const params = { ...(team ? { team } : {}), ...(match ? { match } : {}) };
  const run = useQuery({
    queryKey: ['run', 'match_room', filters, params, bootId],
    queryFn: () => api.run('match_room', filters, params),
    enabled,
    placeholderData: keepPreviousData,
  });
  const r = run.data?.status === 'ok' ? run.data : undefined;
  const chart: ChartSpec | undefined = r?.charts.find((c) => c.kind === 'match_replay');
  const room = chart?.options as unknown as Room | undefined;
  const game = room?.games.find((g) => g.match === room.match);
  const bigCalls = useMemo(() => (room?.decisions ?? []).filter((d) => !d.followed && d.stake >= 1).sort((a, b) => b.stake - a.stake).slice(0, 6), [room]);

  return (
    <section className={`room ${run.isFetching ? 'is-busy' : ''}`} aria-busy={run.isFetching}>
      <form className="room__inputs" onSubmit={(e) => { e.preventDefault(); onTeam(draft.trim()); setMatch(''); }}>
        <label>Players
          <input className="input" value={draft} placeholder="e.g. clix, rapid" onChange={(e) => setDraft(e.target.value)} />
        </label>
        {room && (
          <label>Game
            <select className="input" value={room.match} onChange={(e) => setMatch(e.target.value)}>
              {room.games.map((g) => (
                <option key={g.match} value={g.match}>{`Game ${g.game}, ${g.date}: placed ${g.placement ?? '?'}, ${g.points ?? '?'} points`}</option>
              ))}
            </select>
          </label>
        )}
        <button className="btn" type="submit">Open</button>
      </form>

      {run.isPending && <p className="muted">Loading the game…</p>}
      {run.isError && <p className="notice notice--error">{(run.error as Error).message}</p>}
      {run.data?.status === 'empty' && <p className="notice">{run.data.message}</p>}
      {run.isFetching && room && <p className="muted small">Pricing every decision in the selection; the first game takes up to a minute.</p>}

      {r && !room && (
        <>
          <p className="room__ask">{r.headline}</p>
          {r.tables.map((tb) => <DataTable key={tb.title} table={tb} maxRows={30} />)}
        </>
      )}

      {r && room && chart && (
        <>
          <header className="room__band">
            <h2>{room.team_name}</h2>
            <p>{game ? `Game ${game.game} of ${room.games.length}: placed ${game.placement ?? '?'}, ${game.points ?? '?'} points` : ''}
              <span className="room__scoring">{room.scoring}</span></p>
          </header>
          {r.warnings.map((w) => <p key={w} className="notice notice--warn">{w}</p>)}
          <div className="room__stage">
            <div className="room__map">
              <Suspense fallback={<p className="muted">Loading the map…</p>}>
                <MatchMap spec={chart} time={jump} onTime={setT} bare />
              </Suspense>
            </div>
            <aside className="hud" aria-label="Team at this moment">
              {room.hud.players.map((p) => <PlayerCard key={p.id} p={p} t={t} />)}
              <TeamCard room={room} t={t} />
            </aside>
          </div>
          <div className="room__below">
            <DecisionCard room={room} t={t} onJump={onJump} />
            <div className="room__side">
              {bigCalls.length > 0 && (
                <div className="room__calls">
                  <h4>Biggest calls this game</h4>
                  <ol>
                    {bigCalls.map((d) => (
                      <li key={d.t}><button className="link" onClick={() => onJump(d.t)}>{fmt(d.t)}, zone {d.zone}</button>: {room.actions[d.actual] ?? d.actual},
                        best was {(room.actions[d.engine] ?? d.engine).toLowerCase()} (+{d.stake.toFixed(1)})</li>
                    ))}
                  </ol>
                </div>
              )}
              {(() => {
                const plan = [...room.plans].reverse().find((p) => p.t <= t + 0.5);
                return plan ? (
                  <div className="room__plan">
                    <h4>Zone {plan.zone}: the rotation plan</h4>
                    <ul>{plan.reasons.slice(0, 5).map((x) => <li key={x}>{x}</li>)}</ul>
                  </div>
                ) : null;
              })()}
            </div>
          </div>
          <details className="more">
            <summary>Notes</summary>
            <div className="more__body"><ul className="notes">{r.notes.map((n) => <li key={n}>{n}</li>)}</ul></div>
          </details>
        </>
      )}
    </section>
  );
}
