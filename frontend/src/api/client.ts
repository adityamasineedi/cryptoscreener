import type {
  FreshValue,
  HealthResponse,
  ScreenerPreset,
  ScreenerRow,
  SystemStats,
} from "../types/market";

const API_BASE = import.meta.env.VITE_API_URL || "";

/** "API <path> failed: <status>", plus the server's own detail when the body has one. */
async function apiError(path: string, res: Response): Promise<Error> {
  let detail = "";
  try {
    const text = await res.text();
    try {
      const body = JSON.parse(text) as { detail?: unknown; error?: unknown; reason?: unknown };
      const d = body.detail ?? body.error ?? body.reason;
      detail = typeof d === "string" ? d : d != null ? JSON.stringify(d) : "";
    } catch {
      detail = text;
    }
  } catch {
    /* no readable body */
  }
  detail = detail.trim().slice(0, 300);
  return new Error(`API ${path} failed: ${res.status}${detail ? ` — ${detail}` : ""}`);
}

async function getJson<T>(
  path: string,
  opts?: { signal?: AbortSignal },
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    signal: opts?.signal,
  });
  if (!res.ok) {
    throw await apiError(path, res);
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
    discovered_universe?: number;
    active_universe?: number;
    active_universe_cap?: number;
    eligible_count?: number;
    returned_count?: number;
    limit?: number;
    selection_updated_at?: string | null;
    excluded?: Record<string, number>;
    search_mode?: boolean;
    screen_filter?: string | null;
    max_screen_symbols?: number;
    screen_timeframe?: string;
    screener_identity?: import("../types/market").ScreenerIdentity;
    v1_watcher_view?: import("../types/market").V1WatcherViewRow[];
    is_telegram_eligible?: boolean;
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

export type StrategyCatalogItem = {
  id: string;
  name: string;
  status: string;
  tier?: string;
  combo_id?: string;
  combo_version?: string;
  direction?: string;
  setup_timeframe?: string;
  horizon?: "scalping" | "intraday" | "swing" | string;
  htf_timeframes?: string[];
  summary: string;
  gates?: string[];
  symbols?: {
    freeze_claim?: string[];
    paper_watch?: string[];
    extended_paper?: string[];
  };
  risk?: {
    default_percent?: number;
    display?: string;
    example_equity_usd?: number;
    example_risk_usd?: number;
    risk_percent_by_symbol?: Record<string, number>;
  };
  where_to_run?: Array<{ label: string; path: string; kind?: string }>;
  do_not?: string[];
  example?: Record<string, unknown>;
  combo_definition?: Record<string, unknown>;
};

export type StrategyCatalogResponse = {
  label?: string;
  disclaimer?: string;
  freeze_tags?: {
    strategy_logic?: string;
    pre_research_baseline?: string;
  };
  primary_working?: string;
  v1_profile?: Record<string, unknown>;
  strategies: StrategyCatalogItem[];
  all_combinations?: Array<Record<string, unknown>>;
  sizing_example?: {
    title: string;
    equity_usd: number;
    risk_percent: number;
    risk_usd: number;
    entry: number;
    stop: number;
    risk_per_unit: number;
    qty: number;
    tp1_2r: number;
    outcome: string;
  };
  timestamp?: string;
};

export function fetchStrategyCatalog() {
  return getJson<StrategyCatalogResponse>("/api/research/strategies");
}

export type StrategyRankCell = {
  symbol: string;
  coin: string;
  net_pnl_usd: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  trades_closed: number;
  fees_usd: number | null;
  max_drawdown_usd: number | null;
  passes: boolean;
  fail_reasons: string[];
};

export type StrategyRankAlias = { id: string; kind: string; note: string };

export type StrategyRankRow = {
  rank: number;
  key: string;
  style: string;
  family: string;
  strategy_id: string;
  direction: string;
  timeframe: string;
  in_catalog: boolean;
  catalog_id: string | null;
  name: string;
  description_source: "catalog" | "combination_registry" | "rank_file" | string;
  summary: string;
  gates: string[];
  variant: string | null;
  coins: number;
  coins_passing: number;
  coins_net_positive: number;
  passing_symbols: string[];
  trades: number;
  net_pnl_usd: number | null;
  fees_usd: number | null;
  worst_cell_net_usd: number | null;
  best_cell_net_usd: number | null;
  execution_models: string[];
  fill_label: string;
  risk_usd: number[];
  leverage: number[];
  sizing_note: string | null;
  usd_comparable: boolean;
  aliases: StrategyRankAlias[];
  alias_of: StrategyRankAlias | null;
  do_not_use: boolean;
  do_not_use_reason: string | null;
  cells: StrategyRankCell[];
};

export type StrategyRankGroup = {
  id: string;
  label: string;
  timeframes: string;
  cells?: number | null;
  cells_net_positive?: number | null;
  cells_passing?: number | null;
  caveats: string[];
  hidden?: { duplicates: number; failures: number };
  rows: StrategyRankRow[];
};

export type StrategyRankResponse = {
  status: "OK" | "MISSING" | "EMPTY" | "INVALID" | string;
  error?: string | null;
  source?: { path?: string; written_by?: string; modified_utc?: string | null };
  window?: {
    start: string | null;
    end: string | null;
    note?: string;
    in_sample?: boolean;
    sample_note?: string;
    coins: string[];
    market?: string;
    account?: {
      principal_usd?: number | null;
      risk_usd?: number | null;
      leverage?: number | null;
      exceptions?: string;
    };
    fees?: { taker_pct?: number | null; maker_pct?: number | null };
  };
  pass_rule?: { text?: string; rule?: Record<string, string> };
  caveats?: string[];
  cells_ok?: number | null;
  cells_not_run?: number | null;
  groups: StrategyRankGroup[];
  timestamp?: string;
};

export function fetchStrategyRank() {
  return getJson<StrategyRankResponse>("/api/research/strategies/rank");
}

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

/** How completely funding settlements cover a trade (or, on a row, the worst over its trades). */
export type FundingCoverageStatus = "COMPLETE" | "PARTIAL" | "UNRESOLVED" | "MISSING";

export type StrategyTradeRow = {
  trade_id?: string | number;
  /** Daily trend family: the book (1-19) that produced this trade (rotation / static 12 sleeves). */
  book?: number | null;
  /** Daily trend family: "rotation" or "static12" when the trade belongs to a multi-book sleeve. */
  sleeve?: string | null;
  trade_no?: number;
  symbol: string;
  timeframe: string;
  direction: string;
  signal_time?: string | null;
  /** CAUSAL_NEXT_OPEN: open time of the bar after the signal, where the entry filled. */
  fill_time?: string | null;
  signal_index?: number | null;
  fill_index?: number | null;
  /** e.g. "CAUSAL_NEXT_OPEN" on trades from the causal book. */
  execution_model?: string | null;
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
  leverage?: number;
  notional_entry_usd?: number;
  margin_usd?: number | null;
  liquidation_price?: number | null;
  fee_entry_usd?: number;
  fee_exit_usd?: number;
  fee_total_usd?: number | null;
  fee_entry_rate?: number;
  fee_exit_rate?: number;
  gross_pnl_usd?: number | null;
  net_pnl_usd?: number | null;
  r_gross?: number | null;
  r_net?: number | null;
  /**
   * 4h book 16 (MTF_4H_16_BREAKOUT252): funding charged on the trade, signed $
   * (negative = paid). net_pnl_usd and r_net include it.
   */
  funding_usd?: number | null;
  funding_settlements?: number | null;
  funding_coverage_status?: FundingCoverageStatus | null;
  funding_unknown_schedule_checks?: number | null;
  funding_missing_records?: number | null;
  /** Net $ and R before funding (fees and slippage only). */
  net_pnl_before_funding_usd?: number | null;
  r_net_before_funding?: number | null;
  mae_r?: number | null;
  mfe_r?: number | null;
  account_equity?: number | null;
  capped_by_leverage?: boolean;
  requested_risk_usd?: number | null;
  /** Display-only labels at the signal bar (backend trade_context.py). */
  market_context?: {
    structure?: "CORRECT" | "WRONG" | "NEUTRAL" | string;
    trend?: string | null;
    volume?: string | null;
    relative_volume?: number | null;
    zone?: string | null;
    range_position?: number | null;
    decision_bar?: string | null;
    label?: string;
  } | null;
};

export type VectorbtParity = {
  verdict?: "PASS" | "FAIL" | "UNSUPPORTED" | "UNAVAILABLE" | "UNMAPPED" | "ERROR" | string;
  reason?: string | null;
  mismatches?: number;
  first_mismatch_field?: string | null;
  first_mismatch_trade?: string | null;
  bar_loop_trades?: number;
  vectorbt_trades?: number;
  error?: string | null;
};

export type StrategyMatrixRow = {
  /** Set on rows from a ranked strategy's own engine (CANDLE12, multi-cap, OSE). */
  engine_family?: string;
  /** Present only when the run asked for VectorBT execution (research only). */
  execution_engine?: "vectorbt" | "bar_loop";
  vectorbt_parity?: VectorbtParity;
  vectorbt_mismatches?: number;
  vectorbt_unrecomputed_fields?: string[];
  engine_fill_model?: string;
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
  pnl_model?: string | null;
  win_rate?: number | null;
  max_drawdown_usd?: number | null;
  average_R_usd_basis_gross?: number | null;
  average_R_usd_basis_net?: number | null;
  profit_factor_net?: number | null;
  win_rate_net?: number | null;
  max_drawdown_usd_net?: number | null;
  equity_curve_usd_net?: number[];
  /** 4h book 16: total funding over the row's trades, signed $ (negative = paid). */
  funding_total_usd?: number | null;
  /** 4h book 16: worst funding coverage over the row's trades. */
  funding_coverage_status?: FundingCoverageStatus | null;
  /** Backend wording for what the row's net PnL includes. */
  pnl_label?: string | null;
  r_definition?: string | null;
  warmup_bars?: number | null;
  bars_evaluated?: number | null;
  optimistic_comparison?: {
    model?: string;
    label?: string;
    net_pnl?: number | null;
    gross_pnl?: number | null;
    fees?: number | null;
    net_r?: number | null;
    gross_r?: number | null;
    winners?: number | null;
    trades?: number | null;
    win_rate?: number | null;
    max_drawdown_usd?: number | null;
    max_drawdown_r?: number | null;
  } | null;
  sd_review?: {
    min_closed_sample?: number;
    sample_warning?: boolean;
    slippage_rate?: number;
    market_type?: string;
    session?: string;
    contract_roll?: string;
    funding_applied?: boolean;
    event_days?: string;
    random_entry_baseline?: string;
    chronological_split?: {
      development_n?: number;
      development_avg_r_net?: number | null;
      holdout_n?: number;
      holdout_avg_r_net?: number | null;
    };
    daily_loss_slice?: {
      max_loss_r?: number;
      kept_n?: number;
      skipped_n?: number;
      avg_r_net?: number | null;
    };
    regime_counts?: { trend?: number; range?: number; unknown?: number };
  };
  risk_usd?: number;
  leverage?: number;
  bars_loaded?: number;
  candle_source?: string;
  trades?: StrategyTradeRow[];
  status?: string;
  strategy_id?: string;
  combo_version?: string;
  source?: string;
  symbol_role?: string | null;
  timeframe_role?: string;
  configured_risk_percent?: number;
  effective_risk_percent?: number;
  effective_risk_amount?: number;
  risk_source?: string;
  production_comparable?: boolean;
  /** Why a COMBO_02 v1 run is not production comparable (fill book). */
  production_comparable_note?: string | null;
  research_only?: boolean;
  dataset_fingerprint?: string | null;
  configuration_fingerprint?: string;
  requested_range?: Record<string, unknown>;
  actual_range?: Record<string, unknown>;
  paper_trade_created?: boolean;
  live_trade_created?: boolean;
  telegram_sent?: boolean;
  market_structure?: MarketStructureAnalytics | null;
  /** Run record (owner D202): first and last bar this cell loaded, warm-up included. */
  data_first_bar?: string | null;
  data_last_bar?: string | null;
  /** Last UTC day any backtest may read (the seal, 2026-07-14). */
  sealed_through?: string | null;
};

export type MarketStructureTableRow = {
  decision_time?: string | null;
  trade_id?: string | number | null;
  trade_status?: string | null;
  entry?: string | null;
  direction?: string | null;
  regime_4h?: string | null;
  trend_4h?: string | null;
  structure_4h?: string | null;
  bos_4h?: string | null;
  regime_1h?: string | null;
  trend_1h?: string | null;
  structure_1h?: string | null;
  bos_1h?: string | null;
  regime_15m?: string | null;
  trend_15m?: string | null;
  structure_15m?: string | null;
  bos_15m?: string | null;
  mtf_alignment?: string | null;
  mtf_score?: number | null;
  volatility_state?: string | null;
  choppiness_state?: string | null;
  signal_stage?: string | null;
  final_strategy_decision?: string | null;
  rejection_reason?: string | null;
  win_loss?: string | null;
  r_multiple?: number | null;
  market_regime?: string | null;
  regime_confidence?: number | null;
  research_label?: string | null;
  "15m_status"?: string | null;
};

export type MarketStructureAnalytics = {
  status?: string;
  analytics_enabled?: boolean;
  analytics_version?: string;
  analytics_runtime_seconds?: number;
  strategy_runtime_seconds?: number | null;
  feature_config_fingerprint?: string;
  feature_config?: Record<string, unknown>;
  "15m_status"?: string;
  closed_candle_policy?: string;
  metadata?: Record<string, unknown>;
  by_bar?: Record<string, unknown>[];
  table_rows?: MarketStructureTableRow[];
  trade_regime_summary?: {
    regime?: string;
    timeframe?: string;
    group_key?: string;
    total_trades?: number;
    wins?: number;
    losses?: number;
    win_rate?: number | null;
    sum_R?: number | null;
    average_R?: number | null;
    median_R?: number | null;
    profit_factor?: number | null;
    gross_pnl?: number | null;
    fees?: number | null;
    net_pnl?: number | null;
    max_drawdown?: number | null;
    average_holding_time?: number | null;
  }[];
  regime_opportunity_summary?: {
    regime?: string;
    bars_in_regime?: number;
    accepted_signals?: number;
    rejected_signals?: number;
    trades_executed?: number;
    signal_rate?: number | null;
    trade_rate?: number | null;
  }[];
  mtf_alignment_summary?: {
    mtf_alignment?: string;
    bars?: number;
    accepted_signals?: number;
    avg_mtf_score?: number | null;
    avg_R_accepted?: number | null;
    win_rate_accepted?: number | null;
  }[];
  quality_report?: {
    total_1h_decision_bars?: number;
    bars_with_4h_features?: number;
    bars_with_1h_features?: number;
    bars_with_15m_features?: number;
    bars_with_future_feature_violation?: number;
    bars_with_missing_feature_data?: number;
    bars_with_unknown_regime?: number;
    bars_with_timestamp_mismatch?: number;
    bars_with_duplicate_feature_rows?: number;
  };
  artifact_paths?: Record<string, string>;
  error?: string;
  disclaimer?: string;
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
  leverage?: number;
  strategy_id?: string;
  combo_version?: string;
  source?: string;
  setup_timeframe?: string;
  htf_timeframes?: string[];
  htf_alignment?: string;
  configured_risk_percent?: number;
  effective_risk_percent?: number;
  effective_risk_amount?: number;
  risk_source?: string;
  production_comparable?: boolean;
  research_only?: boolean;
  short_status?: string;
  period_mode?: string;
  dataset_fingerprint?: string;
  configuration_fingerprint?: string;
  paper_trade_created?: boolean;
  live_trade_created?: boolean;
  telegram_sent?: boolean;
  fee_model?: {
    taker_fee?: number;
    maker_fee?: number;
    exit_fee?: number;
    leverage?: number;
    note?: string;
  };
  fee_metadata?: Record<string, unknown>;
  symbols?: string[];
  timeframes?: string[];
  rows: StrategyMatrixRow[];
  elapsed_seconds?: number;
  disclaimer?: string;
  timestamp?: string;
  reason?: string;
  error_code?: string;
};

export type OhlcvRangeRow = {
  symbol: string;
  timeframe: string;
  bars: number;
  start?: string | null;
  end?: string | null;
  /** Bars a backtest may use: those before the seal (2026-07-15 00:00 UTC). Metadata only. */
  sealed_bars?: number;
  sealed_end?: string | null;
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
  /** Holdout-guarded strategy: the server caps the window before 2026-07-15 at any timeframe. */
  holdout?: boolean;
}) {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start: params.start,
  });
  if (params.end) q.set("end", params.end);
  if (params.pad_bars != null) q.set("pad_bars", String(params.pad_bars));
  if (params.holdout) q.set("holdout", "true");
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
export function fetchCombo02CandidateResults(
  stamp?: string,
  opts?: { signal?: AbortSignal },
) {
  const q = new URLSearchParams();
  if (stamp) q.set("stamp", stamp);
  const qs = q.toString();
  return getJson<Combo02CandidateResultsResponse>(
    `/api/research/combo02-candidate-results${qs ? `?${qs}` : ""}`,
    opts,
  );
}

