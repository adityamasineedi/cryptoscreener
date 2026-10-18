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
  | "OSE_D_faster_confirmation"
  | "DAILY_TREND_01_SMA200"
  | "DAILY_TREND_02_EMA200"
  | "DAILY_TREND_03_SMA50"
  | "DAILY_TREND_04_MA_SLOPE"
  | "DAILY_TREND_05_GOLDEN_CROSS"
  | "DAILY_TREND_06_WEINSTEIN"
  | "DAILY_TREND_09_DONCHIAN20_WEINSTEIN"
  | "DAILY_TREND_11_ADX_DMI"
  | "DAILY_TREND_12_ICHIMOKU"
  | "DAILY_TREND_14_ROC90"
  | "DAILY_TREND_15_ROC30"
  | "DAILY_TREND_19_COMBINED"
  | "DAILY_TREND_ROTATION"
  | "DAILY_TREND_STATIC12"
  | "MTF_4H_16_BREAKOUT252";

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

/** Direction an engine strategy can be asked for. BOTH = one walk with long and short. */
export type EngineDirection = "LONG" | "SHORT" | "BOTH";

/** Daily trend books (backend family "daily_trend"). Menu order. */
export const DAILY_TREND_STRATEGY_IDS = [
  "DAILY_TREND_01_SMA200",
  "DAILY_TREND_02_EMA200",
  "DAILY_TREND_03_SMA50",
  "DAILY_TREND_04_MA_SLOPE",
  "DAILY_TREND_05_GOLDEN_CROSS",
  "DAILY_TREND_06_WEINSTEIN",
  "DAILY_TREND_09_DONCHIAN20_WEINSTEIN",
  "DAILY_TREND_11_ADX_DMI",
  "DAILY_TREND_12_ICHIMOKU",
  "DAILY_TREND_14_ROC90",
  "DAILY_TREND_15_ROC30",
  "DAILY_TREND_19_COMBINED",
  "DAILY_TREND_ROTATION",
  "DAILY_TREND_STATIC12",
] as const satisfies readonly BacktestStrategyId[];

export const DAILY_TREND_COMBO_VERSION = "engine-daily-trend";
export const DAILY_TREND_OPTGROUP_LABEL = "Daily trend (research, in-sample)";

/**
 * Last UTC day a daily trend book may load (inclusive end_date). Bars from
 * 2026-07-15 on are the sealed holdout and must never be requested.
 */
export const DAILY_TREND_LAST_DAY = "2026-07-14";

export const DAILY_TREND_HOLDOUT_BANNER =
  "Holdout sealed: data from 2026-07-15 is not loaded for daily trend books. Results 2022-10 → 2026-07-14 are in-sample (already read by the pre-registered screen) — for inspection, not confirmation. Funding not charged; fixed-$ risk sizing (the screen compounds 0.5%). Research only — not tradable.";

export function isDailyTrendStrategy(id: string | null | undefined): boolean {
  return (DAILY_TREND_STRATEGY_IDS as readonly string[]).includes(String(id ?? ""));
}

/** The later of a UTC day and the daily trend holdout cap is never sent: returns the earlier. */
export function capDailyTrendDay(day: string): string {
  return day > DAILY_TREND_LAST_DAY ? DAILY_TREND_LAST_DAY : day;
}

/**
 * 4h trend books: same holdout-guarded backend family ("daily_trend") as the daily
 * books, but on 4h bars. Book 16 is the only one: a COMPLETE + SCREEN_HIT in the
 * pre-registered 4h/1h screen on BTCUSDT and ETHUSDT.
 */
export const MTF_TREND_STRATEGY_IDS = ["MTF_4H_16_BREAKOUT252"] as const satisfies readonly BacktestStrategyId[];

export const MTF_TREND_OPTGROUP_LABEL = "4h trend (research, in-sample)";

/** Same sealed holdout as the daily books: the last UTC day a 4h book may load. */
export const MTF_TREND_LAST_DAY = DAILY_TREND_LAST_DAY;

export const MTF_TREND_HOLDOUT_BANNER =
  "Holdout sealed: data from 2026-07-15 is not loaded. 4h book 16 (252-bar closing-high breakout, long only) was a COMPLETE + SCREEN_HIT in the pre-registered 4h/1h screen on BTCUSDT and ETHUSDT only (29 and 21 trades over 2022 → 2026-07-14; fold 2022 lost on both). Results here are in-sample — for inspection, not confirmation. Funding is charged from bundled Binance settlements through 2026-07-14; trades held into July 2026 show funding UNRESOLVED. Fixed-$ risk sizing (the screen compounds 0.5%). 4h bars come from Postgres (2021-10-19 onward), not the screen's archive files. Research only — not tradable.";

