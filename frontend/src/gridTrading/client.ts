/**Grid research client. Kept out of the shared API client. */

export type GridCandle = {
  time: number | string;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type GridStatus = {
  enabled: boolean;
  market: string;
  mode: string;
  live: boolean;
  paper: boolean;
};

export type GridConfigInput = {
  symbol: string;
  timeframe: string;
  grid_type: "arithmetic" | "geometric";
  range_low: number;
  range_high: number;
  grid_count: number;
  capital: number;
  max_capital: number;
  fee_bps: number;
  taker_fee_bps?: number | null;
  slippage_bps: number;
  leverage?: number;
  stop_price: number;
  max_drawdown: number;
  max_open_orders: number;
  paused?: boolean;
  funding_mode?: "charge" | "exclude";
  intrabar_path: "worst" | "buy_then_sell" | "sell_then_buy";
};

export type GridFill = {
  time: number | string | null;
  side: string;
  slot: number | null;
  level: number;
  price: number;
  qty: number;
  fee: number;
  reason?: string | null;
};

export type GridBacktest = {
  summary: {
    market: string;
    accepted: boolean;
    error: string | null;
    halted: string | null;
    fill_count: number;
    touch_count: number;
    fill_rate: number;
    round_trips: number;
    max_drawdown: number;
    fees_paid?: number;
    realized_pnl: number;
    unrealized_pnl?: number;
    net_pnl?: number | null;
    pnl_before_funding?: number | null;
    funding_status?: "unavailable" | "excluded" | "charged" | "incomplete";
    funding_paid?: number;
    funding_coverage_pct?: number | null;
    pct_time_in_range?: number;
    avg_profit_per_grid_cycle?: number | null;
    events?: { type: string; time: number | string | null; price?: number; side?: string }[];
    warnings?: string[];
    funding_included?: boolean;
    liquidated_equity: number;
    return: number;
    buy_and_hold_return: number;
    excess_return: number;
    bar_count: number;
  };
  levels: number[];
  fills: GridFill[];
  equity_curve: { time: number | string | null; equity: number; drawdown: number }[];
  candles: GridCandle[];
  buy_and_hold: { equity: number; return: number };
  warnings?: string[];
  overlap?: boolean;
};

export type GridScanRow = {
  symbol: string;
  timeframe: string;
  score: number;
  eligible: boolean;
  reason: string | null;
  market: string;
  range_low?: number;
  range_high?: number;
  stop_price?: number;
  atr?: number | null;
  mid_crosses?: number;
  bars?: number;
  window_end?: number | string | null;
};

async function read<T>(res: Response): Promise<T> {
  const payload = (await res.json().catch(() => ({}))) as {
    detail?: { message?: string } | string;
  };
  if (!res.ok) {
    const detail = payload.detail;
    const message =
      typeof detail === "object" && detail?.message
        ? detail.message
        : typeof detail === "string"
          ? detail
          : `request failed (${res.status})`;
    throw new Error(message);
  }
  return payload as T;
}

export async function fetchGridStatus(): Promise<GridStatus | null> {
  const res = await fetch("/api/grid-trading/status");
  if (!res.ok) return null;
  return read<GridStatus>(res);
}

export function runGridBacktest(
  config: GridConfigInput,
  extra?: {
    candles?: GridCandle[];
    limit?: number;
    after_time?: number | string | null;
    allow_overlap?: boolean;
  },
): Promise<GridBacktest> {
  return fetch("/api/grid-trading/backtest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...config, ...extra }),
  }).then((res) => read<GridBacktest>(res));
}

export function runGridValidation(
  config: GridConfigInput,
  extra: {
    limit?: number;
    is_fraction: number;
    train_bars: number;
    test_bars: number;
    grid_counts: number[];
    after_time?: number | string | null;
    allow_overlap?: boolean;
  },
): Promise<Record<string, unknown>> {
  return fetch("/api/grid-trading/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...config, ...extra }),
  }).then((res) => read(res));
}

export function runGridScan(body: {
  timeframe: string;
  symbols: string[];
  limit: number;
  fee_bps: number;
  slippage_bps: number;
  grid_count: number;
}): Promise<{ market: string; rows: GridScanRow[] }> {
  return fetch("/api/grid-trading/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((res) => read(res));
}
