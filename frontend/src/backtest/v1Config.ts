/** COMBO_02 v1 Strategy Backtest configuration helpers (UI-only). */

export const V1_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;
export const V1_SETUP_TF = "1h";
export const SHORT_STATUS = "PAUSED" as const;

/**
 * Coins the Backtest screen may select. These mirror LARGE and MID in
 * backend/app/research/passed_cap_book.py (same symbols, same order) and must
 * not diverge. No API serves the lists, so a parity test reads the Python file.
 */
export const BACKTEST_LARGE_SYMBOLS = [
  "BTCUSDT",
  "ETHUSDT",
  "BNBUSDT",
  "SOLUSDT",
  "XRPUSDT",
  "DOGEUSDT",
  "ADAUSDT",
  "TRXUSDT",
  "AVAXUSDT",
  "LINKUSDT",
  "DOTUSDT",
  "BCHUSDT",
  "LTCUSDT",
  "TONUSDT",
] as const;

export const BACKTEST_MID_SYMBOLS = [
  "NEARUSDT",
  "ATOMUSDT",
  "APTUSDT",
  "ARBUSDT",
  "OPUSDT",
  "SUIUSDT",
  "INJUSDT",
  "AAVEUSDT",
  "UNIUSDT",
  "FILUSDT",
  "ETCUSDT",
  "ICPUSDT",
  "RENDERUSDT",
] as const;

/** Large caps first, then mid caps. */
export const BACKTEST_CAP_SYMBOLS = [...BACKTEST_LARGE_SYMBOLS, ...BACKTEST_MID_SYMBOLS] as const;

/**
 * True only for a symbol listed in LARGE or MID. The backend cap_group() treats
 * every symbol outside LARGE as mid; that fallback is deliberately not copied.
 */
export function isBacktestCapSymbol(symbol: string): boolean {
  return (BACKTEST_CAP_SYMBOLS as readonly string[]).includes(String(symbol ?? "").trim().toUpperCase());
}

/** Splits symbols into cap-book and other. Keeps input order, upper-cases, drops repeats. */
export function filterBacktestCapSymbols(symbols: readonly string[]): {
  allowed: string[];
  rejected: string[];
} {
  const allowed: string[] = [];
  const rejected: string[] = [];
  const seen = new Set<string>();
  for (const raw of symbols) {
    const sym = String(raw ?? "").trim().toUpperCase();
    if (!sym || seen.has(sym)) continue;
    seen.add(sym);
    (isBacktestCapSymbol(sym) ? allowed : rejected).push(sym);
  }
  return { allowed, rejected };
}

/** Backtest strategy selector ids (default remains COMBO_02_V1). */
export type BacktestStrategyId =
  | "COMBO_02_V1"
  | "COMBO_02_V2"
  | "COMBO_02_V2_1B"
  | "COMBO_02_V2_1_A"
  | "COMBO_02"
  | "COMBO_02_CLOSED_HTF"
  | "COMBO_03_TRANSITION"
  | "COMBO_03_TRANSITION_B"
  | "COMBO_01"
  | "COMBO_02_LOCAL"
  | "COMBO_SD_STRUCTURE"
  | "CANDLE12_V2"
  | "CANDLE12_V3_EXEC_FIX"
  | "LARGE_CAP_SWEEP_CHOCH"
  | "SMALL_CAP_VOLUME_BOS"
  | "OSE_A_corrected_baseline"
  | "OSE_B_acceptance"
  | "OSE_D_faster_confirmation";

/** Supply/demand structure variants. C is the starting hypothesis. */
export const SD_VARIANT_OPTIONS: ReadonlyArray<{ id: string; label: string }> = [
  { id: "COMBO_SD_A", label: "COMBO_SD_A · reversal · zone + CHoCH" },
  { id: "COMBO_SD_B", label: "COMBO_SD_B · reversal · zone + CHoCH + BOS" },
  { id: "COMBO_SD_C", label: "COMBO_SD_C · reversal · B + same-slot RVOL 1.5" },
  { id: "COMBO_SD_C12", label: "COMBO_SD_C12 · reversal · B + same-slot RVOL 1.2" },
  { id: "COMBO_SD_C20", label: "COMBO_SD_C20 · reversal · B + same-slot RVOL 2.0" },
  { id: "COMBO_SD_D", label: "COMBO_SD_D · continuation · RVOL 1.5 on the break, then retest" },
  { id: "COMBO_SD_E", label: "COMBO_SD_E · reversal · zone + CHoCH + swing divergence" },
  { id: "COMBO_SD_F", label: "COMBO_SD_F · continuation · zone + BOS + wider range + slot RVOL 1.5" },
  { id: "COMBO_SD_C_FRESH", label: "COMBO_SD_C_FRESH · reversal · fresh zone + slot RVOL 1.5" },
  { id: "COMBO_SD_LIQ", label: "COMBO_SD_LIQ · reversal · EQH/EQL sweep + zone + CHoCH + BOS" },
] as const;

