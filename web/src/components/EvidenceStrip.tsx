import type { TestRow } from '../types';

// p-values on a log track from 1 (left) to 1e-12 (right), with the threshold marked.
const MIN_LOG = -12;
const pos = (p: number | null) => (p === null ? 0 : Math.min(1, Math.max(0, Math.log10(Math.max(p, 1e-300)) / MIN_LOG)));
const fmtP = (p: number | null) =>
  p === null ? 'n/a' : p < 0.001 ? `p < 0.001` : `p = ${p.toFixed(3)}`;

export function EvidenceStrip({ tests, alpha }: { tests: TestRow[]; alpha: number }) {
  if (!tests.length) return null;
  const groups = [...new Set(tests.map((t) => t.group))];
  const threshold = pos(alpha);
  return (
    <section className="evidence" aria-label="Statistical tests">
      <div className="evidence__scale" aria-hidden>
        <span />
        <span className="evidence__ends"><span>Consistent with chance</span><span>Strong evidence of a pattern</span></span>
      </div>
      {groups.map((g, gi) => {
        const rows = tests.filter((t) => t.group === g);
        const sig = rows.filter((t) => t.significant).length;
        const list = (
          <ul>
            {rows.map((t, i) => (
              <li key={i} className={`test ${t.significant ? 'is-sig' : ''}`}>
                <div className="test__name">
                  {t.name}
                  <span className="test__value">{t.value}</span>
                </div>
                <div className="track" role="img"
                     aria-label={`${t.name}: ${fmtP(t.p)}, ${t.significant ? 'significant' : 'not significant'} at p < ${alpha}`}>
                  <span className="track__threshold" style={{ left: `${threshold * 100}%` }} title={`p = ${alpha}`} />
                  <span className="track__dot" style={{ left: `${pos(t.p) * 100}%` }} />
                </div>
                <div className="test__p">
                  {fmtP(t.p)}
                  <span className="muted"> n={t.n.toLocaleString()}</span>
                </div>
                {t.significant && t.reading && <p className="test__reading">{t.reading}</p>}
              </li>
            ))}
          </ul>
        );
        // The first group is the headline evidence and stays open; the rest fold away.
        return gi === 0 || groups.length <= 2 ? (
          <div className="evidence__group" key={g}><h3>{g}</h3>{list}</div>
        ) : (
          <details className="evidence__group" key={g}>
            <summary>
              <h3>{g}</h3>
              <span className={sig ? 'sig-count is-sig' : 'sig-count'}>
                {sig} of {rows.length} {rows.length === 1 ? 'test' : 'tests'} below the threshold
              </span>
            </summary>
            {list}
          </details>
        );
      })}
    </section>
  );
}
