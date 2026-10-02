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
  eligible_count: number;
  returned_count: number;
  limit: number;
  selection_updated_at?: string | null;
  excluded?: Record<string, number>;
  search_mode?: boolean;
  screen_filter?: string | null;
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