export const SD_DEFAULT_VARIANT = "COMBO_SD_C";

export const BACKTEST_STRATEGY_OPTIONS: ReadonlyArray<{
  id: BacktestStrategyId;
  label: string;
  status: "Production candidate" | "Research";
  combinationId: string;
  comboVersion: string;
  /** Sends this combination as-is. SHORT is not rewritten to COMBO_04_HTF. */
  direct?: boolean;
  /** Runs its own engine (not the COMBO walk). Setup timeframes it accepts. */
  engineTimeframes?: readonly string[];
}> = [
  {
    id: "COMBO_02_V1",
    label: "COMBO_02 V1",
    status: "Production candidate",
    combinationId: "COMBO_02",
    comboVersion: "v1",
  },
  {
    id: "COMBO_02_V2",
    // The backend runs the COMBO_02_V2 family with the direction filter off.
    label: "COMBO_02_V2 · 1h LONG+SHORT",
    status: "Research",
    combinationId: "COMBO_02_V2",
    comboVersion: "v2-combo02-adaptive-3-regime",
    direct: true,
  },
  {
    id: "COMBO_02_V2_1B",
    label: "COMBO_02_V2_1B · 1h LONG+SHORT",
    status: "Research",
    combinationId: "COMBO_02_V2_1B",
    comboVersion: "v2.1-b-choppy-15m",
    direct: true,
  },
  {
    id: "COMBO_02_V2_1_A",
    label: "COMBO_02 V2.1-A",
    status: "Research",
    combinationId: "COMBO_02_V2_1_A",
    comboVersion: "v2.1-a-choppy-wait",
  },
  {
    id: "COMBO_02",
    label: "COMBO_02 · 1h SHORT",
    status: "Research",
    combinationId: "COMBO_02",
    comboVersion: "v1",
    direct: true,
  },
  {
    id: "COMBO_02_CLOSED_HTF",
    label: "COMBO_02_CLOSED_HTF · 1h SHORT",
    status: "Research",
    combinationId: "COMBO_02_CLOSED_HTF",
    comboVersion: "v1-combo02-long-htf-closed",
    direct: true,
  },
  {
    id: "COMBO_03_TRANSITION",
    label: "COMBO_03_TRANSITION · 1h SHORT",
    status: "Research",
    combinationId: "COMBO_03_TRANSITION",
    comboVersion: "combo03-transition",
    direct: true,
  },
  {
    id: "COMBO_03_TRANSITION_B",
    label: "COMBO_03_TRANSITION_B · 1h",
    status: "Research",
    combinationId: "COMBO_03_TRANSITION_B",
    comboVersion: "combo03-transition-b",
    direct: true,
  },
  {
    id: "COMBO_01",
    label: "COMBO_01 · 1h SHORT",
    status: "Research",
    combinationId: "COMBO_01",
    comboVersion: "combo01-bos",
    direct: true,
  },
  {
    id: "COMBO_02_LOCAL",
    label: "COMBO_02_LOCAL · 1h SHORT",
    status: "Research",
    combinationId: "COMBO_02_LOCAL",
    comboVersion: "combo02-local",
    direct: true,
  },
  {
    id: "COMBO_SD_STRUCTURE",
    label: "SD + CHoCH + BOS",
    status: "Research",
    combinationId: SD_DEFAULT_VARIANT,
    comboVersion: "sd-structure",
  },
  // Ranked strategies with their own engine (see backend engine_backtest.py).
  {
    id: "CANDLE12_V2",
    label: "CANDLE12_V2 · 1h LONG",
    status: "Research",
    combinationId: "CANDLE12_V2",
    comboVersion: "engine-candle12",
    direct: true,
    engineTimeframes: ["5m", "15m", "1h"],
  },
  {
    id: "CANDLE12_V3_EXEC_FIX",
    label: "CANDLE12_V3_EXEC_FIX · 1h LONG",
    status: "Research",
    combinationId: "CANDLE12_V3_EXEC_FIX",
    comboVersion: "engine-candle12-v3",
    direct: true,
    engineTimeframes: ["5m", "15m", "1h"],
  },
  {
    id: "LARGE_CAP_SWEEP_CHOCH",
    label: "LARGE_CAP_SWEEP_CHOCH · 1h LONG",
    status: "Research",
    combinationId: "LARGE_CAP_SWEEP_CHOCH",
    comboVersion: "engine-multicap",
    direct: true,
    engineTimeframes: ["5m", "15m", "1h"],
  },
  {
    id: "SMALL_CAP_VOLUME_BOS",
    label: "SMALL_CAP_VOLUME_BOS · 1h LONG",
    status: "Research",
    combinationId: "SMALL_CAP_VOLUME_BOS",
    comboVersion: "engine-multicap",
    direct: true,
    engineTimeframes: ["5m", "15m", "1h"],
  },
  {
    id: "OSE_A_corrected_baseline",
    label: "OSE_A corrected baseline · 1h LONG",
    status: "Research",
    combinationId: "OSE_A_corrected_baseline",
    comboVersion: "engine-ose",
    direct: true,
    engineTimeframes: ["15m", "1h"],
  },
  {
    id: "OSE_B_acceptance",
    label: "OSE_B acceptance · 1h LONG",
    status: "Research",
    combinationId: "OSE_B_acceptance",
    comboVersion: "engine-ose",
    direct: true,
    engineTimeframes: ["15m", "1h"],
  },
  {
    id: "OSE_D_faster_confirmation",
    label: "OSE_D faster confirmation · 1h LONG",
    status: "Research",
    combinationId: "OSE_D_faster_confirmation",
    comboVersion: "engine-ose",
    direct: true,
    engineTimeframes: ["15m", "1h"],
  },
] as const;