/** Row PnL label when every trade's funding is resolved (same wording as the backend pnl_label). */
export const FUNDING_COMPLETE_PNL_LABEL = "After fees, slippage and funding.";
/** Row PnL label when any trade's funding is PARTIAL, UNRESOLVED or MISSING. */
export const FUNDING_INCOMPLETE_PNL_LABEL =
  "After fees, slippage and included funding; funding incomplete/unresolved.";

/** PnL label for a row's funding coverage: complete only when the status is COMPLETE. */
export function fundingPnlLabel(status: string | null | undefined): string {
  return status === "COMPLETE" ? FUNDING_COMPLETE_PNL_LABEL : FUNDING_INCOMPLETE_PNL_LABEL;
}

export function isMtfTrendStrategy(id: string | null | undefined): boolean {
  return (MTF_TREND_STRATEGY_IDS as readonly string[]).includes(String(id ?? ""));
}

/** Daily or 4h trend book: no run request or chart candle load may reach 2026-07-15. */
export function isHoldoutGuardedStrategy(id: string | null | undefined): boolean {
  return isDailyTrendStrategy(id) || isMtfTrendStrategy(id);
}

/** Bars per UTC day at a holdout-guarded strategy's bar size (1d = 1, 4h = 6). */
export function holdoutBarsPerDay(id: string | null | undefined): number {
  return isMtfTrendStrategy(id) ? 6 : 1;
}

/**
 * First UTC day of a DB-tail window of `bars` bars that ends on the holdout cap
 * (inclusive). A tail covers ceil(bars / barsPerDay) days, so 365 daily bars and
 * 2190 4h bars both start 2025-07-15.
 */
