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

export function fetchSentimentDiagnostic() {
  return getJson<Record<string, unknown>>("/api/data/sentiment/diagnostic");
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

export type BosStrategiesResponse = {
  status?: string;
  dataset?: string;
  results?: Array<Record<string, unknown>>;
  condition_contribution?: Array<Record<string, unknown>>;
  data_period?: Record<string, unknown>;
  universe?: Record<string, unknown>;
  data_coverage?: Record<string, unknown>;
  trade_count?: number;
  total_trades?: number;
  fee_assumptions?: Record<string, unknown>;
  disclaimer?: string;
  label?: string;
  note?: string;
};

export function fetchBosStrategiesCatalog() {
  return getJson<{
    strategies: Array<Record<string, unknown>>;
    disclaimer?: string;
  }>("/api/research/bos-strategies/catalog");
}

export function fetchBosStrategiesCoverage() {
  return getJson<Record<string, unknown>>("/api/research/bos-strategies/coverage");
}

export function fetchBosStrategiesDiagnostics(params: {
  symbol: string;
  timeframe?: string;
  start?: string;
  end?: string;
  limit?: number;
  strategies?: string;
}) {
  const q = new URLSearchParams();
  q.set("symbol", params.symbol);
  if (params.timeframe) q.set("timeframe", params.timeframe);
  if (params.start) q.set("start", params.start);
  if (params.end) q.set("end", params.end);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.strategies) q.set("strategies", params.strategies);
  return getJson<Record<string, unknown>>(
    `/api/research/bos-strategies/diagnostics?${q.toString()}`
  );
}

export function fetchBosStrategies(params?: {
  symbols?: string;
  timeframe?: string;
  start_date?: string;
  end_date?: string;
  direction?: string;
  limit?: number;
  max_symbols?: number;
  persist?: boolean;
  include_walk_forward?: boolean;
  latest_only?: boolean;
}) {
  const q = new URLSearchParams();
  if (params?.symbols) q.set("symbols", params.symbols);
  if (params?.timeframe) q.set("timeframe", params.timeframe);
  if (params?.start_date) q.set("start_date", params.start_date);
  if (params?.end_date) q.set("end_date", params.end_date);
  if (params?.direction) q.set("direction", params.direction);
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.max_symbols != null) q.set("max_symbols", String(params.max_symbols));
  if (params?.persist != null) q.set("persist", String(params.persist));
  if (params?.include_walk_forward != null) {
    q.set("include_walk_forward", String(params.include_walk_forward));
  }
  if (params?.latest_only != null) q.set("latest_only", String(params.latest_only));
  const qs = q.toString();
  return getJson<BosStrategiesResponse>(
    `/api/research/bos-strategies${qs ? `?${qs}` : ""}`
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

export type ResearchOhlcvCandle = {
  open_time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

/** Postgres OHLCV slice around a blotter trade window (backtest chart overlay). */
export function fetchResearchOhlcvCandles(params: {
  symbol: string;
  timeframe: string;
  start: string;
  end?: string | null;
  pad_bars?: number;
}) {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start: params.start,
  });
  if (params.end) q.set("end", params.end);
  if (params.pad_bars != null) q.set("pad_bars", String(params.pad_bars));
  return getJson<{
    status: string;
    reason?: string;
    symbol: string;
    timeframe: string;
    candles: ResearchOhlcvCandle[];
    window_start?: string;
    window_end_exclusive?: string;
  }>(`/api/research/ohlcv-candles?${q.toString()}`);
}

export type OhlcvExpandResultRow = {
  symbol: string;
  timeframe: string;
  written: number;
  direction?: string;
  error?: string | null;
  db_start?: string | null;
  db_end?: string | null;
  bars?: number;
};

export type OhlcvExpandJob = {
  job_id?: string | null;
  status: string;
  symbols?: string[];
  timeframes?: string[];
  until?: string | null;
  refresh_tip?: boolean;
  max_pages?: number;
  total_cells?: number;
  done_cells?: number;
  /** 0..1 progress inside the current symbol/TF cell while REST pages pull */
  cell_fraction?: number;
  pct?: number;
  current?: string;
  written_total?: number;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  results?: OhlcvExpandResultRow[];
};

export function fetchOhlcvExpandStatus() {
  return getJson<OhlcvExpandJob & { timestamp?: string }>(
    "/api/research/ohlcv-expand"
  );
}

export async function startOhlcvExpand(body: {
  symbols: string[];
  timeframes: string[];
  until?: string;
  refresh_tip?: boolean;
  max_pages?: number;
}) {
  const res = await fetch(`${API_BASE}/api/research/ohlcv-expand`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`API /api/research/ohlcv-expand failed: ${res.status}`);
  }
  return res.json() as Promise<{
    status: string;
    error?: string;
    job?: OhlcvExpandJob;
    timestamp?: string;
  }>;
}

