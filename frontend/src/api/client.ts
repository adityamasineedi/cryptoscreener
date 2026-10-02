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
  screen_filter?: string;
  signal?: string;
  setup?: string;
}) {
  const q = new URLSearchParams();
  if (params?.search) q.set("search", params.search);
  if (params?.preset) q.set("preset", params.preset);
  if (params?.sort_by) q.set("sort_by", params.sort_by);
  if (params?.screen_filter) q.set("screen_filter", params.screen_filter);
  if (params?.signal) q.set("signal", params.signal);
  if (params?.setup) q.set("setup", params.setup);
  // Main screener hard-caps at 100 on the backend
  q.set("limit", String(Math.min(params?.limit ?? 100, 100)));
  q.set("offset", String(params?.offset ?? 0));
  return getJson<{
    total: number;
    rows: ScreenerRow[];
    ingestion: string;
    use_real_data: boolean;
    preset?: string | null;
    total_universe?: number;
    eligible_count?: number;
    returned_count?: number;
    limit?: number;
    selection_updated_at?: string | null;
    excluded?: Record<string, number>;
    search_mode?: boolean;
    screen_filter?: string | null;
    max_screen_symbols?: number;
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

export type StrategyTradeRow = {
  trade_no?: number;
  symbol: string;
  timeframe: string;
  direction: string;
  signal_time?: string | null;
  exit_time?: string | null;
  entry_price: number;
  exit_price?: number | null;
  stop_price: number;
  tp1?: number | null;
  tp2?: number | null;
  rr?: number | null;
  outcome?: string | null;
  holding_bars?: number | null;
  entry_type?: string;
  qty?: number;
  risk_usd?: number;
  fee_entry_usd?: number;
  fee_exit_usd?: number;
  fee_total_usd?: number;
  fee_entry_rate?: number;
  fee_exit_rate?: number;
  gross_pnl_usd?: number | null;
  net_pnl_usd?: number | null;
  r_gross?: number | null;
  r_net?: number | null;
  mae_r?: number | null;
  mfe_r?: number | null;
};

export type StrategyMatrixRow = {
  combination_id: string;
  name?: string;
  symbol: string;
  timeframe: string;
  direction: string;
  structure?: string;
  sample_size: number;
  average_R?: number | null;
  average_R_net?: number | null;
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
  pnl_usd_net?: number | null;
  fees_usd?: number | null;
  risk_usd?: number;
  bars_loaded?: number;
  candle_source?: string;
  trades?: StrategyTradeRow[];
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
  fee_model?: {
    taker_fee?: number;
    maker_fee?: number;
    exit_fee?: number;
    note?: string;
  };
  symbols?: string[];
  timeframes?: string[];
  rows: StrategyMatrixRow[];
  elapsed_seconds?: number;
  disclaimer?: string;
  timestamp?: string;
  reason?: string;
};

export type OhlcvRangeRow = {
  symbol: string;
  timeframe: string;
  bars: number;
  start?: string | null;
  end?: string | null;
};

export function fetchResearchOhlcvRange(params?: {
  symbols?: string[];
  timeframes?: string[];
}) {
  const q = new URLSearchParams();
  if (params?.symbols?.length) q.set("symbols", params.symbols.join(","));
  if (params?.timeframes?.length) q.set("timeframes", params.timeframes.join(","));
  const qs = q.toString();
  return getJson<{ status: string; rows: OhlcvRangeRow[] }>(
    `/api/research/ohlcv-range${qs ? `?${qs}` : ""}`
  );
}

export function fetchLongStrategyBacktest(params: {
  symbols?: string[];
  timeframes?: string[];
  direction?: "LONG" | "SHORT";
  combination_id?: string;
  limit?: number;
  risk_usd?: number;
  taker_fee_pct?: number;
  maker_fee_pct?: number;
  include_trades?: boolean;
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
  if (params.taker_fee_pct != null) q.set("taker_fee_pct", String(params.taker_fee_pct));
  if (params.maker_fee_pct != null) q.set("maker_fee_pct", String(params.maker_fee_pct));
  if (params.include_trades != null) {
    q.set("include_trades", params.include_trades ? "true" : "false");
  }
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

export type PaperStatus = {
  enabled: boolean;
  paper_only: boolean;
  disclaimer: string;
  starting_equity: number;
  equity: number;
  realized_pnl_usd: number;
  open_count: number;
  closed_count: number;
  open_risk_usd: number;
  risk_percent: number;
  entry_mode?: string;
  entry_mode_label?: string;
  timestamp: string;
};

export type PaperPosition = {
  id: string;
  symbol: string;
  side: string;
  status: string;
  entry_price: number;
  stop_price: number;
  tp1_price: number | null;
  quantity: number;
  risk_usd: number;
  opened_at: string;
  closed_at: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  pnl_usd: number | null;
  r_multiple: number | null;
  mark_price: number | null;
  unrealized_pnl_usd: number | null;
  unrealized_r: number | null;
  source_candle_ts: string | null;
  timeframe: string;
};

export type PaperOpportunity = {
  symbol: string;
  tier: "READY" | "NEAR" | "FORMING" | string;
  chance: string;
  status: string;
  direction: string;
  timeframe: string;
  setup_trend: string | null;
  bos: string | null;
  bos_state: string | null;
  impulse: string | null;
  pullback: string | null;
  retest: string | null;
  entry_price: number | null;
  stop_price: number | null;
  tp1_price: number | null;
  rr_pass: boolean;
  pass_count: number;
  missing: string[];
  already_open: boolean;
  ohlcv_freshness?: string | null;
};

export function fetchPaperStatus() {
  return getJson<PaperStatus>("/api/paper/status");
}

export function fetchPaperPositions(closedLimit = 50) {
  return getJson<{
    open: PaperPosition[];
    closed: PaperPosition[];
    status: PaperStatus;
  }>(`/api/paper/positions?closed_limit=${closedLimit}`);
}

export function fetchPaperOpportunities(limit = 120, warm = 8) {
  return getJson<{
    rows: PaperOpportunity[];
    count: number;
    ready: number;
    near: number;
    forming: number;
    waiting?: number;
    watch?: number;
    blocked?: number;
    warmed?: number;
    auto_enabled: boolean;
    note: string;
  }>(`/api/paper/opportunities?limit=${limit}&warm=${warm}`);
}

async function postJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST" });
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json() as Promise<T>;
}

export function enablePaperTrade() {
  return postJson<PaperStatus>("/api/paper/enable");
}

export function disablePaperTrade() {
  return postJson<PaperStatus>("/api/paper/disable");
}

export function resetPaperTrade() {
  return postJson<PaperStatus>("/api/paper/reset");
}

export function wsUrl(path: string): string {
  const env = import.meta.env.VITE_WS_URL as string | undefined;
  if (env) return `${env.replace(/\/$/, "")}${path}`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}
