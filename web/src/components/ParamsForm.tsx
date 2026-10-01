import type { ParamSpec } from '../types';

export function ParamsForm({ params, values, onChange }: {
  params: ParamSpec[]; values: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void;
}) {
  if (!params.length) return null;
  return (
    <div className="params">
      {params.map((p) => {
        const v = values[p.name] ?? p.default;
        const id = `param-${p.name}`;
        return (
          <div className="param" key={p.name} title={p.help ?? undefined}>
            {p.kind === 'boolean' ? (
              <label className="check">
                <input id={id} type="checkbox" checked={Boolean(v)} onChange={(e) => onChange({ ...values, [p.name]: e.target.checked })} />
                {p.label}
              </label>
            ) : (
              <>
                <label htmlFor={id}>{p.label}</label>
                {p.kind === 'select' ? (
                  <select id={id} value={String(v)} onChange={(e) => {
                    const opt = p.options?.find((o) => String(o.value) === e.target.value);
                    onChange({ ...values, [p.name]: opt ? opt.value : e.target.value });
                  }}>
                    {p.options?.map((o) => <option key={String(o.value)} value={String(o.value)}>{o.label}</option>)}
                  </select>
                ) : (
                  <input id={id} type="number" value={Number(v)} onChange={(e) => onChange({ ...values, [p.name]: Number(e.target.value) })} />
                )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