export async function cancelOhlcvExpand() {
  const res = await fetch(`${API_BASE}/api/research/ohlcv-expand/cancel`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(`API /api/research/ohlcv-expand/cancel failed: ${res.status}`);
  }
  return res.json() as Promise<{
    status: string;
    job?: OhlcvExpandJob;
    timestamp?: string;
  }>;
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

export type Combo02CandidateResultsResponse = {
  status: string;
  source_file?: string;
  generated_at_utc?: string;
  fingerprint?: Record<string, unknown>;
  final_lists?: Record<string, string[]>;
  portfolio?: Array<Record<string, unknown>>;
  oos?: Array<Record<string, unknown>>;
  results?: Array<Record<string, unknown>>;
  disclaimer?: string;
  note?: string;
  v1_unchanged?: boolean;
  read_only?: boolean;
  execution_rights?: boolean;
  timestamp?: string;
};

/** Read-only candidate research report (no promote / Telegram / paper actions). */
export function fetchCombo02CandidateResults(stamp?: string) {
  const q = new URLSearchParams();
  if (stamp) q.set("stamp", stamp);
  const qs = q.toString();
  return getJson<Combo02CandidateResultsResponse>(
    `/api/research/combo02-candidate-results${qs ? `?${qs}` : ""}`
  );
}

export type BacktestJob = {
  job_id?: string | null;
  status: string;
  symbols?: string[];
  timeframes?: string[];
  direction?: string;
  combination_id?: string;
  combination_name?: string;
  playbook?: string | null;
  label?: string | null;
  limit?: number;
  risk_usd?: number;
  taker_fee_pct?: number;
  maker_fee_pct?: number;
  include_trades?: boolean;
  start_date?: string | null;
  end_date?: string | null;
  total_cells?: number;
  done_cells?: number;
  pct?: number;
  current?: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  rows?: StrategyMatrixRow[];
  elapsed_seconds?: number | null;
  disclaimer?: string | null;
};

export function fetchBacktestJobStatus() {
  return getJson<BacktestJob & { timestamp?: string }>(
    "/api/research/long-strategy/backtest/job"
  );
}

export async function startBacktestJob(body: {
  symbols: string[];
  timeframes: string[];
  direction?: "LONG" | "SHORT";
  combination_id?: string;
  limit?: number;
  risk_usd?: number;
  principal_usd?: number;
  taker_fee_pct?: number;
  maker_fee_pct?: number;
  include_trades?: boolean;
  start_date?: string;
  end_date?: string;
}) {
  const res = await fetch(`${API_BASE}/api/research/long-strategy/backtest/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(
      `API /api/research/long-strategy/backtest/start failed: ${res.status}`
    );
  }
  return res.json() as Promise<{
    status: string;
    error?: string;
    job?: BacktestJob;
    timestamp?: string;
  }>;
}

export async function cancelBacktestJob() {
  const res = await fetch(`${API_BASE}/api/research/long-strategy/backtest/cancel`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(
      `API /api/research/long-strategy/backtest/cancel failed: ${res.status}`
    );
  }
  return res.json() as Promise<{
    status: string;
    job?: BacktestJob;
    timestamp?: string;
  }>;
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

export type PaperTelegramStatus = {
  enabled?: boolean;
  configured?: boolean;
  subscribed?: boolean;
  ready?: boolean;
  chat_id_set?: boolean;
  monitors?: string[];
  timeframe?: string;
  alert_types?: string[];
  source?: string;
  combo_version?: string;
  path?: string;
  reason?: string | null;
  label?: string;
};

export type PaperWatcherBookLive = {
  symbol: string;
  timeframe?: string;
  tier?: string;
  risk_percent?: number;
  seeded?: boolean;
  watermark?: string | null;
  tip_bar?: string | null;
  waiting_next_closed_bar?: boolean;
  tip_status?: string;
  tip_reason?: string;
  gates?: { trend?: boolean; bos?: boolean; htf?: boolean };
  htf_alignment?: string | null;
  trend_1h?: string | null;
  trend_4h?: string | null;
  would_open_on_new_bar?: boolean;
  bars_1h?: number;
  bars_4h?: number;
};

export type PaperWatcherStatus = {
  enabled?: boolean;
  timeframe?: string;
  replay_mode?: boolean;
  secondary_enabled?: boolean;
  books?: Array<{
    symbol: string;
    timeframe?: string;
    tier?: string;
    risk_percent?: number;
  }>;
  seeded?: string[];
  watermarks?: Record<string, string>;
  processed_bars?: number;
  last_skip?: string | null;
  combo_id?: string;
  combo_version?: string;
  path?: string;
  label?: string;
  auto_executes_from?: string;
  live?: PaperWatcherBookLive[];
  live_error?: string;
  error?: string;
};

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
  legacy_auto_entry_enabled?: boolean;
  v1_watcher_owns_entries?: boolean;
  last_skip_reason?: string | null;
  v1_profile?: {
    enabled?: boolean;
    universe_only?: boolean;
    secondary_enabled?: boolean;
    combo_version?: string;
  };
  monitor?: {
    auto_source?: string;
    v1_watcher_enabled?: boolean;
    legacy_auto_entry_enabled?: boolean;
    explanation?: string;
  };
  v1_watcher?: PaperWatcherStatus;
  telegram?: PaperTelegramStatus;
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
  signal_snippet?: Record<string, unknown>;
  strategy_id?: string;
  source?: string;
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
    entry_mode?: string;
    v1_watcher_owns_entries?: boolean;
    legacy_auto_entry_enabled?: boolean;
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

export function closeLegacyPaperPositions(confirm = false) {
  return postJson<{
    ok: boolean;
    error?: string;
    reason?: string;
    closed_count?: number;
    closed?: PaperPosition[];
    skipped_v1?: string[];
    message?: string;
    status: PaperStatus;
  }>(`/api/paper/close-legacy?confirm=${confirm ? "true" : "false"}`);
}

export type AlertType =
  | "BOS"
  | "SETUP_STATUS"
  | "MARKET_SIGNAL"
  | "PAPER_ENTRY"
  | "PAPER_EXIT"
  | "LIQ_SPIKE"
  | string;

export type AlertSeverity = "info" | "watch" | "action" | string;

export type AlertBadge =
  | "V1_VERIFIED"
  | "RESEARCH_15M"
  | "STRUCTURE"
  | "LIQUIDATIONS"
  | "EXPERIMENTAL"
  | string;

export type LiveAlert = {
  id: string;
  seq: number;
  time: string;
  type: AlertType;
  symbol: string;
  timeframe?: string | null;
  severity: AlertSeverity;
  title: string;
  detail: string;
  payload?: Record<string, unknown>;
  strategy_id?: string | null;
  source?: string | null;
  combo_id?: string | null;
  combo_version?: string | null;
  path?: string | null;
  telegram_eligible?: boolean | null;
  badge?: AlertBadge | null;
};

export function fetchAlerts(params?: {
  limit?: number;
  types?: string;
  symbol?: string;
  since_seq?: number;
}) {
  const q = new URLSearchParams();
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.types) q.set("types", params.types);
  if (params?.symbol) q.set("symbol", params.symbol);
  if (params?.since_seq != null) q.set("since_seq", String(params.since_seq));
  const qs = q.toString();
  return getJson<{
    rows: LiveAlert[];
    count: number;
    total_buffered: number;
    latest_seq: number;
    timestamp: string;
  }>(`/api/alerts${qs ? `?${qs}` : ""}`);
}

export function wsUrl(path: string): string {
  const env = import.meta.env.VITE_WS_URL as string | undefined;
  if (env) return `${env.replace(/\/$/, "")}${path}`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}

/* ---- System Diagnostics / Issue Center ---- */

export function fetchDiagnosticsOverview() {
  return getJson<Record<string, unknown>>("/api/diagnostics/overview");
}

export function fetchDiagnosticsIssues(params?: {
  status?: string;
  severity?: string;
  component?: string;
  limit?: number;
}) {
  const q = new URLSearchParams();
  if (params?.status) q.set("status", params.status);
  if (params?.severity) q.set("severity", params.severity);
  if (params?.component) q.set("component", params.component);
  if (params?.limit != null) q.set("limit", String(params.limit));
  const qs = q.toString();
  return getJson<{ count: number; issues: Array<Record<string, unknown>> }>(
    `/api/diagnostics/issues${qs ? `?${qs}` : ""}`
  );
}

export function fetchDiagnosticsIssue(id: string) {
  return getJson<{
    issue: Record<string, unknown>;
    events: Array<Record<string, unknown>>;
    timeline: Array<Record<string, unknown>>;
  }>(`/api/diagnostics/issues/${encodeURIComponent(id)}`);
}

export function fetchDiagnosticsAiPackage(id: string) {
  return getJson<{
    diagnostic_id: string;
    markdown: string;
    json: Record<string, unknown>;
  }>(`/api/diagnostics/issues/${encodeURIComponent(id)}/ai-package`);
}

export async function acknowledgeDiagnosticsIssue(id: string, owner?: string) {
  const res = await fetch(
    `${API_BASE}/api/diagnostics/issues/${encodeURIComponent(id)}/acknowledge`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner }),
    }
  );
  if (!res.ok) throw new Error(`acknowledge failed: ${res.status}`);
  return res.json() as Promise<{ issue: Record<string, unknown> }>;
}

export async function resolveDiagnosticsIssue(id: string, message?: string) {
  const res = await fetch(
    `${API_BASE}/api/diagnostics/issues/${encodeURIComponent(id)}/resolve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    }
  );
  if (!res.ok) throw new Error(`resolve failed: ${res.status}`);
  return res.json() as Promise<{ issue: Record<string, unknown> }>;
}

