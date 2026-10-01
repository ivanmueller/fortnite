import type { AnalysisResult, Guide } from '../types';
import { ChartView } from '../charts/ChartView';
import { ConclusionPanel } from './ConclusionPanel';
import { DataTable } from './DataTable';
import { EvidenceStrip } from './EvidenceStrip';
import { InfoTip } from './InfoTip';

interface Props { result: AnalysisResult; alpha: number; guide?: Guide | null; title: string; scope: string }

export function ResultView({ result, alpha, guide, title, scope }: Props) {
  const terms = guide?.terms ?? {};
  return (
    <div className="result">
      <p className="headline">{result.headline}</p>
      {result.metrics.length > 0 && (
        <dl className="metrics">
          {result.metrics.map((m) => (
            <div key={m.label} className="metric">
              <dt>{m.label} <InfoTip label={m.label} text={terms[m.label] ?? m.detail} /></dt>
              <dd>{m.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {result.warnings.map((w) => <p key={w} className="notice notice--warn">{w}</p>)}
      {result.conclusion && <ConclusionPanel conclusion={result.conclusion} title={title} scope={scope} />}
      <EvidenceStrip tests={result.tests} alpha={alpha} terms={terms} />
      {result.charts.length > 0 && (
        <div className="charts">
          {result.charts.map((c, i) => <ChartView key={`${c.title}-${i}`} spec={c} help={guide?.charts[c.title]} />)}
        </div>
      )}
      {result.tables.map((t) => <DataTable key={t.title} table={t} />)}
      {result.notes.length > 0 && (
        <aside className="notes" aria-label="Notes on this result">
          <h3>Notes on this result</h3>
          <ul>{result.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </aside>
      )}
      <p className="muted small">Computed in {result.elapsed_ms.toLocaleString()} ms.</p>
    </div>
  );
}
