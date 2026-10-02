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
  min_lobby_strength?: number | null;
}

export interface FacetValue { value: string; count: number }
export interface Facets {
  seasons: FacetValue[];
  regions: FacetValue[];
  event_windows: FacetValue[];
  playlists: FacetValue[];
  date_min: string | null;
  date_max: string | null;
  lobby_rated_matches?: number;
}

export interface DatasetInfo { label: string; available: boolean; matches: number; path: string }
export interface Health { ok: boolean; boot_id: string; datasets: Record<DatasetKey, DatasetInfo> }

export interface ParamSpec {
  name: string;
  label: string;
  kind: 'select' | 'number' | 'boolean' | 'match' | 'zone';
  default: unknown;
  options?: { value: string | number; label: string }[] | null;
  help?: string | null;
}

export interface Guide {
  question: string;
  method: string[];
  terms: Record<string, string>;
  charts: Record<string, string>;
  conclude: string[];
  limits: string[];
}

export interface AnalysisInfo {
  id: string;
  title: string;
  summary: string;
  needs_compare: boolean;
  params: ParamSpec[];
  guide: Guide | null;
}

export type Strength = 'strong' | 'clear' | 'weak' | 'none';

export interface Conclusion {
  status: 'found' | 'none' | 'insufficient' | 'descriptive';
  title: string;
  summary: string;
  evidence: { label: string; detail: string; strength: Strength; p: number | null; n: number }[];
  reliability: { label: string; ok: boolean; detail: string }[];
  next_steps: string[];
}

export type ChartKind = 'bar' | 'stacked_bar' | 'line' | 'histogram' | 'polar_histogram' | 'box' | 'map_points';

export interface Series {
  name: string;
  text?: string[];
  color?: string;
  size?: number;
  opacity?: number;
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
    horizontal?: boolean;
    circles?: { x: number; y: number; r: number; label?: string }[];
    marker_size?: number;
    zone_circles?: boolean;
    range_x?: number[];
    range_y?: number[];
    match?: string;
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
  conclusion: Conclusion | null;
  n_matches: number;
  n_compare: number;
  params: Record<string, unknown>;
  elapsed_ms: number;
}

export interface MatchList { total: number; columns: string[]; rows: unknown[][] }

export interface SectionInfo {
  analysis: string;
  title: string;
  question: string;
  charts: string[];
  table: { title: string; columns: string[] | null } | null;
  metrics: string[];
}

export interface PageInfo { id: string; title: string; question: string; needs_compare?: boolean; sections: SectionInfo[] }

export interface DataStatus {
  epic: { logged_in: boolean; name?: string; account_id?: string; since?: string };
  login_url: string;
  parser: { ready: boolean; dotnet: boolean };
  data_dir: string;
  data_exists: boolean;
  size_gb: number;
  keep_raw: boolean;
  parallel: number;
  api_key_set: boolean;
  counts: { collected: number; waiting: number; raw: number; parsed: number; in_tables: number };
  power_rankings: { players: number; fetched: string | null };
  pois: { places: number; fetched: string | null };
  validation: string | null;
  tournaments_list: { windows: number; updated: string | null };
  busy: boolean;
}

export interface JobSummary {
  id: string;
  kind: string;
  title: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  step: number;
  steps: string[];
  step_label: string;
  detail: string;
  progress: number | null;
  step_frac: number | null;
  eta_s: number | null;
  params: Record<string, unknown>;
  created: number;
  started: number | null;
  ended: number | null;
  error: string | null;
  failed_steps: string[];
}

export interface JobDetail extends JobSummary { log: string[] }

export interface TournamentRow {
  end: string; region: string; name: string; window: string; event: string | null;
  collected: number; downloaded: number; processed: number;
}

export interface DownloadPlan { rows: { match_id: string; top1000: number; top10000: number; seen: number }[]; waiting: number; note: string }
