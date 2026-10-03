import { useState } from 'react';

type Plan = Record<string, string | number>;
const FIELDS: { key: string; label: string; step?: number; min?: number; max?: number }[] = [
  { key: 'teams_alive', label: 'Teams left', min: 1, max: 100 },
  { key: 'zone', label: 'Zone', min: 2, max: 12 },
  { key: 'progress', label: 'Progress through the zone (0–1)', step: 0.1, min: 0, max: 1 },
  { key: 'members', label: 'Teammates alive', min: 1, max: 4 },
  { key: 'hp', label: 'Health + shield per player', step: 10, min: 0, max: 200 },
  { key: 'outside_m', label: 'Metres outside the next zone', step: 10, min: 0 },
  { key: 'storm_m', label: 'Metres into the storm', step: 10, min: 0 },
  { key: 'off_centre', label: 'From the zone centre (0 centre – 1 edge)', step: 0.1, min: 0, max: 2 },
  { key: 'height_rank', label: 'Height rank (0 low – 1 high)', step: 0.1, min: 0, max: 1 },
  { key: 'enemies_50', label: 'Enemy teams within 50 m', min: 0 },
  { key: 'enemies_150', label: 'Enemy teams within 150 m', min: 0 },
  { key: 'hit_10s', label: 'Damage taken in the last 10 s', step: 10, min: 0 },
  { key: 'surge_rank', label: 'Net damage this zone (dealt − taken), rank (0 least – 1 most)', step: 0.1, min: 0, max: 1 },
  { key: 'kills', label: 'Eliminations so far', min: 0 },
  { key: 'team_pr_rank', label: "Team's Power Rankings rank (unranked = 100000)", step: 100, min: 1, max: 100000 },
  { key: 'lobby_pr_rank', label: "Lobby's median Power Rankings rank", step: 100, min: 1, max: 100000 },
];

/** Two plans side by side; "Compare" sends them to the model. */
export function PlanCompare({ initial, onCompare }: { initial: string; onCompare: (json: string) => void }) {
  const start = (() => { try { return JSON.parse(initial) as { a: Plan; b: Plan }; } catch { return { a: {}, b: {} }; } })();
  const [plans, setPlans] = useState<{ a: Plan; b: Plan }>(start);
  const set = (side: 'a' | 'b', key: string, value: string) =>
    setPlans((p) => ({ ...p, [side]: { ...p[side], [key]: key === 'name' ? value : value === '' ? '' : Number(value) } }));
  return (
    <form className="plans" onSubmit={(e) => { e.preventDefault(); onCompare(JSON.stringify(plans)); }}>
      <div className="plans__grid">
        <span />
        {(['a', 'b'] as const).map((s) => (
          <input key={s} className="input plans__name" value={String(plans[s].name ?? '')} onChange={(e) => set(s, 'name', e.target.value)} />
        ))}
        {FIELDS.map((f) => (
          <div key={f.key} className="plans__row">
            <label>{f.label}</label>
            {(['a', 'b'] as const).map((s) => (
              <input key={s} className={`input ${plans.a[f.key] !== plans.b[f.key] ? 'plans__diff' : ''}`} type="number"
                     step={f.step ?? 1} min={f.min} max={f.max} value={String(plans[s][f.key] ?? '')}
                     onChange={(e) => set(s, f.key, e.target.value)} />
            ))}
          </div>
        ))}
      </div>
      <button className="btn" type="submit">Compare plans</button>
      <p className="muted small">Change only what differs between the two plans (highlighted). The model was trained on the matches selected in the left panel.</p>
    </form>
  );
}
