import type { TableSpec } from '../types';

function toCsv(t: TableSpec) {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [t.columns.map(esc).join(','), ...t.rows.map((r) => r.map(esc).join(','))].join('\n');
}

export function DataTable({ table, maxRows }: { table: TableSpec; maxRows?: number }) {
  const rows = maxRows ? table.rows.slice(0, maxRows) : table.rows;
  const download = () => {
    const url = URL.createObjectURL(new Blob([toCsv(table)], { type: 'text/csv' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: `${table.title.replace(/\W+/g, '_')}.csv` });
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="table">
      <div className="table__head">
        <h3>{table.title}</h3>
        <button className="link" onClick={download}>Download CSV</button>
      </div>
      <div className="table__scroll">
        <table>
          <thead><tr>{table.columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === 'number' ? 'num' : ''}>{fmt(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function fmt(v: unknown) {
  if (v === null || v === undefined) return '–';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 3 });
  return String(v);
}
