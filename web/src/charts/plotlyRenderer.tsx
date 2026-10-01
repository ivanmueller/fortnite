// Plotly adapter: neutral ChartSpec -> Plotly traces + layout.
// To try another library, write a sibling renderer with the same props and register it in ChartView.
import createPlotlyComponent from 'react-plotly.js/factory';
import Plotly from 'plotly.js-dist-min';
import type { ChartSpec, Series } from '../types';
import { GRID, INK, MUTED, seriesColors } from './palette';

const Plot = createPlotlyComponent(Plotly);
const FONT = { family: 'Barlow, system-ui, sans-serif', size: 13, color: INK };
const nums = (a?: (number | null)[]) => (a ?? []).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));

function polarBins(theta: number[], bins: number, normalize: boolean) {
  const width = 360 / bins;
  const counts = new Array(bins).fill(0);
  for (const t of theta) counts[Math.floor((((t % 360) + 360) % 360) / width) % bins] += 1;
  const total = theta.length || 1;
  return {
    r: normalize ? counts.map((c) => c / total) : counts,
    theta: counts.map((_, i) => i * width + width / 2),
    width,
  };
}

function traces(spec: ChartSpec): unknown[] {
  const o = spec.options;
  const colors = seriesColors(spec.series.map((s) => s.name));
  const multi = spec.series.length > 1;
  return spec.series.map((s: Series, i) => {
    const color = colors[i];
    switch (spec.kind) {
      case 'bar':
        if (spec.options.horizontal) {
          return { type: 'bar', orientation: 'h', name: s.name, x: nums(s.y), y: s.x, marker: { color } };
        }
      // falls through
      case 'stacked_bar':
        return { type: 'bar', name: s.name, x: s.x, y: s.y, marker: { color } };
      case 'line':
        return { type: 'scatter', mode: 'lines+markers', name: s.name, x: s.x, y: s.y,
                 line: { color, width: 2.5 }, marker: { color, size: 7 } };
      case 'histogram': {
        const [lo, hi] = o.range ?? [Math.min(...nums(s.values)), Math.max(...nums(s.values))];
        const size = (hi - lo) / (o.bins ?? 20);
        // Values equal to the upper bound (e.g. u = 1.0) must land in the last bin, so clamp just inside it.
        const x = nums(s.values).map((v) => Math.min(Math.max(v, lo), hi - size * 1e-6));
        return { type: 'histogram', name: s.name, x,
                 xbins: { start: lo, end: hi, size },
                 histnorm: o.normalize ? 'probability' : '', opacity: multi ? 0.6 : 1,
                 marker: { color, line: { color: '#F7F9FB', width: 1 } } };
      }
      case 'polar_histogram': {
        const b = polarBins(nums(s.theta), o.bins ?? 24, !!o.normalize);
        if (multi) {
          // Overlapping bars hide each other; outlined shapes keep every series readable.
          return { type: 'scatterpolar', mode: 'lines', name: s.name, r: [...b.r, b.r[0]], theta: [...b.theta, b.theta[0]],
                   fill: 'toself', fillcolor: color + '26', line: { color, width: 2.5 } };
        }
        return { type: 'barpolar', name: s.name, r: b.r, theta: b.theta, width: b.width,
                 opacity: multi ? 0.6 : 0.9, marker: { color, line: { color: '#F7F9FB', width: 1 } } };
      }
      case 'box':
        return { type: 'box', name: s.name, y: nums(s.values), boxpoints: false,
                 marker: { color }, line: { color, width: 1.5 }, fillcolor: color + '33' };
      case 'map_points':
        if (s.text) {  // labelled reference points, e.g. named places
          return { type: 'scatter', mode: 'markers+text', name: s.name, x: s.x, y: s.y, text: s.text,
                   textposition: 'top center', textfont: { ...FONT, size: 10, color: INK },
                   marker: { color: INK, size: 6, symbol: 'diamond' }, hoverinfo: 'text' };
        }
        return { type: 'scattergl', mode: 'markers', name: s.name, x: s.x, y: s.y,
                 marker: { color, size: spec.options.marker_size ?? 4, opacity: spec.options.marker_size ? 0.8 : 0.55 } };
      default:
        return {};
    }
  });
}

