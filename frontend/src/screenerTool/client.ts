/** Owner D188: screener context (cost as R, HTF, BTC, badge) and the My Trades journal. Paper only. */

const API_BASE = import.meta.env.VITE_API_URL || "";

async function errorOf(path: string, res: Response): Promise<Error> {
  let detail = "";
  try {
    const text = await res.text();
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      detail = typeof body.detail === "string" ? body.detail : body.detail != null ? JSON.stringify(body.detail) : "";
    } catch {
      detail = text;
    }
  } catch {
    /* no body */
  }
  detail = detail.trim().slice(0, 300);
  return new Error(`API ${path} failed: ${res.status}${detail ? ` — ${detail}` : ""}`);
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) throw await errorOf(path, res);
  return res.json() as Promise<T>;
}

// --- screener context ------------------------------------------------------------------------
export type Arrow = "UP" | "DOWN" | "FLAT" | null;

export type HtfState = {
  timeframe: string;
  status: "OK" | "SHORT_HISTORY" | "NO_DATA" | string;
  bars: number;
  last_closed_bar: string | null;
  close_vs_ema50: Arrow;
  ema20_vs_ema50: Arrow;
  trend: "UP" | "DOWN" | "MIXED" | null;
  source?: string;
};

export type CostInfo = {
  status: string;
  slippage_bps: number;
  thin_coin: boolean;
  round_trip_pct: number;
  stop_distance_pct: number | null;
  cost_r: number | null;
  cost_r_max: number;
  cost_ok: boolean | null;
  cost_too_high: boolean | null;
};

export type ResearchBadge = {
  setup_type: string;
  setup_label: string;
  verdict: "NO_EDGE_FOUND" | "UNTESTED" | string;
  text: string;
  detail: string;
  not_a_signal: string;
};

export type ContextRow = {
  symbol: string;
  direction: string | null;
  cost: CostInfo;
  htf: Record<string, HtfState>;
  htf_agrees: boolean | null;
  badge: ResearchBadge;
};

export type BtcRegime = {
  trend: string | null;
  as_of_bar: string | null;
  bars: number;
  short_history: boolean | null;
  status: string;
};

export type BtcFunding = {
  status: "OK" | "NO_DATA" | string;
  text: string;
  mean_72h: number | null;
  state: string | null;
  latest_settlement: string | null;
  settlements: number;
  share_above_base: number | null;
  reason: string | null;
};

export type ScreenerContext = {
  read_only: boolean;
  as_of: string;
  rows: ContextRow[];
  btc: { regime: BtcRegime; funding: BtcFunding };
  rules: { cost: string; cost_r_max: number; thin_coins: string[]; htf: string; badge: string; not_a_signal: string };
};

export type ContextRequestRow = {
  symbol: string;
  direction: string | null;
  tier?: string;
  status?: string;
  entry_price: number | null;
  stop_price: number | null;
  setup_trend?: string | null;
  bos?: string | null;
  pullback?: string | null;
  retest?: string | null;
};

export function fetchScreenerContext(rows: ContextRequestRow[]) {
  return call<ScreenerContext>("/api/screener-tool/context", { method: "POST", body: JSON.stringify({ rows }) });
}

// --- My Trades ---------------------------------------------------------------------------------
export type ManualTrade = {
  row_id: number;
  trade_id: string;
  version: number;
  valid_from: string;
  action: "OPEN" | "EDIT" | "CLOSE" | "VOID";
  voided: boolean;
  status: "OPEN" | "CLOSED" | "VOID";
  symbol: string;
  side: "LONG" | "SHORT";
  timeframe: string | null;
  setup_tag: string | null;
  entry_price: number;
  stop_price: number;
  targets: number[];
  targets_r: number[];
  quantity: number;
  risk_usd: number;
  size_input: "quantity" | "risk_usd";
  opened_at: string;
  notes: string | null;
  screenshot_path: string | null;
  exit_price: number | null;
  exit_at: string | null;
  exit_reason: string | null;
  gross_pnl_usd: number | null;
  fees_usd: number | null;
  slippage_usd: number | null;
  costs_usd: number | null;
  net_pnl_usd: number | null;
  gross_r: number | null;
  r_multiple: number | null;
  stop_distance_pct: number | null;
  edit_note: string | null;
};

export type TradeStats = {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_r: number | null;
  expectancy_r: number | null;
  expectancy_usd: number | null;
  total_r: number;
  net_pnl_usd: number;
  profit_factor: number | null;
  profit_factor_r: number | null;
  max_drawdown_r: number;
  max_drawdown_usd: number;
  longest_losing_streak: number;
};

export type GroupStats = TradeStats & { key: string };

export type Honesty = {
  trades: number;
  min_trades: number;
  judgeable: boolean;
  text: string;
  expectancy_r: number | null;
  ci_low: number | null;
  ci_high: number | null;
  lower_bound_above_zero: boolean | null;
  resamples: number;
  seed: number;
  confidence: number;
};

export type MyTradesView = {
  trades: ManualTrade[];
  counts: { open: number; closed: number; void: number };
  stats: { overall: TradeStats; by_setup: GroupStats[]; by_coin: GroupStats[]; by_month: GroupStats[]; honesty: Honesty };
  cost_model: { name: string; taker_fee_rate: number; slippage_rate: number; thin_coins: string[]; formula: string };
  storage: { table: string; key: string[]; append_only_guard: boolean | null };
  disclaimer: string;
};

export type NewTrade = {
  symbol: string;
  side: "LONG" | "SHORT";
  entry_price: number;
  stop_price: number;
  targets: number[];
  quantity?: number;
  risk_usd?: number;
  setup_tag?: string;
  timeframe?: string;
  notes?: string;
  screenshot_path?: string;
};

export function fetchMyTrades() {
  return call<MyTradesView>("/api/my-trades");
}

export function logTrade(body: NewTrade) {
  return call<{ trade: ManualTrade }>("/api/my-trades", { method: "POST", body: JSON.stringify(body) });
}

export function closeTrade(tradeId: string, body: { exit_price: number; exit_at?: string; exit_reason?: string }) {
  return call<{ trade: ManualTrade }>(`/api/my-trades/${encodeURIComponent(tradeId)}/close`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function voidTrade(tradeId: string, reason: string) {
  return call<{ trade: ManualTrade }>(`/api/my-trades/${encodeURIComponent(tradeId)}/void`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function fetchPrice(symbol: string) {
  return call<{ symbol: string; price: number | null }>(`/api/my-trades/price?symbol=${encodeURIComponent(symbol)}`);
}

export function exportUrl(history: boolean): string {
  return `${API_BASE}/api/my-trades/export.csv${history ? "?history=1" : ""}`;
}