/** Cap-book sides that passed. The backtest screen selects these on open. */
export const PASSED_STRATEGY_IDS: readonly BacktestStrategyId[] = [
  "COMBO_02_V2",
  "COMBO_02_V2_1B",
  "COMBO_02_V2_1_A",
  "COMBO_02",
  "COMBO_02_CLOSED_HTF",
  "COMBO_03_TRANSITION",
  "COMBO_03_TRANSITION_B",
  "COMBO_01",
  "COMBO_02_LOCAL",
] as const;

/** Highest passed short. Used when the short book is selected together. */
export const PASSED_RUN_STRATEGY_ID: BacktestStrategyId = "COMBO_02_V2";

export type TradeBook = "long" | "short" | "hedge" | "grid";

/** Passed long side, plus the long research menus that are not short cap-book rows. */
export const LONG_STRATEGY_IDS: readonly BacktestStrategyId[] = [
  "COMBO_03_TRANSITION_B",
  "COMBO_02_V1",
  // Long sides on the Strategies ranked board (net positive, same engine).
  // COMBO_02_V2 and COMBO_02_V2_1B run LONG and SHORT together: hedge book.
  "COMBO_01",
  "CANDLE12_V2",
  "LARGE_CAP_SWEEP_CHOCH",
  "SMALL_CAP_VOLUME_BOS",
  "OSE_A_corrected_baseline",
  "OSE_B_acceptance",
  "OSE_D_faster_confirmation",
  "COMBO_SD_STRUCTURE",
] as const;

export const LONG_PASSED_IDS: readonly BacktestStrategyId[] = ["COMBO_03_TRANSITION_B"] as const;

export const LONG_RUN_STRATEGY_ID: BacktestStrategyId = "COMBO_03_TRANSITION_B";

/**
 * Passed shorts. The COMBO_02_V2 family (V2, V2_1B, V2.1-A) is the hedge book because
 * the backend runs it with the direction filter off, so one run keeps both directions.
 */
export const SHORT_STRATEGY_IDS: readonly BacktestStrategyId[] = [
  "COMBO_02",
  "COMBO_02_CLOSED_HTF",
  "COMBO_03_TRANSITION",
  "COMBO_03_TRANSITION_B",
  "COMBO_01",
  "COMBO_02_LOCAL",
] as const;

