import { useId, useState } from 'react';

/** Small "i" button that explains a term on hover, focus or tap. */
export function InfoTip({ text, label }: { text?: string | null; label: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!text) return null;
  return (
    <span className="infotip" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" className="infotip__btn" aria-label={`What is ${label}?`} aria-expanded={open}
              aria-describedby={open ? id : undefined}
              onClick={() => setOpen((o) => !o)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}>i</button>
      {open && <span role="tooltip" id={id} className="infotip__pop">{text}</span>}
    </span>
  );
}
