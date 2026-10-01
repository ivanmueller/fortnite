// Picks a renderer for a chart spec. Add new libraries here (e.g. ECharts, deck.gl for maps)
// and route chart kinds to them without touching any analysis code.
import { Component, lazy, Suspense, type ReactNode } from 'react';
import type { ChartKind, ChartSpec } from '../types';
// Plotly is large, so it loads on first use instead of with the page.
const PlotlyChart = lazy(() => import('./plotlyRenderer').then((m) => ({ default: m.PlotlyChart })));

type Renderer = (props: { spec: ChartSpec }) => ReactNode;

const RENDERERS: Record<ChartKind, Renderer> = {
  bar: PlotlyChart,
  stacked_bar: PlotlyChart,
  line: PlotlyChart,
  histogram: PlotlyChart,
  polar_histogram: PlotlyChart,
  box: PlotlyChart,
  map_points: PlotlyChart,
};

class ChartBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(e: Error) { return { error: e.message }; }
  render() {
    return this.state.error ? <p className="chart-error">This chart couldn't be drawn: {this.state.error}</p> : this.props.children;
  }
}

export function ChartView({ spec, help }: { spec: ChartSpec; help?: string }) {
  const Render = RENDERERS[spec.kind];
  return (
    <figure className={`chart chart--${spec.kind}`}>
      <figcaption>
        {spec.title}
        {help && <span className="chart__help">{help}</span>}
      </figcaption>
      <ChartBoundary>
        <Suspense fallback={<div className="chart-loading">Loading chart…</div>}>
          {Render ? <Render spec={spec} /> : <p className="chart-error">No renderer for “{spec.kind}”.</p>}
        </Suspense>
      </ChartBoundary>
    </figure>
  );
}