export function holdoutTailStartDay(bars: number, barsPerDay: number): string {
  const days = Math.ceil(Math.max(1, bars) / Math.max(1, barsPerDay));
  const ms = Date.parse(`${DAILY_TREND_LAST_DAY}T00:00:00Z`) - (days - 1) * 24 * 60 * 60 * 1000;
  return new Date(ms).toISOString().slice(0, 10);
}

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
  /**
   * Directions an engine strategy accepts. Omitted: the engine runs LONG only
   * (every engine strategy before the daily trend books).
   */
  engineDirections?: readonly EngineDirection[];
  /**
   * Symbols an engine strategy accepts (the backend refuses others with
   * engine_request_unsupported). Omitted: any cap-book coin.
   */
  engineSymbols?: readonly string[];
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
  // Daily trend books (backend family "daily_trend"): 1d bars only, in-sample research.
  {
    id: "DAILY_TREND_01_SMA200",
    label: "Daily · SMA200 (book 1)",
    status: "Research",
    combinationId: "DAILY_TREND_01_SMA200",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_02_EMA200",
    label: "Daily · EMA200 (book 2)",
    status: "Research",
    combinationId: "DAILY_TREND_02_EMA200",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_03_SMA50",
    label: "Daily · SMA50 (book 3)",
    status: "Research",
    combinationId: "DAILY_TREND_03_SMA50",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_04_MA_SLOPE",
    label: "Daily · MA slope (book 4)",
    status: "Research",
    combinationId: "DAILY_TREND_04_MA_SLOPE",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_05_GOLDEN_CROSS",
    label: "Daily · Golden/death cross (book 5)",
    status: "Research",
    combinationId: "DAILY_TREND_05_GOLDEN_CROSS",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_06_WEINSTEIN",
    label: "Daily · Weinstein stage (book 6)",
    status: "Research",
    combinationId: "DAILY_TREND_06_WEINSTEIN",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_09_DONCHIAN20_WEINSTEIN",
    label: "Daily · Donchian 20 + Weinstein (book 9)",
    status: "Research",
    combinationId: "DAILY_TREND_09_DONCHIAN20_WEINSTEIN",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_11_ADX_DMI",
    label: "Daily · ADX/DMI 14 (book 11)",
    status: "Research",
    combinationId: "DAILY_TREND_11_ADX_DMI",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_12_ICHIMOKU",
    label: "Daily · Ichimoku (book 12)",
    status: "Research",
    combinationId: "DAILY_TREND_12_ICHIMOKU",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_14_ROC90",
    label: "Daily · ROC 90 (book 14)",
    status: "Research",
    combinationId: "DAILY_TREND_14_ROC90",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_15_ROC30",
    label: "Daily · ROC 30 (book 15)",
    status: "Research",
    combinationId: "DAILY_TREND_15_ROC30",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_19_COMBINED",
    label: "Daily · Combined 4+7 (book 19)",
    status: "Research",
    combinationId: "DAILY_TREND_19_COMBINED",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_ROTATION",
    label: "Daily · Rotation top 3 of 12 (walk-forward)",
    status: "Research",
    combinationId: "DAILY_TREND_ROTATION",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  {
    id: "DAILY_TREND_STATIC12",
    label: "Daily · Static 12 (rotation benchmark)",
    status: "Research",
    combinationId: "DAILY_TREND_STATIC12",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["1d"],
    engineDirections: ["LONG", "SHORT", "BOTH"],
  },
  // 4h trend book (same holdout-guarded backend family): 4h bars, BTC/ETH only.
  // LONG only, so engineDirections is omitted (the engine default; no direction buttons).
  {
    id: "MTF_4H_16_BREAKOUT252",
    label: "4h · 252-bar breakout (book 16, BTC/ETH)",
    status: "Research",
    combinationId: "MTF_4H_16_BREAKOUT252",
    comboVersion: DAILY_TREND_COMBO_VERSION,
    direct: true,
    engineTimeframes: ["4h"],
    engineSymbols: ["BTCUSDT", "ETHUSDT"],
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
  // COMBO_02_V2 and COMBO_02_V2_1B run LONG and SHORT together (direction BOTH): hedge book.
  "COMBO_01",
  "CANDLE12_V2",
  "LARGE_CAP_SWEEP_CHOCH",
  "SMALL_CAP_VOLUME_BOS",
  "OSE_A_corrected_baseline",
  "OSE_B_acceptance",
  "OSE_D_faster_confirmation",
  "COMBO_SD_STRUCTURE",
  ...DAILY_TREND_STRATEGY_IDS,
  // Long only: not in the short book.
  ...MTF_TREND_STRATEGY_IDS,
] as const;

export const LONG_PASSED_IDS: readonly BacktestStrategyId[] = ["COMBO_03_TRANSITION_B"] as const;

export const LONG_RUN_STRATEGY_ID: BacktestStrategyId = "COMBO_03_TRANSITION_B";

/**
 * Passed shorts. The COMBO_02_V2 family (V2, V2_1B, V2.1-A) is the hedge book: V2.1-A
 * always walks both sides, and the hedge book posts V2 / V2_1B with direction BOTH, so
 * one run keeps both directions.
 */
export const SHORT_STRATEGY_IDS: readonly BacktestStrategyId[] = [
  "COMBO_02",
  "COMBO_02_CLOSED_HTF",
  "COMBO_03_TRANSITION",
  "COMBO_03_TRANSITION_B",
  "COMBO_01",
  "COMBO_02_LOCAL",
  ...DAILY_TREND_STRATEGY_IDS,
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
  | "custom_leverage"
  | "next_open_fill_book";

/**
 * The Backtest screen shows the next-open (CAUSAL_NEXT_OPEN) book for COMBO_02 v1.
 * The frozen v1 production profile was defined on signal-close fills, so a v1
 * run is not production comparable while that book is shown.
 */
export const NEXT_OPEN_FILL_BOOK_NOTE =
  "next-open fill book; v1 production profile was signal-close";

export function assessProductionComparable(input: {
  symbols: string[];
  timeframes: string[];
  direction: "LONG" | "SHORT" | "BOTH";
  researchRiskOverride: boolean;
  leverage: number;
  takerFeePct: number;
  makerFeePct: number;
  strategyId?: BacktestStrategyId;
}): { productionComparable: boolean; reasons: ConfigMismatchReason[]; note: string | null } {
  const reasons: ConfigMismatchReason[] = [];
  const isV1 = !input.strategyId || input.strategyId === "COMBO_02_V1";
  if (!isV1) {
    reasons.push("non_v1_strategy");
  } else {
    reasons.push("next_open_fill_book");
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
    note: isV1 ? NEXT_OPEN_FILL_BOOK_NOTE : null,
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