export const SHORT_PASSED_IDS: readonly BacktestStrategyId[] = [
  "COMBO_02",
  "COMBO_02_CLOSED_HTF",
  "COMBO_03_TRANSITION",
  "COMBO_03_TRANSITION_B",
  "COMBO_01",
  "COMBO_02_LOCAL",
] as const;

/** Native LONG+SHORT runs. V2.1-A first: it is the hedge book's passed run. */
export const HEDGE_STRATEGY_IDS: readonly BacktestStrategyId[] = [
  "COMBO_02_V2_1_A",
  "COMBO_02_V2",
  "COMBO_02_V2_1B",
] as const;

/** Only V2.1-A passed as a two-sided run; V2 and V2_1B passed as one-direction slices. */
export const HEDGE_PASSED_IDS: readonly BacktestStrategyId[] = ["COMBO_02_V2_1_A"] as const;

export function strategyIdsForBook(book: TradeBook): readonly BacktestStrategyId[] {
  if (book === "long") return LONG_STRATEGY_IDS;
  if (book === "hedge") return HEDGE_STRATEGY_IDS;
  if (book === "short") return SHORT_STRATEGY_IDS;
  return [];
}

export function passedIdsForBook(book: TradeBook): readonly BacktestStrategyId[] {
  if (book === "long") return LONG_PASSED_IDS;
  if (book === "hedge") return HEDGE_PASSED_IDS;
  if (book === "short") return SHORT_PASSED_IDS;
  return [];
}

export function bookChipLabel(id: BacktestStrategyId, book: TradeBook): string {
  const base = backtestStrategyLabel(id);
  if (id === "COMBO_03_TRANSITION_B" && book === "long") return `${base} LONG`;
  if (id === "COMBO_03_TRANSITION_B" && book === "short") return `${base} SHORT`;
  // The option labels name the short side; the long book runs the same id LONG.
  if (book === "long") return base.replace(/ SHORT$/, " LONG");
  return base;
}

export function backtestStrategyLabel(id: BacktestStrategyId): string {
  return BACKTEST_STRATEGY_OPTIONS.find((o) => o.id === id)?.label ?? id;
}

export type V1SymbolRole = "CORE" | "SECONDARY" | "RESEARCH" | "NON_V1";

export const V1_PRODUCTION_RISK: Record<
  (typeof V1_SYMBOLS)[number],
  { role: "core" | "secondary"; riskPercent: number; riskUsdAt1k: number }
> = {
  BTCUSDT: { role: "core", riskPercent: 2, riskUsdAt1k: 20 },
  ETHUSDT: { role: "secondary", riskPercent: 2, riskUsdAt1k: 20 },
  SOLUSDT: { role: "secondary", riskPercent: 2, riskUsdAt1k: 20 },
};

const TF_MINUTES: Record<string, number> = {
  "15m": 15,
  "1h": 60,
  "4h": 240,
  "5m": 5,
  "1d": 1440,
};

/** UTC calendar window ending today. Does not change how a backtest is computed. */
export function lastNDaysUtc(days: number, now = new Date()): { start: string; end: string } {
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}

export function barsToApproxDays(bars: number, timeframe: string): number | null {
  const mins = TF_MINUTES[timeframe];
  if (mins == null || !Number.isFinite(bars) || bars <= 0) return null;
  return Math.round(((bars * mins) / (60 * 24)) * 10) / 10;
}

export function formatBarsDuration(bars: number, timeframe: string): string {
  const days = barsToApproxDays(bars, timeframe);
  if (days == null) return "—";
  const n = Number.isInteger(days) ? String(days) : days.toFixed(1);
  return `${bars.toLocaleString()} bars ≈ ${n} days`;
}

export function timeframeRoleLabel(tf: string): string {
  if (tf === "15m") return "research-only";
  if (tf === "1h") return "v1 setup timeframe";
  if (tf === "4h") return "HTF context";
  return "research-only";
}

export function symbolRole(symbol: string, timeframe = V1_SETUP_TF): V1SymbolRole {
  const sym = symbol.toUpperCase();
  if (frozenBook(sym, timeframe) === "core") return "CORE";
  if (frozenBook(sym, timeframe) === "secondary") return "SECONDARY";
  if (frozenBook(sym, timeframe) === "research") return "RESEARCH";
  if ((V1_SYMBOLS as readonly string[]).includes(sym)) return "RESEARCH";
  return "NON_V1";
}

