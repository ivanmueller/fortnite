import { useMemo, useState } from 'react';
import type { Facets, Filters } from '../types';
import { emptyFilters } from '../state';

interface Props {
  title: string;
  filters: Filters;
  onChange: (f: Filters) => void;
  facets?: Facets;
  count?: number;
  showDataset?: boolean;
  datasets?: { key: Filters['dataset']; label: string; available: boolean; matches: number }[];
}

function toggle(list: string[], v: string) {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

export function SelectionPanel({ title, filters, onChange, facets, count, showDataset, datasets }: Props) {
  const [windowQuery, setWindowQuery] = useState('');
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const windowMatches = useMemo(() => {
    const q = windowQuery.trim().toLowerCase();
    if (!q || !facets) return [];
    return facets.event_windows.filter((w) => w.value.toLowerCase().includes(q) && !filters.event_windows.includes(w.value)).slice(0, 8);
  }, [windowQuery, facets, filters.event_windows]);
  const id = title.replace(/\W+/g, '-').toLowerCase();

  return (
    <section className="selection" aria-labelledby={`${id}-h`}>
      <header className="selection__head">
        <h2 id={`${id}-h`}>{title}</h2>
        {count !== undefined && <span className="selection__count">{count.toLocaleString()} matches</span>}
      </header>

      {showDataset && datasets && (
        <div className="field">
          <span className="field__label">Data</span>
          <div className="segmented" role="radiogroup" aria-label="Dataset">
            {datasets.map((d) => (
              <button key={d.key} role="radio" aria-checked={filters.dataset === d.key}
                      className={filters.dataset === d.key ? 'is-on' : ''}
                      onClick={() => onChange({ ...emptyFilters(d.key) })}
                      title={d.available ? `${d.matches.toLocaleString()} matches` : 'No tables yet'}>
                {d.label}{!d.available && <span className="segmented__note"> (empty)</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="field field--dates">
        <label className="field__label" htmlFor={`${id}-from`}>Dates</label>
        <div className="dates">
          <input id={`${id}-from`} type="date" value={filters.date_from ?? ''} min={facets?.date_min ?? undefined}
                 max={facets?.date_max ?? undefined} onChange={(e) => set({ date_from: e.target.value || null })} />
          <span aria-hidden>to</span>
          <input aria-label="End date" type="date" value={filters.date_to ?? ''} min={facets?.date_min ?? undefined}
                 max={facets?.date_max ?? undefined} onChange={(e) => set({ date_to: e.target.value || null })} />
        </div>
      </div>

      <ChipField label="Seasons" values={facets?.seasons ?? []} selected={filters.seasons}
                 onToggle={(v) => set({ seasons: toggle(filters.seasons, v) })} />
      <ChipField label="Regions" values={facets?.regions ?? []} selected={filters.regions}
                 onToggle={(v) => set({ regions: toggle(filters.regions, v) })} />

      <div className="field">
        <label className="field__label" htmlFor={`${id}-win`}>Event windows</label>
        <input id={`${id}-win`} type="search" placeholder="Search, e.g. W14_EU" value={windowQuery}
               onChange={(e) => setWindowQuery(e.target.value)} />
        {windowMatches.length > 0 && (
          <ul className="suggest">
            {windowMatches.map((w) => (
              <li key={w.value}>
                <button onClick={() => { set({ event_windows: [...filters.event_windows, w.value] }); setWindowQuery(''); }}>
                  {w.value} <span className="muted">{w.count}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {filters.event_windows.length > 0 && (
          <div className="chips">
            {filters.event_windows.map((w) => (
              <button key={w} className="chip is-on" onClick={() => set({ event_windows: filters.event_windows.filter((x) => x !== w) })}
                      aria-label={`Remove ${w}`}>{w} ×</button>
            ))}
          </div>
        )}
      </div>

      <label className="check">
        <input type="checkbox" checked={filters.server_only} onChange={(e) => set({ server_only: e.target.checked })} />
        Server replays only
      </label>

      <button className="link" onClick={() => onChange(emptyFilters(filters.dataset))}>Clear filters</button>
    </section>
  );
}

function ChipField({ label, values, selected, onToggle }: {
  label: string; values: { value: string; count: number }[]; selected: string[]; onToggle: (v: string) => void;
}) {
  if (!values.length) return null;
  return (
    <fieldset className="field">
      <legend className="field__label">{label}</legend>
      <div className="chips">
        {values.map((v) => (
          <button key={v.value} className={`chip ${selected.includes(v.value) ? 'is-on' : ''}`}
                  aria-pressed={selected.includes(v.value)} onClick={() => onToggle(v.value)}>
            {v.value} <span className="chip__n">{v.count}</span>
          </button>
        ))}
      </div>
    </fieldset>
  );
}