function layout(spec: ChartSpec): Record<string, unknown> {
  const o = spec.options;
  const axis = (title?: string) => ({
    title: title ? { text: title, font: { ...FONT, size: 12, color: MUTED } } : undefined,
    gridcolor: GRID, zerolinecolor: GRID, linecolor: GRID, tickfont: { ...FONT, size: 12, color: MUTED },
    automargin: true,
  });
  const base: Record<string, unknown> = {
    font: FONT,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    margin: { l: 56, r: 16, t: 12, b: 48 },
    showlegend: spec.series.length > 1,
    legend: { orientation: 'h', y: -0.22, font: { ...FONT, size: 12 } },
    hoverlabel: { font: FONT, bgcolor: '#FFFFFF', bordercolor: GRID },
    xaxis: axis(o.x_label),
    yaxis: { ...axis(o.y_label), autorange: o.reverse_y ? 'reversed' : true },
    barmode: spec.kind === 'stacked_bar' ? 'stack' : spec.kind === 'histogram' ? 'overlay' : 'group',
    bargap: 0.08,
  };
  if (spec.kind === 'bar' && o.horizontal) {
    // Sideways bars: measure names down the left, values along the bottom.
    base.yaxis = { ...axis(o.y_label), type: 'category', automargin: true };
    base.xaxis = { ...axis(o.x_label), type: 'linear', zeroline: true, zerolinecolor: INK, zerolinewidth: 1.5 };
    base.margin = { l: 16, r: 24, t: 12, b: 56 };
    return base;
  }
  if (spec.kind === 'bar' || spec.kind === 'stacked_bar') {
    // Labels like "10" or "Phase 3" are categories, not numbers; ISO dates stay on a date axis.
    const xs = spec.series.flatMap((s) => s.x ?? []);
    const isDate = xs.length > 0 && xs.every((x) => typeof x === 'string' && /^\d{4}-\d{2}-\d{2}/.test(x));
    if (!isDate) (base.xaxis as Record<string, unknown>).type = 'category';
    else {
      (base.xaxis as Record<string, unknown>).tickformat = '%b %d';
      if (new Set(xs).size <= 3) (base.xaxis as Record<string, unknown>).type = 'category';
    }
  }
  if (spec.kind === 'stacked_bar') (base.legend as Record<string, unknown>).traceorder = 'normal';
  if (spec.kind === 'polar_histogram') {
    base.polar = {
      bgcolor: 'rgba(0,0,0,0)',
      angularaxis: {
        rotation: 0, direction: 'counterclockwise', gridcolor: GRID, linecolor: GRID,
        tickfont: { ...FONT, size: 11, color: MUTED },
        ...(o.zero_label ? { tickmode: 'array', tickvals: [0, 90, 180, 270], ticktext: [o.zero_label, '90°', '180°', '270°'] } : {}),
      },
      radialaxis: { gridcolor: GRID, linecolor: GRID, showticklabels: false, ticks: '' },
    };
    base.margin = { l: 40, r: 40, t: 24, b: 40 };
  }
  if (spec.kind === 'map_points') {
    (base.yaxis as Record<string, unknown>).scaleanchor = 'x';
    if (o.range) {
      (base.xaxis as Record<string, unknown>).range = o.range;
      (base.yaxis as Record<string, unknown>).range = o.range;
    }
    if (o.circles?.length) {
      base.shapes = o.circles.map((c) => ({ type: 'circle', xref: 'x', yref: 'y', x0: c.x - c.r, x1: c.x + c.r,
        y0: c.y - c.r, y1: c.y + c.r, line: { color: INK, width: 1.5, dash: 'dot' } }));
      base.annotations = o.circles.filter((c) => c.label).map((c) => ({ x: c.x, y: c.y + c.r, text: c.label,
        showarrow: false, yanchor: 'bottom', font: { ...FONT, size: 11, color: MUTED } }));
    }
    base.legend = { font: { ...FONT, size: 11 }, x: 1.02, y: 1 };
    base.margin = { l: 56, r: 100, t: 12, b: 48 };
  }
  if (o.reference_lines?.length) {
    base.shapes = o.reference_lines.map((l) => l.axis === 'y'
      ? { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: l.value, y1: l.value, line: { color: INK, width: 1.5, dash: 'dash' } }
      : { type: 'line', yref: 'paper', y0: 0, y1: 1, x0: l.value, x1: l.value, line: { color: INK, width: 1.5, dash: 'dash' } });
    base.annotations = o.reference_lines.filter((l) => l.label).map((l) => l.axis === 'y'
      ? { xref: 'paper', x: 1, y: l.value, text: l.label, showarrow: false, xanchor: 'right', yanchor: 'bottom', font: { ...FONT, size: 11, color: MUTED } }
      : { yref: 'paper', y: 1, x: l.value, text: l.label, showarrow: false, xanchor: 'left', yanchor: 'top', font: { ...FONT, size: 11, color: MUTED } });
  }
  return base;
}

export function PlotlyChart({ spec }: { spec: ChartSpec }) {
  return (
    <Plot
      data={traces(spec)}
      layout={layout(spec)}
      config={{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ['select2d', 'lasso2d', 'autoScale2d'],
                toImageButtonOptions: { filename: spec.title.toLowerCase().replace(/\W+/g, '_'), scale: 2 } }}
      useResizeHandler
      style={{ width: '100%', height: spec.kind === 'map_points' ? 420 : 320 }}
    />
  );
}
