/** COMBO_02 v1 Strategy Backtest configuration helpers (UI-only). */

export const V1_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;
export const V1_SETUP_TF = "1h";
export const SHORT_STATUS = "PAUSED" as const;

export type V1SymbolRole = "CORE" | "SECONDARY" | "RESEARCH" | "NON_V1";

export const V1_PRODUCTION_RISK: Record<
  (typeof V1_SYMBOLS)[number],
  { role: "core" | "secondary"; riskPercent: number; riskUsdAt1k: number }
> = {
  BTCUSDT: { role: "core", riskPercent: 1.5, riskUsdAt1k: 15 },
  ETHUSDT: { role: "secondary", riskPercent: 0.5, riskUsdAt1k: 5 },
  SOLUSDT: { role: "secondary", riskPercent: 0.5, riskUsdAt1k: 5 },
};

const TF_MINUTES: Record<string, number> = {
  "15m": 15,
  "1h": 60,
  "4h": 240,
  "5m": 5,
  "1d": 1440,
};

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
}): { productionComparable: boolean; reasons: ConfigMismatchReason[] } {
  const reasons: ConfigMismatchReason[] = [];
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
