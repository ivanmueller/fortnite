import type { AnalysisResult } from '../types';
import { ChartView } from '../charts/ChartView';
import { DataTable } from './DataTable';
import { EvidenceStrip } from './EvidenceStrip';

export function ResultView({ result, alpha }: { result: AnalysisResult; alpha: number }) {
  return (
    <div className="result">
      <p className="headline">{result.headline}</p>
      {result.metrics.length > 0 && (
        <dl className="metrics">
          {result.metrics.map((m) => (
            <div key={m.label} className="metric" title={m.detail ?? undefined}>
              <dt>{m.label}</dt>
              <dd>{m.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {result.warnings.map((w) => <p key={w} className="notice notice--warn">{w}</p>)}
      <EvidenceStrip tests={result.tests} alpha={alpha} />
      {result.charts.length > 0 && (
        <div className="charts">
          {result.charts.map((c, i) => <ChartView key={`${c.title}-${i}`} spec={c} />)}
        </div>
      )}
      {result.tables.map((t) => <DataTable key={t.title} table={t} />)}
      {result.notes.length > 0 && (
        <aside className="notes" aria-label="How to read this">
          <h3>How to read this</h3>
          <ul>{result.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </aside>
      )}
      <p className="muted small">Computed in {result.elapsed_ms.toLocaleString()} ms.</p>
    </div>
  );
}