export function fetchDiagnosticsResources() {
  return getJson<Record<string, unknown>>("/api/diagnostics/resources");
}

export function fetchDiagnosticsGit() {
  return getJson<Record<string, unknown>>("/api/diagnostics/git");
}

export function fetchDiagnosticsWhy(dataset: string) {
  return getJson<Record<string, unknown>>(
    `/api/diagnostics/why/${encodeURIComponent(dataset)}`
  );
}

export async function postDiagnosticsEvent(body: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/api/diagnostics/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`record event failed: ${res.status}`);
  return res.json() as Promise<{
    event: Record<string, unknown>;
    issue: Record<string, unknown>;
  }>;
}

export async function postDiagnosticsAiHandoff(body: {
  issue_id?: string;
  include?: Record<string, boolean>;
}) {
  const res = await fetch(`${API_BASE}/api/diagnostics/ai-handoff`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`ai-handoff failed: ${res.status}`);
  return res.json() as Promise<{ markdown: string; [k: string]: unknown }>;
}

export function fetchDiagnosticsDatabase() {
  return getJson<Record<string, unknown>>("/api/diagnostics/database");
}

export function fetchDiagnosticsDatabaseDetail(opts?: { refresh?: boolean }) {
  const q = opts?.refresh ? "?refresh=true" : "";
  return getJson<Record<string, unknown>>(`/api/diagnostics/database/detail${q}`);
}

