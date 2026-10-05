export type DataStatus =
  | "LIVE"
  | "HISTORICAL"
  | "CACHED"
  | "STALE"
  | "UNAVAILABLE"
  | "WAITING";

export interface FreshValue<T = number | string | null> {
  value: T | null;
  timestamp: string | null;
  source: string;
  status: DataStatus;
  methodology?: string | null;
}

export interface ScreenerRow {
  rank: number | null;
  screener_rank?: number | null;
  market_rank?: FreshValue<number>;
  symbol: string;
  base_asset: string;
  quote_asset: string;
  exchange: string;
  market_type: string;
  price: FreshValue<number>;
  change_24h_pct: FreshValue<number>;
  volume_24h: FreshValue<number>;
  quote_volume_24h: FreshValue<number>;
  volume_change_pct?: FreshValue<number>;
  volume_change_1h?: FreshValue<number>;
  volume_change_4h?: FreshValue<number>;
  volume_change_24h?: FreshValue<number>;
  volume_change_7d?: FreshValue<number>;
  performance_1d?: FreshValue<number>;
  performance_7d?: FreshValue<number>;
  performance_30d?: FreshValue<number>;
  performance_90d?: FreshValue<number>;
  performance_180d?: FreshValue<number>;
  performance_1y?: FreshValue<number>;
  performance_ytd?: FreshValue<number>;
  high_24h: FreshValue<number>;
  low_24h: FreshValue<number>;
  funding_rate: FreshValue<number>;
  open_interest: FreshValue<number>;
  oi_change_pct: FreshValue<number>;
  oi_change_24h?: FreshValue<number>;
  market_cap: FreshValue<number>;
  fdv: FreshValue<number>;
  circulating_supply?: FreshValue<number>;
  total_supply?: FreshValue<number>;
  max_supply?: FreshValue<number>;
  category?: FreshValue<string>;
  tvl: FreshValue<number>;
  volume_mcap?: FreshValue<number>;
  mcap_fdv?: FreshValue<number>;
  nvt?: FreshValue<number>;
  velocity?: FreshValue<number>;
  williams_r?: FreshValue<number>;
  relative_volume: FreshValue<number>;
  volatility?: FreshValue<number>;
  structure: FreshValue<string>;
  market_structure?: FreshValue<string>;
  bos?: FreshValue<string>;
  choch?: FreshValue<string>;
  nearest_supply_zone?: FreshValue<string>;
  nearest_demand_zone?: FreshValue<string>;
  zone: FreshValue<string>;
  liquidation: FreshValue<string>;
  long_liquidations?: FreshValue<number>;
  short_liquidations?: FreshValue<number>;
  liquidation_status?: DataStatus;
  technical_state: FreshValue<string>;
  entry_exit_state?: FreshValue<string>;
  tech_rating?: FreshValue<string>;
  setup_trend?: FreshValue<string>;
  setup_bos?: FreshValue<string>;
  setup_impulse?: FreshValue<string>;
  setup_pullback?: FreshValue<string>;
  setup_entry?: FreshValue<number>;
  setup_sl?: FreshValue<number>;
  setup_tp1?: FreshValue<number>;
  setup_rr?: FreshValue<number>;
  setup_signal?: FreshValue<string>;
  market_signal?: FreshValue<string>;
  confirmation_strength?: FreshValue<string>;
  social_dominance?: FreshValue<number>;
  data_status?: DataStatus;
  updated_at?: string | null;
  /** Presentation-only screening rank — not a strategy/profit score */
  screen_priority_score?: number | null;
  screen_priority_reason?: string | null;
  /** Presentation metadata — discovery vs COMBO_02 v1 (never Telegram-eligible) */
  screen_timeframe?: string | null;
  local_trend?: string | null;
  screen_signal?: string | null;
  screen_setup?: string | null;
  potential_levels?: PotentialLevels | null;
  v1_status?: V1Status | null;
  v1_paper_trade?: V1PaperTradeHint | null;
  is_telegram_eligible?: boolean;
}

export interface PotentialLevels {
  entry: number | null;
  stop: number | null;
  tp1: number | null;
  rr: number | null;
  is_confirmed: boolean;
  reference_only?: boolean;
  display_mode?: "candidate" | "potential";
  tooltip?: string;
}

export interface V1Status {
  in_v1_universe: boolean;
  status: string;
  label?: string;
  tier?: string | null;
  risk_percent?: number | null;
  timeframe?: string | null;
  trend_1h?: string | null;
  trend_4h?: string | null;
  htf_alignment?: string | null;
  bos_status?: string | null;
  open_trade_id?: string | null;
  last_evaluated_at_utc?: string | null;
  tooltip?: string;
  eval_status?: string | null;
  tip_bar?: string | null;
}

export interface V1PaperTradeHint {
  open: boolean;
  trade_id?: string | null;
  label?: string;
  href?: string;
}

export interface ScreenerIdentity {
  title: string;
  subtitle: string;
  screen_setup_timeframe: string;
  v1_execution: string;
  is_telegram_eligible: boolean;
  legend?: Record<string, string>;
}

export interface V1WatcherViewRow {
  symbol: string;
  tier?: string | null;
  risk_percent?: number | null;
  risk_label?: string | null;
  last_closed_1h_bar?: string | null;
  trend_1h?: string | null;
  trend_4h?: string | null;
  htf_alignment?: string | null;
  bos_status?: string | null;
  watcher_status?: string | null;
  watcher_status_code?: string | null;
  open_trade_id?: string | null;
  paper_label?: string | null;
  last_evaluated_at_utc?: string | null;
  timeframe?: string;
  path?: string;
  combo_id?: string;
  read_only?: boolean;
  creates_orders?: boolean;
}

export type ScreenSize = 25 | 50 | 100;
export type ScreenFilter =
  | "ALL_ELIGIBLE"
  | "SETUPS"
  | "ENTRY_READY"
  | "BUY_BIAS"
  | "SELL_BIAS"
  | "WAITING"
  | "CONFLICT";

export interface ScreenerMeta {
  total_universe: number;
  discovered_universe?: number;
  active_universe?: number;
  active_universe_cap?: number;
  eligible_count: number;
  returned_count: number;
  limit: number;
  selection_updated_at?: string | null;
  excluded?: Record<string, number>;
  search_mode?: boolean;
  screen_filter?: string | null;
  screen_timeframe?: string | null;
  screener_identity?: ScreenerIdentity | null;
  v1_watcher_view?: V1WatcherViewRow[] | null;
}

export interface ScreenerPreset {
  id: string;
  label: string;
  description: string;
  filters: Array<{ field: string; operator: string; value: unknown }>;
}

export interface HealthResponse {
  status: string;
  use_real_data: boolean;
  redis: string;
  database: string;
  ingestion: string;
  symbols_loaded: number;
  discovered_symbols?: number;
  active_universe?: number;
  active_universe_cap?: number;
  tickers_live: number;
  timestamp: string;
}

export interface SystemStats {
  symbols: number;
  ticker_live: number;
  kline_live: number;
  websocket_connections: number;
  active_streams: number;
  rest_requests_last_minute: number;
  rate_limit_errors: number;
  database: string;
  redis: string;
}
