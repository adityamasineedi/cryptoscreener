import type {
  FreshValue,
  HealthResponse,
  ScreenerPreset,
  ScreenerRow,
  SystemStats,
} from "../types/market";

const API_BASE = import.meta.env.VITE_API_URL || "";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function fetchHealth() {
  return getJson<HealthResponse>("/api/health");
}

export function fetchSystemStats() {
  return getJson<SystemStats>("/api/system/stats");
}

export function fetchCoverage() {
  return getJson<Record<string, unknown>>("/api/data/coverage");
}

export function fetchOhlcvCoverage() {
  return getJson<Record<string, unknown>>("/api/data/ohlcv-coverage");
}

export function fetchBackfill() {
  return getJson<Record<string, unknown>>("/api/data/backfill");
}

export function fetchProviderHealth() {
  return getJson<Record<string, unknown>>("/api/health/providers");
}

export function fetchPerformance() {
  return getJson<Record<string, unknown>>("/api/system/performance");
}

export function fetchFuturesScreener(params?: {
  search?: string;
  preset?: string;
  sort_by?: string;
  limit?: number;
  offset?: number;
}) {
  const q = new URLSearchParams();
  if (params?.search) q.set("search", params.search);
  if (params?.preset) q.set("preset", params.preset);
  if (params?.sort_by) q.set("sort_by", params.sort_by);
  q.set("limit", String(params?.limit ?? 200));
  q.set("offset", String(params?.offset ?? 0));
  return getJson<{
    total: number;
    rows: ScreenerRow[];
    ingestion: string;
    use_real_data: boolean;
    preset?: string | null;
  }>(`/api/screener/futures?${q.toString()}`);
}

export function fetchPresets() {
  return getJson<{ presets: ScreenerPreset[] }>("/api/screener/presets");
}

export function fetchCoinDetail(symbol: string) {
  return getJson<{
    symbol: string;
    row: ScreenerRow;
    structure?: unknown;
    zones?: unknown;
    indicators?: unknown;
    tabs?: Record<string, unknown>;
    signals?: {
      entry_exit?: unknown;
      tech_rating?: unknown;
      setup?: unknown;
    };
  }>(`/api/coin/${symbol}`);
}

export function fetchSetupAnnotations(symbol: string) {
  return getJson<{
    symbol: string;
    annotations: Array<{
      kind?: string;
      group?: string;
      price?: number | null;
      time?: string | null;
      label?: string | null;
      direction?: string | null;
    }>;
    status?: string;
  }>(`/api/signals/${symbol}/annotations`);
}

export function fetchSetupSignal(symbol: string) {
  return getJson<Record<string, unknown>>(`/api/signals/${symbol}`);
}

export function fetchOhlcv(symbol: string, timeframe: string, limit = 200) {
  const q = new URLSearchParams({ timeframe, limit: String(limit) });
  return getJson<{
    symbol: string;
    timeframe: string;
    candles: Array<Record<string, unknown>>;
    status: string;
  }>(`/api/charts/${symbol}/ohlcv?${q.toString()}`);
}

export function fetchOiHistory(symbol: string) {
  return getJson<{
    symbol: string;
    history: Array<Record<string, unknown>>;
    status: string;
  }>(`/api/charts/${symbol}/oi`);
}

export function fetchLiquidations(symbol: string) {
  return getJson<{
    symbol: string;
    events: Array<Record<string, unknown>>;
    aggregates: Record<string, unknown>;
    status: string;
  }>(`/api/charts/${symbol}/liquidations`);
}

export type BosCompareResponse = {
  label?: string;
  dataset?: string;
  dataset_id?: string;
  not_dataset?: string;
  status?: "SUCCESS_WITH_DATA" | "SUCCESS_EMPTY" | string;
  empty_reason?: string | null;
  rows: Array<Record<string, unknown>>;
  combinations_tested?: number;
  parameters_tested?: number;
  parameter_variants_tested?: number;
  parameters_tested_meaning?: string;
  closed_trades_sample?: number;
  multiple_testing_flag?: string | null;
  multiple_testing_detail?: {
    combinations_tested?: number;
    parameter_variants_tested?: number;
    configurations_explored?: number;
    threshold?: number;
  };
  historical_period?: Record<string, unknown>;
  date_filter?: Record<string, unknown>;
  candle_source?: string;
  timezone?: string;
  elapsed_seconds?: number;
  disclaimer?: string;
};