export type CrossSectionalHoldoutRow = {
  candidate_id: string;
  family: string;
  lookback: number;
  rebalance_hours: number;
  sides: number;
  inverse_volatility: boolean;
  beta_neutral: boolean;
  robust_score: number;
  holdout_total_return: number;
  holdout_cagr: number;
  holdout_sharpe: number;
  holdout_sortino: number;
  holdout_max_drawdown: number;
  holdout_annualized_volatility: number;
  holdout_average_turnover: number;
  holdout_average_gross_exposure: number;
  holdout_fee_cost: number;
  holdout_slippage_cost: number;
  holdout_funding_cost: number;
  holdout_liquidation_warning_bars: number;
};

export type CrossSectionalDiscoveryResponse = {
  status: "OK" | "EMPTY" | "ERROR" | string;
  research_only: boolean;
  read_only: boolean;
  execution_rights: boolean;
  generated_at_utc?: string;
  manifest?: Record<string, unknown>;
  selected?: CrossSectionalHoldoutRow[];
  trade_signals?: {
    latest?: {
      status?: string;
      validation_status?: string;
      trade_status?: string;
      data_status?: string;
      decision_time?: string;
      valid_until?: string;
      selected_model?: Record<string, unknown>;
      development_metrics?: Record<string, unknown>;
      holdout_metrics?: Record<string, unknown>;
      signals?: Array<{
        symbol: string;
        action: "BUY" | "SELL" | "NO_TRADE" | string;
        model_action?: string;
        momentum?: number | null;
        symbol_validation_passed?: boolean;
        holdout_metrics?: Record<string, unknown>;
        take_profit?: number;
        stop_loss?: number;
        max_hold_hours?: number;
        reason?: string;
      }>;
      note?: string;
    };
    manifest?: Record<string, unknown>;
  } | null;
  breakout_risk?: {
    latest?: {
      status?: string;
      validation_status?: string;
      trade_status?: string;
      data_status?: string;
      decision_time?: string;
      valid_until?: string;
      selected_entry?: Record<string, unknown>;
      selected_risk?: Record<string, unknown>;
      development_robust_score?: number;
      holdout_metrics?: Record<string, unknown>;
      inverted_direction_metrics?: Record<string, unknown>;
      direction_validated?: boolean;
      direction_audit_status?: string;
      side_metrics?: Record<string, Record<string, unknown>>;
      family_comparison?: Record<string, Record<string, unknown>>;
      risk_comparison?: Record<string, Record<string, unknown>>;
      signals?: Array<{
        symbol: string;
        action: "BUY" | "SELL" | "NO_TRADE" | string;
        model_action?: string;
        reason?: string;
      }>;
      note?: string;
    };
    manifest?: Record<string, unknown>;
  } | null;
  daily_signals?: {
    latest?: {
      status?: string;
      validation_status?: string;
      trade_status?: string;
      data_status?: string;
      decision_time?: string;
      valid_until?: string;
      selected_model?: Record<string, unknown>;
      selected_model_holdout?: Record<string, unknown>;
      regime?: Record<string, unknown>;
      signals?: Array<{
        symbol: string;
        action: "LONG" | "SHORT" | "FLAT" | string;
        model_action?: string;
        target_weight?: number;
        notional_leverage?: number;
        momentum?: number | null;
        eligible?: boolean;
        reason?: string;
      }>;
      note?: string;
    };
    manifest?: Record<string, unknown>;
    holdout?: Array<Record<string, unknown>>;
  } | null;
  artifacts?: Record<string, string>;
  note?: string;
  error?: string;
  paper_trade_created?: boolean;
  live_trade_created?: boolean;
  telegram_sent?: boolean;
};

