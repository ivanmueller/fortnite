import { useState } from 'react';
import type { Filters } from '../types';

/** Runs every study on the current selection and downloads findings.md (for an AI), findings.json and findings.csv. */
export function ExportPanel({ filters, count }: { filters: Filters; count?: number }) {
  const [team, setTeam] = useState('');
  const [asOf, setAsOf] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setMsg(null);
    const t0 = Date.now();
    try {
      const res = await fetch('/api/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filters: { ...filters, date_from: filters.date_from || null, date_to: filters.date_to || null,
                     playlist_contains: filters.playlist_contains?.trim() || null },
          team: team.trim(), as_of: asOf,
        }),
      });
      if (!res.ok) {
        let detail = `${res.status} ${res.statusText}`;
        try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
        throw new Error(detail);
      }
      const blob = await res.blob();
      const name = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') ?? '')?.[1] ?? 'vantage-findings.zip';
      const url = URL.createObjectURL(blob);
      Object.assign(document.createElement('a'), { href: url, download: name }).click();
      URL.revokeObjectURL(url);
      setMsg(`Downloaded ${name} in ${Math.round((Date.now() - t0) / 1000)} s.`);
    } catch (e) {
      setMsg(`Export failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <details className="export">
      <summary>Export all findings</summary>
      <p className="muted small">Runs every study on this selection{count !== undefined ? ` (${count} matches)` : ''} and downloads
        findings.md for an AI to read, plus findings.json and a findings.csv sheet.</p>
      <label>Players (optional)
        <input className="input" value={team} placeholder="Adds the team's own studies" onChange={(e) => setTeam(e.target.value)} />
      </label>
      <label>Event's first day (optional)
        <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
      </label>
      <button className="btn" type="button" disabled={busy || count === 0} onClick={run}>{busy ? 'Running every study…' : 'Export findings'}</button>
      {busy && <p className="muted small">This takes a few minutes: every study runs on the whole selection.</p>}
      {msg && <p className={`small ${msg.startsWith('Export failed') ? 'notice notice--error' : 'muted'}`}>{msg}</p>}
    </details>
  );
}
