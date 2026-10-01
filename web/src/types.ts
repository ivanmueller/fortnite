// Mirrors the API's JSON. Keep in sync with api/fnlab/filters.py and api/fnlab/result.py.

export type DatasetKey = 'real' | 'local' | 'demo';

export interface Filters {
  dataset: DatasetKey;
  date_from?: string | null;
  date_to?: string | null;
  seasons: string[];
  regions: string[];
  event_windows: string[];
  playlist_contains?: string | null;
  server_only: boolean;
}

export interface FacetValue { value: string; count: number }
export interface Facets {
  seasons: FacetValue[];
  regions: FacetValue[];
  event_windows: FacetValue[];
  playlists: FacetValue[];
  date_min: string | null;
  date_max: string | null;
}

export interface DatasetInfo { label: string; available: boolean; matches: number; path: string }
export interface Health { ok: boolean; boot_id: string; datasets: Record<DatasetKey, DatasetInfo> }

export interface ParamSpec {
  name: string;
  label: string;
  kind: 'select' | 'number' | 'boolean';
  default: unknown;
  options?: { value: string | number; label: string }[] | null;
  help?: string | null;
}

export interface AnalysisInfo {
  id: string;
  title: string;
  summary: string;
  needs_compare: boolean;
  params: ParamSpec[];
}

export type ChartKind = 'bar' | 'stacked_bar' | 'line' | 'histogram' | 'polar_histogram' | 'box' | 'map_points';

export interface Series {
  name: string;
  x?: (string | number)[];
  y?: (number | null)[];
  values?: (number | null)[];
  theta?: (number | null)[];
}

export interface ChartSpec {
  kind: ChartKind;
  title: string;
  series: Series[];
  options: {
    x_label?: string;
    y_label?: string;
    bins?: number;
    range?: [number, number];
    normalize?: boolean;
    reverse_y?: boolean;
    zero_label?: string;
    reference_lines?: { axis: 'x' | 'y'; value: number; label?: string }[];
  };
}

export interface TestRow {
  group: string;
  name: string;
  n: number;
  value: string;
  p: number | null;
  significant: boolean;
  reading?: string;
}

export interface TableSpec { title: string; columns: string[]; rows: unknown[][] }

export interface AnalysisResult {
  status: 'ok' | 'empty';
  message?: string;
  headline: string;
  metrics: { label: string; value: string | number; detail?: string | null }[];
  tests: TestRow[];
  charts: ChartSpec[];
  tables: TableSpec[];
  notes: string[];
  warnings: string[];
  n_matches: number;
  n_compare: number;
  params: Record<string, unknown>;
  elapsed_ms: number;
}

export interface MatchList { total: number; columns: string[]; rows: unknown[][] }