export type DiscoveryRankingRow = {
  rank?: number | null;
  symbol?: string | null;
  combination_id?: string | null;
  direction?: string | null;
  sample_size?: number | null;
  walk_forward_windows?: number | null;
  oos_profit_factor?: number | null;
  oos_profit_factors?: Array<number | null> | null;
  research_passed?: boolean | null;
  promotion_status?: string | null;
  sharpe?: number | null;
  sortino?: number | null;
  profit?: number | null;
  operator_approved?: boolean;
  production_approved?: boolean;
  paper_eligible?: boolean;
  live_eligible?: boolean;
  telegram_eligible?: boolean;
};

export type DiscoveryRankingResponse = {
  run_id: string;
  rows: DiscoveryRankingRow[];
  disclaimer?: string | null;
};

/** Stored discovery run id only. This request does not start research. */
export function fetchLatestDiscoveryRun(opts?: { signal?: AbortSignal }) {
  return getJson<{ run_id: string | null }>("/api/research/market-structure-discovery-v1/latest", opts);
}

/** Read-only ranking for one stored research run. */
export function fetchDiscoveryRunRanking(runId: string, opts?: { signal?: AbortSignal }) {
  return getJson<DiscoveryRankingResponse>(
    `/api/research/market-structure-discovery-v1/${encodeURIComponent(runId)}/ranking`,
    opts,
  );
}

/** Saved research output only; this endpoint cannot run or promote a strategy. */
export function fetchCrossSectionalDiscovery(opts?: { signal?: AbortSignal }) {
  return getJson<CrossSectionalDiscoveryResponse>(
    "/api/research/cross-sectional-discovery/latest",
    opts,
  );
}

export type ShortResearchCandidate = {
  symbol: string;
  timeframe?: string;
  direction?: string;
  strategy_id?: string;
  combo_version?: string;
  source?: string;
  research_tier?: string;
  state?: string;
  research_quality?: string;
  paper_eligible?: boolean;
  production_approved?: boolean;
  telegram_eligible?: boolean;
  run_id?: string;
  created_at?: string;
  updated_at?: string;
  data_health?: Record<string, unknown>;
  base_research?: Record<string, unknown>;
  oos?: Record<string, unknown>;
  oos_split?: Record<string, unknown>;
  direction_checks?: Array<Record<string, unknown>>;
  rejection_reasons?: Array<Record<string, unknown>>;
  portfolio?: Record<string, unknown>;
  risk_simulation?: Record<string, unknown>;
  sample_size?: Record<string, unknown>;
  quality_warnings?: Array<Record<string, unknown>>;
  lookahead_audit?: Record<string, unknown>;
  forensic_lookahead?: Record<string, unknown>;
  trade_forensic_audits?: Array<Record<string, unknown>>;
  research_windows?: Record<string, unknown>;
  window_status?: string;
  partition_counts?: Record<string, unknown>;
  oos_development?: Record<string, unknown>;
  oos_validation?: Record<string, unknown>;
  execution_model?: Record<string, unknown>;
  reconciliation?: Record<string, unknown>;
  prepaper_review?: Record<string, unknown>;
  blockers?: string[];
  human_label?: string;
  configuration_hash?: string;
  dataset_hash?: string;
  engine_version?: string;
  strategy_fingerprint?: string;
  historical_disclaimer?: string;
  labels?: Record<string, string>;
};