function frozenBook(
  symbol: string,
  timeframe: string
): "core" | "secondary" | "research" | null {
  const key = `${symbol}|${timeframe}`;
  const map: Record<string, "core" | "secondary" | "research"> = {
    "BTCUSDT|1h": "core",
    "ETHUSDT|1h": "secondary",
    "SOLUSDT|1h": "secondary",
    "BTCUSDT|4h": "secondary",
    "ETHUSDT|4h": "secondary",
    "BTCUSDT|15m": "research",
    "ETHUSDT|15m": "research",
    "SOLUSDT|15m": "research",
    "SOLUSDT|4h": "research",
  };
  return map[key] ?? null;
}

export function symbolRoleDisplay(symbol: string, timeframe = V1_SETUP_TF): string {
  const role = symbolRole(symbol, timeframe);
  if (role === "CORE") return `${symbol} — v1 CORE`;
  if (role === "SECONDARY") return `${symbol} — v1 SECONDARY`;
  if (role === "RESEARCH") return `${symbol} — research-only`;
  return `${symbol} — non-v1`;
}

export function isFrozenV1Symbol(symbol: string): boolean {
  return (V1_SYMBOLS as readonly string[]).includes(symbol.toUpperCase());
}

export function v1RiskForSymbol(
  symbol: string,
  principalUsd: number
): { riskPercent: number; riskUsd: number; role: string } | null {
  const row = V1_PRODUCTION_RISK[symbol as keyof typeof V1_PRODUCTION_RISK];
  if (!row) return null;
  return {
    riskPercent: row.riskPercent,
    riskUsd: Math.max(1, Math.round((principalUsd * row.riskPercent) / 100)),
    role: row.role,
  };
}

/** True when every selected symbol is a frozen v1 book symbol. */
export function allFrozenV1Symbols(symbols: string[]): boolean {
  return symbols.length > 0 && symbols.every((s) => isFrozenV1Symbol(s));
}

export type ConfigMismatchReason =
  | "risk_mismatch"
  | "direction_mismatch"
  | "setup_timeframe_mismatch"
  | "htf_mismatch"
  | "non_v1_symbol"
  | "non_v1_strategy"
  | "custom_execution_model"
  | "custom_fee_model"
  | "custom_leverage";

export function assessProductionComparable(input: {
  symbols: string[];
  timeframes: string[];
  direction: "LONG" | "SHORT";
  researchRiskOverride: boolean;
  leverage: number;
  takerFeePct: number;
  makerFeePct: number;
  strategyId?: BacktestStrategyId;
}): { productionComparable: boolean; reasons: ConfigMismatchReason[] } {
  const reasons: ConfigMismatchReason[] = [];
  if (input.strategyId && input.strategyId !== "COMBO_02_V1") {
    reasons.push("non_v1_strategy");
  }
  if (input.direction !== "LONG") reasons.push("direction_mismatch");
  if (input.researchRiskOverride) reasons.push("risk_mismatch");
  if (input.timeframes.some((tf) => tf !== V1_SETUP_TF)) {
    reasons.push("setup_timeframe_mismatch");
  }
  if (input.symbols.some((s) => !isFrozenV1Symbol(s))) {
    reasons.push("non_v1_symbol");
  }
  if (Math.abs(input.leverage - 2) > 1e-9) reasons.push("custom_leverage");
  if (Math.abs(input.takerFeePct - 0.04) > 1e-9) reasons.push("custom_fee_model");
  if (Math.abs(input.makerFeePct - 0.02) > 1e-9) reasons.push("custom_fee_model");
  return {
    productionComparable: reasons.length === 0 && input.symbols.length > 0,
    reasons: [...new Set(reasons)],
  };
}

export const FEE_DISPLAY = {
  entryType: "MARKET or LIMIT_RETEST",
  entryFeeType: "Market entry → taker; Limit retest entry → maker",
  exitType: "MARKET",
  exitFeeType: "Market exit → taker",
  feeBasis: "executed notional",
  feeConvention: "negative cost",
} as const;

export function lookbackLabelForTf(bars: number, timeframe: string): string {
  const days = barsToApproxDays(bars, timeframe);
  if (days == null) return `${bars.toLocaleString()} bars`;
  const n = Number.isInteger(days) ? String(Math.round(days)) : days.toFixed(1);
  return `≈ ${n} days at ${timeframe}`;
}
