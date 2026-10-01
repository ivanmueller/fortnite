import { useState } from 'react';
import type { Conclusion, Strength } from '../types';

const STRENGTH_LABEL: Record<Strength, string> = {
  strong: 'Strong evidence', clear: 'Clear evidence', weak: 'Weak, below the bar', none: 'No evidence',
};
const fmtP = (p: number | null) => (p === null ? '' : p < 0.001 ? 'p < 0.001' : `p = ${p.toFixed(3)}`);

function asMarkdown(c: Conclusion, title: string, scope: string): string {
  const lines = [`## ${title}: ${c.title}`, '', `Selection: ${scope}`, '', c.summary, ''];
  if (c.evidence.length) {
    lines.push('Evidence:');
    c.evidence.forEach((e) => lines.push(`- ${e.label}: ${e.detail} ${STRENGTH_LABEL[e.strength]} (${fmtP(e.p)}, n=${e.n}).`));
    lines.push('');
  }
  lines.push('Reliability:');
  c.reliability.forEach((r) => lines.push(`- [${r.ok ? 'ok' : '!'}] ${r.label}: ${r.detail}`));
  if (c.next_steps.length) {
    lines.push('', 'Next steps:');
    c.next_steps.forEach((s, i) => lines.push(`${i + 1}. ${s}`));
  }
  return lines.join('\n');
}

export function ConclusionPanel({ conclusion: c, title, scope }: { conclusion: Conclusion; title: string; scope: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(asMarkdown(c, title, scope));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked */ }
  };
  const issues = c.reliability.filter((r) => !r.ok).length;
  return (
    <section className={`conclusion conclusion--${c.status}`} aria-labelledby="conclusion-h">
      <header className="conclusion__head">
        <div>
          <span className="conclusion__kicker">Conclusion</span>
          <h2 id="conclusion-h">{c.title}</h2>
        </div>
        <button className="link" onClick={copy}>{copied ? 'Copied' : 'Copy as text'}</button>
      </header>
      <p className="conclusion__summary">{c.summary}</p>

      <div className="conclusion__grid">
        {c.evidence.length > 0 && (
          <div>
            <h3>Evidence behind it</h3>
            <ul className="evidence-list">
              {c.evidence.map((e) => (
                <li key={e.label}>
                  <span className={`badge badge--${e.strength}`}>{STRENGTH_LABEL[e.strength]}</span>
                  <span className="evidence-list__label">{e.label}</span>
                  <span className="evidence-list__detail">{e.detail} <span className="muted">{fmtP(e.p)}, n={e.n.toLocaleString()}</span></span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div>
          <h3>Can you trust it? {issues > 0 ? <span className="muted">{issues} to check</span> : <span className="muted">All checks pass</span>}</h3>
          <ul className="checks">
            {c.reliability.map((r) => (
              <li key={r.label} className={r.ok ? 'is-ok' : 'is-warn'}>
                <span className="checks__mark" aria-label={r.ok ? 'Passes' : 'Needs attention'}>{r.ok ? '✓' : '!'}</span>
                <span><strong>{r.label}.</strong> {r.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {c.next_steps.length > 0 && (
        <div className="conclusion__next">
          <h3>Next steps</h3>
          <ol>{c.next_steps.map((s) => <li key={s}>{s}</li>)}</ol>
        </div>
      )}
    </section>
  );
}
