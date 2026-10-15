/** Regime Lab API: regime-routed strategy backtests and walk-forward runs. */

export type Tier = "large" | "mid" | "small";
export type Mode = "backtest" | "walk_forward";

export type RunRequest = {
  mode: Mode;
  tier: Tier;
  timeframe: "1h" | "4h" | "1d";
  symbols?: string[] | null;
  start: string;
  end?: string | null;
  include_holdout: boolean;
  strategies: string[];
  initial_equity: number;
  risk_pct?: number | null;
  stop_atr_mult?: number | null;
  slippage?: number | null;
  fee_rate: number;
  max_leverage: number;
  btc_gate: boolean;
  allow_short?: boolean | null;
  train_months: number;
  test_months: number;
  safeguard_preset: "off" | "recommended";
  safeguards: Record<string, number | boolean | string>;
  params?: Record<string, number | boolean | string>;
  symbol_slippage?: Record<string, number>;
  symbol_timeframes?: Record<string, "1h" | "4h" | "1d">;
};

export type SafeguardValues = Record<string, number | boolean | string>;

export type TierConfig = {
  name: Tier;
  label: string;
  stop_atr_mult: number;
  slippage: number;
  risk_pct: number;
  strategies: string[];
  allow_short: boolean;
  stop_grid: number[];
  rank_min: number;
  rank_max: number;
};

export type LabConfig = {
  regimes: Record<string, string>;
  routing: Record<string, Record<string, string[]>>;
  strategies: { id: string; label: string }[];
  tiers: Record<Tier, TierConfig>;
  timeframes: string[];
  defaults: {
    initial_equity: number;
    fee_rate: number;
    max_leverage: number;
    btc_gate: boolean;
    train_months: number;
    test_months: number;
    params: Record<string, number | boolean | string>;
  };
  regime_methods: Record<string, string>;
  safeguards: { off: SafeguardValues; recommended: SafeguardValues };
  holdout_start: string;
  walk_forward_grid_size: number;
  not_modeled: string[];
};

export type Summary = {
  start_equity: number;
  end_equity: number;
  net_pnl: number;
  return_pct: number;
  cagr_pct: number | null;
  max_drawdown_pct: number;
  max_drawdown_usd: number;
  max_losing_streak: number;
  quick_stops: number;
  pnl_ex_top5: number;
  live_pause_drawdown_pct: number;
  sharpe: number | null;
  trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  avg_r: number | null;
  fees: number;
  slippage: number;
  exposure_pct: number;
  days: number;
  pnl_per_30d: number | null;
};

export type GroupRow = {
  key: string;
  trades: number;
  net_pnl: number;
  win_rate: number;
  avg_r: number;
  profit_factor: number | null;
  fees: number;
};

export type Trade = {
  symbol: string;
  strategy: string;
  side: string;
  regime: string;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  qty: number;
  notional: number;
  gross_pnl: number;
  fees: number;
  slippage_cost: number;
  net_pnl: number;
  r_multiple: number;
  bars_held: number;
  exit_reason: string;
};

export type Fold = {
  fold: number;
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  params: Record<string, number> | string;
  train_score: number | null;
  train_return_pct: number | null;
  train_trades: number | null;
  test_return_pct: number;
  test_max_drawdown_pct: number;
  test_trades: number;
  test_net_pnl: number;
  test_win_rate: number | null;
};

export type MonteCarlo = {
  runs: number;
  trades: number;
  shuffle_max_dd_pct: { p50: number; p95: number; p99: number };
  bootstrap_return_pct: { p5: number; p50: number; p95: number };
  bootstrap_max_dd_pct: { p50: number; p95: number };
  prob_loss: number;
};

export type RunResult = {
  summary: Summary;
  counters: Record<string, number>;
  monte_carlo: MonteCarlo | null;
  kill_switch_at: string | null;
  selection?: string;
  breakdowns: Record<string, GroupRow[]>;
  regime_share?: Record<string, Record<string, number>>;
  equity_curve: { time: number; equity: number }[];
  trades: Trade[];
  trade_count: number;
  folds?: Fold[];
  positive_folds?: number;
  universe: {
    symbols: string[];
    ranking: { rank: number; symbol: string; dollar_volume: number; days: number }[];
    coverage: { symbol: string; bars: number; first_regime_bar?: string | null; late_start?: boolean; note?: string }[];
  };
  window: { start: string; end: string; holdout_start: string };
  effective: {
    risk_pct: number;
    stop_atr_mult: number;
    slippage: number;
    fee_rate: number;
    allow_short: boolean;
    strategies: string[];
    btc_gate: boolean;
    safeguards: SafeguardValues;
  };
};

export type JobRow = {
  job_id: string;
  status: Job["status"];
  progress: number;
  stage: string;
  request: RunRequest;
  created_at: number;
  error: string | null;
  summary: Summary | null;
};

export type Job = {
  job_id: string;
  status: "running" | "done" | "error";
  progress: number;
  stage: string;
  request: RunRequest;
  result: RunResult | null;
  error: string | null;
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const BASE = "/api/research/regime-lab";

export function fetchLabConfig(): Promise<LabConfig> {
  return fetch(`${BASE}/config`).then((r) => json<LabConfig>(r));
}

export function startRun(req: RunRequest): Promise<{ job_id: string }> {
  return fetch(`${BASE}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  }).then((r) => json<{ job_id: string }>(r));
}

export function fetchJob(jobId: string): Promise<Job> {
  return fetch(`${BASE}/jobs/${jobId}`).then((r) => json<Job>(r));
}

export function fetchJobs(): Promise<{ jobs: JobRow[] }> {
  return fetch(`${BASE}/jobs`).then((r) => json<{ jobs: JobRow[] }>(r));
}
