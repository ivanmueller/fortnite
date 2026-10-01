import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import type { Filters } from '../types';
import { DataTable } from './DataTable';

const LABELS: Record<string, string> = {
  match_id: 'Match', match_date: 'Date', season: 'Season', region: 'Region', event_window_id: 'Event window',
  playlist: 'Playlist', players: 'Players', teams: 'Teams', is_server_replay: 'Server replay', minutes: 'Minutes',
};

export function MatchesView({ filters, bootId }: { filters: Filters; bootId?: string }) {
  const q = useQuery({ queryKey: ['matches', filters, bootId], queryFn: () => api.matches(filters, 500) });
  if (q.isPending) return <p className="muted">Loading matches…</p>;
  if (q.isError) return <p className="notice notice--error">{(q.error as Error).message}</p>;
  const d = q.data;
  return (
    <div className="result">
      <p className="headline">{d.total.toLocaleString()} matches in this selection{d.total > d.rows.length ? `, newest ${d.rows.length} shown` : ''}.</p>
      <DataTable table={{ title: 'Matches', columns: d.columns.map((c) => LABELS[c] ?? c), rows: d.rows }} />
    </div>
  );
}