export function fetchDiagnosticsWebsocket() {
  return getJson<Record<string, unknown>>("/api/diagnostics/websocket");
}

export function fetchDiagnosticsRest() {
  return getJson<Record<string, unknown>>("/api/diagnostics/rest");
}

export function fetchDiagnosticsDataHealth() {
  return getJson<Record<string, unknown>>("/api/diagnostics/data-health");
}

export function fetchDiagnosticsDataHealthDrilldown(params: {
  dataset: string;
  symbol: string;
  timeframe: string;
}) {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
  });
  return getJson<Record<string, unknown>>(
    `/api/diagnostics/data-health/${encodeURIComponent(params.dataset)}?${q}`
  );
}

export function fetchDiagnosticsJobs() {
  return getJson<Record<string, unknown>>("/api/diagnostics/jobs");
}

export function fetchDiagnosticsLogs(params?: Record<string, string | number | undefined>) {
  const q = new URLSearchParams();
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== "") q.set(k, String(v));
    }
  }
  const qs = q.toString();
  return getJson<Record<string, unknown>>(
    `/api/diagnostics/logs${qs ? `?${qs}` : ""}`
  );
}

export function fetchDiagnosticsBackups() {
  return getJson<Record<string, unknown>>("/api/diagnostics/backups");
}

export function createDiagnosticsBackup(type: string) {
  return fetch("/api/diagnostics/backups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type }),
  }).then(async (res) => {
    if (!res.ok) throw new Error(`backup create failed: ${res.status}`);
    return res.json() as Promise<Record<string, unknown>>;
  });
}

export function verifyDiagnosticsBackup(backupId: string) {
  return fetch(`/api/diagnostics/backups/${encodeURIComponent(backupId)}/verify`, {
    method: "POST",
  }).then(async (res) => {
    if (!res.ok) throw new Error(`backup verify failed: ${res.status}`);
    return res.json() as Promise<Record<string, unknown>>;
  });
}

export function restoreTestDiagnosticsBackup(backupId: string) {
  return fetch(
    `/api/diagnostics/backups/${encodeURIComponent(backupId)}/restore-test`,
    { method: "POST" }
  ).then(async (res) => {
    if (!res.ok) throw new Error(`restore-test failed: ${res.status}`);
    return res.json() as Promise<Record<string, unknown>>;
  });
}

export function fetchDiagnosticsSnapshots() {
  return getJson<Record<string, unknown>>("/api/diagnostics/snapshot");
}

export function createDiagnosticsSnapshot(label?: string) {
  return fetch("/api/diagnostics/snapshot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  }).then(async (res) => {
    if (!res.ok) throw new Error(`snapshot create failed: ${res.status}`);
    return res.json() as Promise<Record<string, unknown>>;
  });
}

export function fetchDiagnosticsSnapshotAiPackage(snapshotId: string) {
  return getJson<Record<string, unknown>>(
    `/api/diagnostics/snapshot/${encodeURIComponent(snapshotId)}/ai-package`
  );
}

export function exportDiagnosticsSnapshot(snapshotId: string) {
  return getJson<Record<string, unknown>>(
    `/api/diagnostics/snapshot/${encodeURIComponent(snapshotId)}/export`
  );
}