export type ShortResearchListResponse = {
  status?: string;
  strategy_id?: string;
  combo_version?: string;
  source?: string;
  direction?: string;
  paper_eligible?: boolean;
  production_approved?: boolean;
  telegram_eligible?: boolean;
  disclaimer?: string;
  candidates?: ShortResearchCandidate[];
  batch_summary?: Record<string, unknown>;
  labels?: Record<string, string>;
  read_only?: boolean;
  execution_rights?: boolean;
  v1_unchanged?: boolean;
  timestamp?: string;
};

/** Read-only SHORT research candidates (never paper / Telegram / production). */
export function fetchShortResearchCandidates(opts?: { signal?: AbortSignal }) {
  return getJson<ShortResearchListResponse>(
    "/api/research/short-candidates",
    opts,
  );
}

export function fetchShortResearchCandidate(
  symbol: string,
  opts?: { signal?: AbortSignal },
) {
  return getJson<{
    status?: string;
    candidate?: ShortResearchCandidate;
  }>(`/api/research/short-candidates/${encodeURIComponent(symbol)}`, opts);
}

/** Starts SHORT research batch only — never opens paper/live trades. */
export async function runShortResearchCandidates(body?: {
  symbols?: string[];
  run_id?: string;
  run_oos?: boolean;
}) {
  const res = await fetch(`${API_BASE}/api/research/short-candidates/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    throw new Error(
      `API /api/research/short-candidates/run failed: ${res.status}`
    );
  }
  return res.json() as Promise<ShortResearchListResponse & { run_id?: string }>;
}

export type DynamicEligibilityReason = {
  rule?: string;
  name?: string;
  actual?: unknown;
  required?: string;
  passed?: boolean;
  detail?: string;
};

export type DynamicCandidateRow = {
  id?: string;
  symbol: string;
  state?: string;
  state_reason?: string | null;
  badge?: string;
  research_tier?: string | null;
  operational_state?: string | null;
  operator_paper_approval?: string | null;
  production_approval?: string | null;
  production_approved?: boolean;
  telegram_eligibility?: string | null;
  portfolio_status?: string | null;
  blocked_reason?: string | null;
  base_backtest_status?: string | null;
  oos_display_status?: string | null;
  source?: string | null;
  safety_badges?: string[];
  eligibility_reasons?: DynamicEligibilityReason[];
  oos_rule_reasons?: DynamicEligibilityReason[];
  discovery_rank?: number | null;
  discovery_volume_usd?: number | null;
  ohlcv_1h_start_utc?: string | null;
  ohlcv_1h_end_utc?: string | null;
  ohlcv_4h_start_utc?: string | null;
  ohlcv_4h_end_utc?: string | null;
  ohlcv_1h_completeness?: number | null;
  ohlcv_4h_completeness?: number | null;
  history_days?: number | null;
  data_health_block_reason?: string | null;
  backtest_tier?: string | null;
  backtest_status?: string | null;
  backtest_trade_count?: number | null;
  backtest_win_rate?: number | null;
  backtest_net_avg_r?: number | null;
  backtest_profit_factor?: number | null;
  backtest_net_pnl?: number | null;
  backtest_fees?: number | null;
  backtest_max_dd_r?: number | null;
  backtest_max_losing_streak?: number | null;
  backtest_engine_fingerprint?: Record<string, unknown> | null;
  oos_status?: string | null;
  oos_window_start_utc?: string | null;
  oos_window_end_utc?: string | null;
  oos_trade_count?: number | null;
  oos_net_avg_r?: number | null;
  oos_profit_factor?: number | null;
  oos_net_pnl?: number | null;
  oos_max_dd_r?: number | null;
  oos_max_losing_streak?: number | null;
  portfolio_overlap_btc?: number | null;
  portfolio_overlap_eth?: number | null;
  portfolio_overlap_sol?: number | null;
  peak_concurrent_positions?: number | null;
  portfolio_incremental_dd_r?: number | null;
  portfolio_report?: Record<string, unknown> | null;
  operator_approved?: boolean;
  experimental_paper_approved?: boolean;
  operator_approved_by?: string | null;
  operator_approved_at_utc?: string | null;
  approval_note?: string | null;
  telegram_eligible?: boolean;
  strategy_id?: string | null;
  combo_version?: string | null;
  risk_percent?: number | null;
  updated_at_utc?: string | null;
  state_updated_at_utc?: string | null;
};

export type DynamicCandidatesResponse = {
  status: string;
  strategy_id?: string;
  candidates?: DynamicCandidateRow[];
  count?: number;
  disclaimer?: string;
  read_only?: boolean;
  v1_unchanged?: boolean;
  timestamp?: string;
};

/** Read-only dynamic v2 candidate registry (no enable-for-v1). */
export function fetchDynamicCandidates(
  state?: string,
  opts?: { signal?: AbortSignal },
) {
  const q = new URLSearchParams();
  if (state) q.set("state", state);
  const qs = q.toString();
  return getJson<DynamicCandidatesResponse>(
    `/api/research/candidates${qs ? `?${qs}` : ""}`,
    opts,
  );
}

export type DynamicCandidateDetailResponse = {
  status: string;
  candidate?: DynamicCandidateRow;
  audit?: Array<Record<string, unknown>>;
  disclaimer?: string;
  read_only_except_approve_paper?: boolean;
  timestamp?: string;
  symbol?: string;
};

/** Persisted registry detail for one symbol (expand/drawer). */
export function fetchDynamicCandidateDetail(
  symbol: string,
  opts?: { signal?: AbortSignal },
) {
  return getJson<DynamicCandidateDetailResponse>(
    `/api/research/candidates/${encodeURIComponent(symbol)}`,
    opts,
  );
}

export type DynamicAdvanceRunSummary = {
  health_ready: number;
  backtests_started: number;
  oos_started: number;
  rejected: number;
  advanced: number;
  errors: number;
};

export type DynamicAdvanceRegistrySummary = {
  discovered: number;
  data_pending: number;
  data_ready: number;
  backtest_completed: number;
  research_rejected: number;
  oos_failed: number;
  v2_paper_candidate: number;
  paper_validating: number;
  experimental_paper_candidates?: number;
  production_approved: number;
  suspended: number;
};

export type DynamicAdvanceResponse = DynamicCandidatesResponse & {
  run_summary?: DynamicAdvanceRunSummary;
  registry_summary?: DynamicAdvanceRegistrySummary;
  health?: Array<Record<string, unknown>>;
  backtests?: Array<Record<string, unknown>>;
  oos?: Array<Record<string, unknown>>;
  telegram_eligible?: boolean;
  paper_trades_created?: number;
};

/** Research-only advance: data-health → COMBO_02 backtest → OOS. */
export async function advanceDynamicCandidates(body?: {
  symbols?: string[];
  skip_oos?: boolean;
  health_only?: boolean;
  backtest_only?: boolean;
  limit?: number;
}) {
  const res = await fetch(`${API_BASE}/api/research/candidates/advance`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (await res.json()) as DynamicAdvanceResponse;
}

export type BacktestJob = {
  job_id?: string | null;
  status: string;
  /** Run record (owner D202): earliest first bar and latest last bar any cell loaded. */
  data_window?: { first_bar?: string | null; last_bar?: string | null; sealed_through?: string | null };
  symbols?: string[];
  timeframes?: string[];
  direction?: string;
  combination_id?: string;
  combination_name?: string;
  playbook?: string | null;
  label?: string | null;
  limit?: number;
  risk_usd?: number;
  principal_usd?: number;
  leverage?: number;
  taker_fee_pct?: number;
  maker_fee_pct?: number;
  include_trades?: boolean;
  vectorbt_execution?: boolean;
  vectorbt_mismatch_total?: number;
  /** Owner D212: the run read data after the seal (owner-only dev mode). */
  dev_mode?: boolean;
  run_label?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  total_cells?: number;
  done_cells?: number;
  pct?: number;
  progress_percent?: number;
  current?: string;
  phase?: string;
  bars_processed?: number;
  total_bars?: number;
  trades?: number;
  trades_generated?: number;
  last_heartbeat?: string | null;
  rows_loaded?: number;
  load_meta?: Record<string, unknown>;
  timing?: Record<string, unknown>;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  error_code?: string | null;
  rows?: StrategyMatrixRow[];
  elapsed_seconds?: number | null;
  disclaimer?: string | null;
  strategy_id?: string;
  combo_version?: string;
  source?: string;
  setup_timeframe?: string;
  htf_timeframes?: string[];
  htf_alignment?: string;
  risk_mode?: string;
  configured_risk_percent?: number;
  effective_risk_percent?: number;
  effective_risk_amount?: number;
  risk_source?: string;
  symbol_role?: string | null;
  production_comparable?: boolean;
  /** Why a COMBO_02 v1 run is not production comparable (fill book). */
  production_comparable_note?: string | null;
  fill_book?: string;
  research_only?: boolean;
  short_status?: string;
  period_mode?: string;
  dataset_fingerprint?: string | null;
  configuration_fingerprint?: string;
  paper_trade_created?: boolean;
  live_trade_created?: boolean;
  telegram_sent?: boolean;
  safety_notice?: string;
  mismatch_reasons?: string[];
};

const BACKTEST_FETCH_MS = 20_000;

function backtestAbortSignal(ms = BACKTEST_FETCH_MS): AbortSignal | undefined {
  try {
    return AbortSignal.timeout(ms);
  } catch {
    return undefined;
  }
}

export function fetchBacktestJobStatus() {
  return getJson<BacktestJob & { timestamp?: string }>(
    "/api/research/long-strategy/backtest/job",
    { signal: backtestAbortSignal() }
  );
}

/** One losing (or fee-flipped) trade in GET .../backtest/loss-analysis. Display only. */
export type LossAnalysisTrade = {
  trade_no: number | null;
  trade_id: string | number | null;
  entry_time: string | null;
  exit_time: string | null;
  direction: string | null;
  outcome: string | null;
  net_usd: number | null;
  gross_usd: number | null;
  fees_usd: number | null;
  r_net: number | null;
  primary_reason: string;
  flags: string[];
  metrics: {
    stop_distance_pct: number | null;
    stop_distance_atr: number | null;
    tp1_distance_r: number | null;
    mfe_r_before_exit: number | null;
    mfe_pct_to_tp1: number | null;
    bars_to_mfe: number | null;
    hold_bars: number | null;
    mae_r: number | null;
    fees_r: number | null;
  };
  hindsight: {
    reached_tp1_after_exit: boolean | null;
    bars_after_exit: number | null;
    stop_needed_r: number | null;
    max_favourable_after_exit_r: number | null;
  } | null;
  explanation: string | null;
  hindsight_label?: string | null;
};

export type LossAnalysisWhatIf = {
  id: string;
  label: string;
  /** false: not computable from this job; `caveat` says why. */
  computable: boolean;
  trades: number | null;
  net_usd: number | null;
  delta_usd: number | null;
  winners_lost: number | null;
  caveat: string | null;
};

export type LossAnalysisSuggestion = {
  rule: string;
  reason_bucket: string;
  trades_affected: number | null;
  net_change_usd: number | null;
  winners_lost: number | null;
  label: string;
};

export type LossAnalysisResponse = {
  symbol: string;
  timeframe: string;
  strategy_id: string | null;
  direction: string | null;
  /** The frozen BTC 1h CANDLE12_V2 LONG book: diagnostics only, no suggestions. */
  frozen_book: boolean;
  suggestions_note: string | null;
  period: { start: string | null; end: string | null };
  hindsight_bars: number | null;
  summary: {
    trades: number;
    winners: number;
    losers: number;
    scratches: number;
    net_usd: number | null;
    gross_usd: number | null;
    fees_usd: number | null;
    by_reason: Array<{ reason: string; count: number; net_usd: number | null; share_of_loss: number | null }>;
    by_context: Array<{ structure: string; count: number; net_usd: number | null }>;
    largest_losses: Array<{ trade_no: number | null; entry_time: string | null; net_usd: number | null; reason: string }>;
    loss_weeks: Array<{ week: string; count: number; net_usd: number | null }>;
    held_would_have_won: { count: number; net_usd: number | null };
    /** Flags left out of the reasons because winners carry them about as often (rates 0..1). */
    non_discriminating_flags?: Array<{
      flag: string;
      losers_rate: number | null;
      winners_rate: number | null;
      reason: string | null;
    }>;
    loss_usd?: number | null;
    trades_unmatched_to_candles?: number | null;
  };
  losses: LossAnalysisTrade[];
  fee_flipped: LossAnalysisTrade[];
  what_ifs: LossAnalysisWhatIf[];
  suggestions: LossAnalysisSuggestion[];
  disclaimer: string;
  /** Not in the contract; checked when the server sends it. */
  job_id?: string | null;
};

/** A refusal from the loss-analysis endpoint (409: no finished job / no trades). */
export class LossAnalysisError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "LossAnalysisError";
    this.status = status;
  }
}

/** Read-only diagnostics of the finished backtest job's losing trades for one cell. */
export async function fetchBacktestLossAnalysis(
  symbol: string,
  timeframe: string,
  opts?: { signal?: AbortSignal },
): Promise<LossAnalysisResponse> {
  const q = new URLSearchParams({ symbol, timeframe });
  const path = `/api/research/long-strategy/backtest/loss-analysis?${q.toString()}`;
  const res = await fetch(`${API_BASE}${path}`, { signal: opts?.signal });
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail != null) detail = JSON.stringify(body.detail);
    } catch {
      detail = "";
    }
    throw new LossAnalysisError(detail || `API ${path} failed: ${res.status}`, res.status);
  }
  return res.json() as Promise<LossAnalysisResponse>;
}

export type CapBookRow = {
  combination_id: string;
  symbol: string;
  timeframe: string;
  direction: string;
  cap_group: string;
  risk_usd: number;
  sample_size: number;
  average_R: number | null;
  average_R_net: number | null;
  pnl_usd_net: number | null;
  max_drawdown_R: number | null;
  cell_seconds: number;
};

export type CapBookShard = {
  shard: string;
  agent_name: string;
  cap_group: string;
  coins: string[];
  status: string;
  market_direction: string | null;
  principal_usd: number;
  elapsed_seconds: number;
  cells_finished: number;
  rows: CapBookRow[];
};

export type CapBookAgentsResponse = {
  status: string;
  poll_interval_seconds: number;
  timestamp?: string;
  shards: CapBookShard[];
  /** Owner D202: the Profit checker window lies inside the seal; it cannot be re-run. */
  sealed?: boolean;
  sealed_label?: string;
  rerun_allowed?: boolean;
  rerun_refused_reason?: string;
  window?: Record<string, { start: string; end: string }>;
};

export function fetchCapBookAgents(signal?: AbortSignal) {
  return getJson<CapBookAgentsResponse>("/api/research/cap-book-agents", { signal });
}

export type PassedRegimeTrend = "BULLISH" | "BEARISH" | "NEUTRAL";

export type PassedRegimeSide = {
  strategy_id: string;
  timeframe: string;
  direction: string;
};

export type PassedRegimeSignal = {
  strategy_id: string;
  direction: string;
  timeframe: string;
  symbol: string;
  setup_bar_time: string;
  trend: string;
  as_of_daily_bar: string;
  outcome: "opened" | "not_opened" | "missed" | string;
  reason: string;
  position_id?: string | number | null;
  recorded_at: string;
  /** Missed rows only, e.g. "missed: evaluated late (56m)". */
  note?: string;
  lag_seconds?: number | null;
};

/**
 * A bar the watcher reached too late. "missed": a signal on a bar evaluated
 * after the late-open bound (never opened). "not_evaluated": due bars that were
 * never evaluated (no strategy fields; ``bars`` from ``setup_bar_time`` to ``last_bar_time``).
 */
export type PassedRegimeMissedRow = {
  outcome: "missed" | "not_evaluated" | string;
  reason?: string;
  note?: string;
  symbol?: string;
  strategy_id?: string;
  direction?: string;
  timeframe?: string;
  setup_bar_time?: string;
  last_bar_time?: string;
  bars?: number;
  lag_seconds?: number | null;
  recorded_at?: string;
};

/** When the watcher last finished a bar, and how far behind it is. */
export type PassedRegimeHeartbeat = {
  last_eval_at?: string;
  last_eval_symbol?: string;
  last_eval_bar?: string;
  /** Seconds from that bar's close to its evaluation. */
  last_eval_lag_seconds?: number | null;
  seconds_since_last_eval?: number | null;
  /** True after more than an hour plus the late-open bound with no evaluation. */
  stale?: boolean;
  last_catchup_at?: string;
  /** Closed bars after the watermark not processed yet. */
  pending_bars?: number;
  pending_by_symbol?: Record<string, number>;
  watermarks?: Record<string, string>;
  state_file?: string;
  max_catchup_bars?: number;
  max_open_lag_seconds?: number;
};

export type PassedRegimeEvaluatedBar = {
  symbol: string;
  bar: string;
  trend: string;
  as_of_daily_bar: string;
  sides_evaluated: number;
};

/** One coin's per-coin allowlist: the passed sides that passed / failed that coin's test. */
export type PassedSymbolAllowlistEntry = {
  symbol?: string;
  allowed_sides: PassedRegimeSide[];
  blocked_sides: PassedRegimeSide[];
  tested_window?: { start?: string | null; end?: string | null };
  decided_at?: string | null;
  pass_rule?: string | null;
  pnl_model?: string | null;
  results?: Array<Record<string, unknown>>;
};

/** Coins with an entry trade only their allowed sides; coins not listed run every passed side. */
export type PassedSymbolAllowlist = {
  version?: number;
  source?: string;
  /** Regime-book runs only: false when the run ignored the allowlist. */
  applied?: boolean;
  symbols: Record<string, PassedSymbolAllowlistEntry>;
};

export type PassedRegimeResponse = {
  enabled: boolean;
  trend: PassedRegimeTrend;
  /** False when the BTC daily read failed; the bar is retried and nothing opens. */
  daily_read_ok: boolean;
  daily_read_error: string;
  /** 1h bar the regime was read for, and when the daily bars were read. */
  regime_bar: string;
  regime_read_at: string;
  as_of_daily_bar: string;
  daily_bars: number;
  min_daily_bars: number;
  short_history: boolean;
  allowed: PassedRegimeSide[];
  skipped: PassedRegimeSide[];
  allowed_strategy_ids: string[];
  skipped_strategy_ids: string[];
  latest_signals: PassedRegimeSignal[];
  last_evaluated_bar: PassedRegimeEvaluatedBar | null;
  last_skip: string | null;
  opened_count: number;
  /** Signals on bars evaluated after the late-open bound. Absent on older backends. */
  missed_signals?: number;
  /** Due bars that were never evaluated. */
  unevaluated_bars?: number;
  /** Newest first, up to 20. */
  recent_missed?: PassedRegimeMissedRow[];
  /** Late-tip missed signals since the forward start (owner D181); `missed_signals` is all-time. */
  missed_signals_since?: { since: string; count: number };
  /** Coins whose bars are never evaluated (e.g. history_short), shown once instead of hourly rows. */
  not_tradable?: { symbol: string; reason: string; label: string; bars: number; last: string | null }[];
  heartbeat?: PassedRegimeHeartbeat | null;
  /** False while new passed-regime entries are off; signals are still recorded. */
  book_open?: boolean;
  book_note?: string;
  /** Coins the passed book may open. Large 2%, mid 1%. */
  symbols?: string[];
  symbol_risk?: {
    large_symbols: string[];
    mid_symbols: string[];
    large_risk_percent: number;
    mid_risk_percent: number;
  };
  /** Per-coin allowlist the watcher applies. */
  symbol_allowlist?: PassedSymbolAllowlist;
  watcher_loop_enabled?: boolean;
  timestamp?: string;
};

/** Read-only: the passed-book regime view. There is no write counterpart. */
export function fetchPassedRegime(signal?: AbortSignal) {
  return getJson<PassedRegimeResponse>("/api/paper/passed-regime", { signal });
}

/** One virtual trade of the 4h book 16 paper book (the screen ledger's trade fields). */
export type Book16PaperTrade = {
  trade_id: string;
  symbol: string;
  side: number;
  signal_time: string;
  entry_time: string;
  entry_price: number;
  exit_time?: string;
  exit_price?: number;
  exit_reason?: string;
  qty: number;
  notional: number;
  stop_price: number;
  risk_fraction: number;
  price_pnl: number;
  fees: number;
  funding: number;
  funding_coverage_status: string;
  net_pnl?: number;
  mark_price?: number;
  unrealized_usd?: number;
};

export type Book16PaperBook = {
  closed: Book16PaperTrade[];
  pending_closed: Book16PaperTrade[];
  open: Book16PaperTrade | null;
  equity: number;
  last_bar: string | null;
  forward_bars: number;
  skipped_lev_cap: number;
};

export type Book16PaperResponse = {
  strategy_id: string;
  mirrors: string;
  symbols: string[];
  timeframe: string;
  mode: string;
  live_orders: boolean;
  start: string;
  account_equity_start: number;
  risk_fraction: number;
  status: string;
  error: string | null;
  updated_utc: string | null;
  books: Record<string, Book16PaperBook>;
  loop_enabled?: boolean;
  timestamp?: string;
};

/** Read-only: the 4h book 16 virtual paper ledger (mirror of forward test BOOK16_4H_FWD-1). */
export function fetchBook16Paper(signal?: AbortSignal) {
  return getJson<Book16PaperResponse>("/api/paper/book16", { signal });
}

export type RegimeBacktestPeriod = {
  period_start?: string | null;
  period_end?: string | null;
  bars_loaded?: number;
  warmup_bars?: number;
  bars_evaluated?: number;
  capped?: boolean;
};

export type RegimeBacktestWindow = {
  mode?: "dates" | "lookback";
  timeframe?: string;
  start_date?: string | null;
  end_date?: string | null;
  limit?: number | null;
  max_eval_bars?: number;
  /** What the screen asked for. */
  requested?: {
    mode?: "dates" | "lookback";
    timeframe?: string;
    start_date?: string | null;
    end_date?: string | null;
    limit?: number | null;
  };
  /** What the book walked: warm-up bars are loaded in front and never traded. */
  evaluated?: (RegimeBacktestPeriod & {
    symbols?: Record<string, RegimeBacktestPeriod>;
    /** Run record (owner D202): first and last 1h bar loaded, warm-up included. */
    data_first_bar?: string | null;
    data_last_bar?: string | null;
    sealed_through?: string | null;
  }) | null;
  /** BTC daily bars the regime clock read. */
  btc_daily?: { first_bar?: string | null; last_bar?: string | null };
  /** Per-symbol cap on evaluated 1h bars; the latest bars are kept when it applies. */
  cap?: { max_eval_bars?: number; applied?: boolean; symbols?: string[] };
};

export type RegimeBacktestStatus = {
  status: "idle" | "running" | "done" | "error" | "cancelled" | string;
  /** Owner D212: the run read data after the seal (owner-only dev mode). */
  dev_mode?: boolean;
  run_label?: string | null;
  job_id?: string | null;
  phase?: string;
  current?: string;
  symbols?: string[];
  timeframe?: string;
  limit?: number;
  window?: RegimeBacktestWindow | null;
  total_symbols?: number;
  done_symbols?: number;
  total_cells?: number;
  done_cells?: number;
  pct?: number;
  progress_percent?: number;
  walks?: number;
  walks_in_symbol?: number;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed_seconds?: number | null;
  /** Sizing and fees the book uses whatever the form says. */
  fixed_config?: {
    principal_usd?: number;
    taker_fee_pct?: number;
    maker_fee_pct?: number;
    leverage?: number;
    risk_rule?: string;
    fill_model?: string;
  };
  /** Fill book the regime run priced trades on, e.g. CAUSAL_NEXT_OPEN. */
  pnl_model?: string;
  execution_model_version?: string | null;
  /** Directions in scope for this run, e.g. ["LONG"]. */
  directions?: Array<"LONG" | "SHORT">;
  sides_in_scope?: Array<{ strategy_id: string; timeframe: string; direction: string }>;
  /** False when the run ignored the per-coin allowlist (every side on every coin). */
  apply_symbol_allowlist?: boolean;
  /** Per-coin filters the run applied (coins with an entry; sides in scope only). */
  symbol_allowlist?: PassedSymbolAllowlist | null;
  error?: string | null;
  skipped_symbols?: string[];
  summary?: {
    trades?: number;
    wins?: number;
    losses?: number;
    win_rate?: number | null;
    pnl_usd_net?: number;
    final_equity_usd?: number;
    max_drawdown_pct?: number;
    signals?: number;
    signals_taken?: number;
    by_strategy?: Array<{
      strategy_id?: string;
      direction?: string;
      timeframe?: string;
      signals?: number;
      taken?: number;
      trades?: number;
      wins?: number;
      losses?: number;
      pnl_usd_net?: number;
    }>;
  } | null;
  trades?: Array<{
    symbol?: string;
    strategy_id?: string;
    direction?: string;
    trend_at_entry?: string;
    signal_time?: string;
    exit_time?: string;
    outcome?: string;
    net_pnl_usd?: number | null;
    r_net?: number | null;
  }>;
};

export type RegimeBacktestStartBody = {
  symbols: string[];
  /** The book runs on 1h bars only; the server rejects anything else. */
  timeframe: "1h";
  /** Lookback mode: number of 1h bars ending at the seal (2026-07-14 23:00 UTC). */
  limit?: number;
  /** Calendar mode: UTC days, end inclusive. */
  start_date?: string;
  end_date?: string;
  /** Passed rows in scope. The Long book sends ["LONG"], the Short book ["SHORT"]. */
  directions?: Array<"LONG" | "SHORT">;
  /** Default true. False runs every passed side on every coin (per-coin pass/fail study). */
  apply_symbol_allowlist?: boolean;
  /** Owner-only dev mode (D212): honoured only when the server allows it. */
  dev_mode?: boolean;
};

export type RegimeBacktestStartResponse = {
  status: "STARTED" | "ALREADY_RUNNING" | "REJECTED" | string;
  error?: string;
  job?: RegimeBacktestStatus;
};

export function startRegimeBacktest(body: RegimeBacktestStartBody) {
  return fetch(`${API_BASE}/api/research/regime-backtest/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(async (res) => {
    if (!res.ok) throw await apiError("/api/research/regime-backtest/start", res);
    return res.json() as Promise<RegimeBacktestStartResponse>;
  });
}

export function fetchRegimeBacktest() {
  return getJson<RegimeBacktestStatus>("/api/research/regime-backtest");
}

/** Owner-only dev mode (D212): whether the server lets the switch be turned on. */
export type BacktestDevMode = {
  allowed: boolean;
  hint?: string | null;
  banner?: string;
  label?: string;
  sealed_through?: string;
};

export function fetchBacktestDevMode(signal?: AbortSignal) {
  return getJson<BacktestDevMode>("/api/research/backtest-dev-mode", { signal });
}

/** What a regime-book run executes (backend run_plan: its PASSED_SIDES rows and sizing). */
export type RegimeBacktestPlan = {
  directions: Array<"LONG" | "SHORT">;
  timeframe: string;
  sides_in_scope: Array<{ strategy_id: string; timeframe: string; direction: string }>;
  fixed_config?: RegimeBacktestStatus["fixed_config"];
  sealed_through?: string;
};

export function fetchRegimeBacktestPlan(directions: Array<"LONG" | "SHORT">, signal?: AbortSignal) {
  const q = new URLSearchParams({ directions: directions.join(",") });
  return getJson<RegimeBacktestPlan>(`/api/research/regime-backtest/plan?${q.toString()}`, { signal });
}

export function cancelRegimeBacktest() {
  return fetch(`${API_BASE}/api/research/regime-backtest/cancel`, { method: "POST" }).then(async (res) => {
    if (!res.ok) throw await apiError("/api/research/regime-backtest/cancel", res);
    return res.json() as Promise<{ status: string; job?: RegimeBacktestStatus }>;
  });
}

export async function startBacktestJob(body: {
  symbols: string[];
  timeframes: string[];
  /**
   * BOTH: two-sided request, COMBO_02_V2 family (the hedge book's V2 / V2_1B) and the
   * daily trend books (full long+short book).
   */
  direction?: "LONG" | "SHORT" | "BOTH";
  combination_id?: string;
  strategy_id?: string;
  combo_version?: string;
  setup_timeframe?: string;
  risk_mode?: string;
  research_risk_override?: boolean;
  limit?: number;
  risk_usd?: number;
  principal_usd?: number;
  leverage?: number;
  taker_fee_pct?: number;
  maker_fee_pct?: number;
  include_trades?: boolean;
  /** Research only: replay trades through VectorBT; its fills are used only on parity PASS. */
  vectorbt_execution?: boolean;
  start_date?: string;
  /** Inclusive UTC day: the backend loads bars with timestamp < end_date + 1 day. */
  end_date?: string;
  /** Owner-only dev mode (D212): honoured only when the server's BACKTEST_DEV_MODE_ALLOWED is true. */
  dev_mode?: boolean;
}) {
  const res = await fetch(`${API_BASE}/api/research/long-strategy/backtest/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: backtestAbortSignal(30_000),
  });
  if (!res.ok) {
    throw await apiError("/api/research/long-strategy/backtest/start", res);
  }
  return res.json() as Promise<{
    status: string;
    error?: string;
    error_code?: string;
    job?: BacktestJob;
    timestamp?: string;
    paper_trade_created?: boolean;
    live_trade_created?: boolean;
    telegram_sent?: boolean;
  }>;
}

export async function cancelBacktestJob() {
  const res = await fetch(`${API_BASE}/api/research/long-strategy/backtest/cancel`, {
    method: "POST",
    signal: backtestAbortSignal(),
  });
  if (!res.ok) {
    throw await apiError("/api/research/long-strategy/backtest/cancel", res);
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
  last_skip?: string | null;
  last_skip_bar?: string | null;
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
  /** Legacy RESEARCH_15M default (often 2%) — not COMBO_02 v1 book sizing. */
  risk_percent: number;
  risk_percent_legacy_research_15m?: number;
  risk_percent_label?: string;
  v1_book_risk?: Record<
    string,
    { tier?: string; risk_percent?: number; risk_pct_display?: string | null }
  >;
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

export type DashboardWatcher = {
  id: string;
  can_open: boolean;
  coins: unknown;
  risk: string;
  sides?: string[][];
};

export type DashboardReview = {
  read_only: boolean;
  as_of: string;
  armed_books: string[];
  watchers: DashboardWatcher[];
  telegram_text: string;
  forward_tests: Array<{ id: string; running: boolean; why: string }>;
  reset_blocked: boolean;
  stops: {
    counter_start: { first_run_utc?: string; ruling?: string } | null;
    limits: {
      consecutive_loss_halt?: number;
      consecutive_loss_symbol_halt?: number;
      peak_drawdown_halt_pct?: number;
      strategy_drawdown_halt_r?: number;
      daily_loss_halt_r?: number;
    };
    books: Record<
      string,
      {
        halted: boolean;
        halts: Array<{ halt: string; tripped_at?: string | null; resets_at?: string | null }>;
        paused_coins: Array<{ symbol?: string; resets_at?: string | null }>;
        consecutive_losses: number | null;
        symbol_streaks: Record<string, number>;
        drawdown_pct: number | null;
        strategy_drawdown_r: number | null;
        day_pnl_r: number | null;
        equity: number | null;
      }
    >;
    journal_tail: Array<Record<string, unknown>>;
    engine_loaded: boolean;
  };
  missed: {
    counter: number;
    unevaluated_bars: number;
    retained_rows: number;
    unretained: number;
    by_reason: Record<string, number>;
    by_book: Record<string, number>;
    by_day: Record<string, number>;
    after_restart_utc: string;
    after_restart: Array<Record<string, unknown>>;
    after_restart_unknown: Array<Record<string, unknown>>;
    note: string;
  };
  runners: Array<{
    runner: string;
    last_heartbeat: string | null;
    age_seconds: number | null;
    status: string;
    detail: string;
  }>;
  candle12_v2: {
    present: boolean;
    equity: number | null;
    realized_pnl: number | null;
    net_r: number | null;
    trades: number;
    open_risk: number | null;
  };
};

export function fetchPaperStatus() {
  return getJson<PaperStatus>("/api/paper/status");
}

export function fetchDashboardReview(signal?: AbortSignal) {
  return getJson<DashboardReview>("/api/paper/dashboard-review", { signal });
}

/** Evidence tag of one closed/cancelled ledger row (owner D187), computed at read time. */
export type PaperEvidenceTag =
  | "LEGACY"
  | "PRE_REWRITE"
  | "PRE_COUNTERS"
  | "CATCHUP_CLOSE"
  | "DUPLICATE_FILL"
  | "VALID";

export type PaperLedgerRow = PaperPosition & {
  book: "PASSED" | "V1" | "LEGACY";
  evidence_tag: PaperEvidenceTag | null;
  evidence_reason: string;
  evidence_matches: PaperEvidenceTag[];
  in_default_view: boolean;
  capped_by_leverage: boolean;
  equity_at_open: number;
  nominal_risk_usd: number | null;
  r_nominal: number | null;
  r_actual: number | null;
};

export type PaperLedgerBook = {
  id: "PASSED" | "V1" | "LEGACY";
  label: string;
  forward_start: string | null;
  forward_start_cite: string;
  equity: number;
  trades: number;
  pnl_usd: number;
  r_actual: number;
  r_nominal: number;
  r_ledger: number;
  capped: number;
  nominal_fallback: number;
};

export type PaperLedgerView = {
  read_only: true;
  available: boolean;
  error?: string;
  as_of: string;
  starting_equity?: number;
  precedence?: PaperEvidenceTag[];
  cutoffs?: { rewrite_utc: string; rewrite_cite: string; counters_utc: string; counters_cite: string };
  other_books?: Array<{ id: string; forward_start: string; source: string; cite: string }>;
  tag_counts?: {
    overall: Record<PaperEvidenceTag, number>;
    by_book: Record<string, Record<PaperEvidenceTag, number>>;
    rule_matches: Record<PaperEvidenceTag, number>;
  };
  books: PaperLedgerBook[];
  rows: PaperLedgerRow[];
  total_rows?: number;
  default_rows?: number;
};

/** Read-only: every paper_trades row with its evidence tag, plus per-book stats on the default view. */
export function fetchPaperLedgerView(signal?: AbortSignal) {
  return getJson<PaperLedgerView>("/api/paper/ledger-view", { signal });
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

export async function postDiagnosticsFrontendHeartbeat() {
  const res = await fetch(`${API_BASE}/api/diagnostics/frontend-heartbeat`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`frontend heartbeat failed: ${res.status}`);
  return res.json() as Promise<{ ok: boolean; seen_at: string }>;
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

export type StructureExperimentMetrics = {
  candidates: number;
  qualified: number;
  fills: number;
  skips: number;
  expirations: number;
  invalidations: number;
  incomplete_windows: number;
  net_expectancy_r: number | null;
  net_pnl_r: number | null;
  max_drawdown_r: number | null;
  profit_factor: number | null;
  win_rate: number | null;
  target_pct: number | null;
  stop_pct: number | null;
  timeout_pct: number | null;
  close_back_10_pct: number | null;
  close_back_10_denominator: number;
  post_entry_close_back_10_pct: number | null;
  post_entry_close_back_10_denominator: number;
  average_holding_bars: number | null;
  fees_r: number | null;
  funding_r: number | null;
  by_symbol?: Record<string, StructureExperimentMetrics>;
  by_call_type?: Record<string, StructureExperimentMetrics>;
  by_direction?: Record<string, StructureExperimentMetrics>;
};

export type StructureExperimentExample = {
  event_id: string;
  symbol: string;
  direction: string;
  call_type: string;
  variant: string;
  status: string;
  provisional_swing: string;
  pivot_at: string;
  confirmed_at: string;
  break_at: string;
  qualified_at: string | null;
  entry_at: string | null;
  invalidated_at: string | null;
  expired_at: string | null;
  broken_level: number;
  stop_price: number | null;
  target_price: number | null;
  entry_price: number | null;
  outcome: string | null;
  ambiguous: boolean;
  net_r: number | null;
  confirmation_delay_bars: number;
  bars_from_confirmation_to_break: number;
};

export type StructureExperimentReport = {
  status: "ready" | "unavailable";
  research_only: boolean;
  validation_status: string;
  reason?: string;
  assumptions?: Record<string, unknown>;
  variants?: Array<{
    id: string;
    event_count: number;
    boundary_excluded: number;
    median_bars_confirmation_to_break: number | null;
    windows: Record<string, StructureExperimentMetrics>;
  }>;
  examples?: StructureExperimentExample[];
  legacy_reference?: { label: string; not_a_variant: boolean };
};

export function fetchOhlcvStructureExperiment() {
  return getJson<StructureExperimentReport>("/api/research/ohlcv-structure-experiment");
}

export type StructureSwingRef = {
  swing_id: string;
  price: number;
  pivot_time: string | null;
  confirmation_open_time: string | null;
  known_at: string | null;
};

export type StructureLabelSnapshot = {
  symbol: string;
  rule_version: string;
  experimental: boolean;
  process_id?: number;
  as_of: string | null;
  trade_connected: boolean;
  baseline_notes: string[];
  timeframes: Record<
    string,
    {
      timeframe: string;
      dataset_fingerprint: string;
      range_explanation: string;
      coverage: {
        usable_bars: number;
        start_open: string | null;
        end_open: string | null;
        end_known_at: string | null;
        calculation_bars?: number;
        calculation_start_open?: string | null;
        gap_count: number;
        warmup_bars: number;
        scored_bars: number;
      };
      counts: Record<string, number>;
      denominator: number;
      latest: {
        state: string;
        reason: string;
        explanation: string | null;
        known_at: string | null;
        state_age: number;
        bars_since_break: number | null;
        ambiguity_flags: string[];
        initialization: string;
        data_quality: string;
        expiry_status: string;
        designated_age_bars: number | null;
        reversal_evidence: Record<string, string> | null;
        designated_level: StructureSwingRef | null;
        supporting_swing: StructureSwingRef | null;
        resisting_swing: StructureSwingRef | null;
      } | null;
    }
  >;
};

export function fetchStructureLabels(symbol: string, ruleVersion: string, signal?: AbortSignal) {
  const q = new URLSearchParams({ symbol, rule_version: ruleVersion });
  return getJson<StructureLabelSnapshot>(`/api/research/structure-labels?${q.toString()}`, { signal });
}
