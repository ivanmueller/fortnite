import type { Guide } from '../types';

/** "About this page": the research question, method, how to conclude, and limits. */
export function GuidePanel({ guide, open, onToggle, summary, label = 'How this is measured' }: {
  guide: Guide; open: boolean; onToggle: (open: boolean) => void; summary?: string; label?: string;
}) {
  return (
    <details className="guide" open={open} onToggle={(e) => onToggle((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{label}</summary>
      <div className="guide__body">
        {guide.question !== summary && <p className="guide__question">{guide.question}</p>}
        <div className="guide__cols">
          <section>
            <h3>How it's measured</h3>
            <ul>{guide.method.map((m) => <li key={m}>{m}</li>)}</ul>
          </section>
          <section>
            <h3>Drawing a conclusion</h3>
            <ul>{guide.conclude.map((m) => <li key={m}>{m}</li>)}</ul>
            {guide.limits.length > 0 && (
              <>
                <h3>What this page can't tell you</h3>
                <ul>{guide.limits.map((m) => <li key={m}>{m}</li>)}</ul>
              </>
            )}
          </section>
        </div>
        <p className="guide__hint">Hover or tap the <span className="infotip__btn infotip__btn--static" aria-hidden>i</span> next to
          any number or test for its definition.</p>
      </div>
    </details>
  );
}