export function fetchBosCombinations() {
  return getJson<{
    label?: string;
    dataset?: string;
    dataset_id?: string;
    combinations: Array<Record<string, unknown>>;
    count?: number;
    disclaimer?: string;
  }>("/api/research/bos-combinations");
}

export function fetchBosCombinationsCompare(params: {
  symbol: string;
  timeframe?: string;
  direction?: string;
  start_date?: string;
  end_date?: string;
  minimum_sample_size?: number;
  limit?: number;
}) {
  const q = new URLSearchParams();
  q.set("symbol", params.symbol);
  if (params.timeframe) q.set("timeframe", params.timeframe);
  if (params.direction) q.set("direction", params.direction);
  if (params.start_date) q.set("start_date", params.start_date);
  if (params.end_date) q.set("end_date", params.end_date);
  if (params.minimum_sample_size != null) {
    q.set("minimum_sample_size", String(params.minimum_sample_size));
  }
  if (params.limit != null) q.set("limit", String(params.limit));
  return getJson<BosCompareResponse>(
    `/api/research/bos-combinations/compare?${q.toString()}`
  );
}

export function fetchBosCombinationDetail(
  combinationId: string,
  params?: {
    symbol?: string;
    timeframe?: string;
    direction?: string;
    start_date?: string;
    end_date?: string;
    limit?: number;
  }
) {
  const q = new URLSearchParams();
  if (params?.symbol) q.set("symbol", params.symbol);
  if (params?.timeframe) q.set("timeframe", params.timeframe);
  if (params?.direction) q.set("direction", params.direction);
  if (params?.start_date) q.set("start_date", params.start_date);
  if (params?.end_date) q.set("end_date", params.end_date);
  if (params?.limit != null) q.set("limit", String(params.limit));
  const qs = q.toString();
  return getJson<Record<string, unknown>>(
    `/api/research/bos-combinations/${combinationId}${qs ? `?${qs}` : ""}`
  );
}

export type StrategyMatrixRow = {
  combination_id: string;
  name?: string;
  symbol: string;
  timeframe: string;
  direction: string;
  structure?: string;
  sample_size: number;
  average_R?: number | null;
  expectancy_R?: number | null;
  tp1_hit_rate?: number | null;
  sl_rate?: number | null;
  profit_factor?: number | null;
  max_drawdown_R?: number | null;
  tp1_hits?: number | null;
  sl_hits?: number | null;
  period_start?: string | null;
  period_end?: string | null;
  r_values?: number[];
  equity_curve_r?: number[];
  pnl_usd?: number | null;
  risk_usd?: number;
  bars_loaded?: number;
  candle_source?: string;
  status?: string;
};

export type StrategyMatrixResponse = {
  status: string;
  label?: string;
  playbook?: string;
  combination_id?: string;
  combination_name?: string;
  direction?: string;
  limit?: number;
  risk_usd?: number;
  symbols?: string[];
  timeframes?: string[];
  rows: StrategyMatrixRow[];
  elapsed_seconds?: number;
  disclaimer?: string;
  timestamp?: string;
  reason?: string;
};

export function fetchLongStrategyBacktest(params: {
  symbols?: string[];
  timeframes?: string[];
  direction?: "LONG" | "SHORT";
  combination_id?: string;
  limit?: number;
  risk_usd?: number;
  start_date?: string;
  end_date?: string;
}) {
  const q = new URLSearchParams();
  if (params.symbols?.length) q.set("symbols", params.symbols.join(","));
  if (params.timeframes?.length) q.set("timeframes", params.timeframes.join(","));
  if (params.direction) q.set("direction", params.direction);
  if (params.combination_id) q.set("combination_id", params.combination_id);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.risk_usd != null) q.set("risk_usd", String(params.risk_usd));
  if (params.start_date) q.set("start_date", params.start_date);
  if (params.end_date) q.set("end_date", params.end_date);
  return getJson<StrategyMatrixResponse>(
    `/api/research/long-strategy/backtest?${q.toString()}`
  );
}

export function formatFresh(
  fv: FreshValue | undefined,
  formatter: (v: number | string) => string
): string {
  if (!fv || fv.value === null || fv.value === undefined) {
    if (fv?.status === "WAITING") return "Waiting for live data...";
    return "Data unavailable";
  }
  return formatter(fv.value);
}

export function wsUrl(path: string): string {
  const env = import.meta.env.VITE_WS_URL as string | undefined;
  if (env) return `${env.replace(/\/$/, "")}${path}`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}
