/**
 * Trade Plan presentation layer.
 *
 * Consumes existing backend setup / market-signal payloads.
 * Does NOT recompute entry, stop, targets, or signals.
 */

export type TradePlanState =
  | "WAITING"
  | "NO_SETUP"
  | "BUY_BIAS"
  | "SELL_BIAS"
  | "LONG_ENTRY_CANDIDATE"
  | "SHORT_ENTRY_CANDIDATE"
  | "ENTRY_READY"
  | "INVALIDATED"
  | "CONFLICT";

export type ConditionVerdict = "PASS" | "FAIL" | "N/A" | "WAITING" | string;

export type TradeCondition = {
  id: string;
  label: string;
  verdict: ConditionVerdict;
  detail?: string;
  /** Presentation bucket */
  bucket: "confirm" | "missing" | "na" | "fail";
};

export type TradePlanLevel = {
  label: string;
  price: number | null;
  rMultiple: number | null;
  display: string;
};

export type TradePlanView = {
  symbol: string;
  timeframe: string;
  marketSignal: string;
  marketSignalDisplay: string;
  setupStatusRaw: string;
  planState: TradePlanState;
  statusMessage: string;
  direction: "LONG" | "SHORT" | null;
  entry: TradePlanLevel;
  stop: TradePlanLevel;
  riskPerUnit: number | null;
  targets: TradePlanLevel[];
  rrLines: string[];
  confirmations: TradeCondition[];
  missing: TradeCondition[];
  unavailable: TradeCondition[];
  failures: TradeCondition[];
  invalidation: string;
  why: string[];
  mtfLines: string[];
  conflictDetail: string | null;
  lifecycleStep: "SETUP" | "CONFIRMATION" | "ENTRY" | "STOP_TARGET" | "EXIT" | "WAIT";
  candleLifecycle: Array<{ title: string; detail: string }>;
  dataNotes: string[];
  /** True when UI invented nothing — all prices come from backend or are null */
  sourceOnly: true;
};

const HARD_ENTRY_IDS = new Set([
  "mtf",
  "trend",
  "bos",
  "impulse",
  "pullback",
  "structure",
  "risk",
  "targets",
  "rr",
]);

function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

function asArray(v: unknown): Array<Record<string, unknown>> {
  return Array.isArray(v) ? (v as Array<Record<string, unknown>>) : [];
}

function numOrNull(v: unknown): number | null {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fmtPrice(v: number | null): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: 8 });
}

function fmtR(v: number | null): string | null {
  if (v == null) return null;
  return `${v.toFixed(1)}R`;
}

export function marketSignalDisplay(raw: string): string {
  const s = String(raw || "WAITING").toUpperCase();
  if (s === "STRONG_BUY") return "STRONG BUY";
  if (s === "STRONG_SELL") return "STRONG SELL";
  return s || "WAITING";
}

export function isBullishMarket(signal: string): boolean {
  const s = String(signal || "").toUpperCase();
  return s === "BUY" || s === "STRONG_BUY";
}

export function isBearishMarket(signal: string): boolean {
  const s = String(signal || "").toUpperCase();
  return s === "SELL" || s === "STRONG_SELL";
}

function normalizeVerdict(v: unknown): ConditionVerdict {
  const s = String(v ?? "WAITING").toUpperCase();
  if (s === "PASS" || s === "FAIL" || s === "WAITING") return s;
  if (s === "N/A" || s === "NA" || s === "UNAVAILABLE") return "N/A";
  return s;
}

function conditionLabel(id: string, fallback?: string): string {
  const map: Record<string, string> = {
    htf_trend: "HTF bullish/bearish structure",
    primary_trend: "Primary TF structure",
    mtf: "Multi-timeframe alignment",
    mtf_alignment: "Multi-timeframe alignment",
    trend: "Setup timeframe trend",
    bos: "BOS confirmed",
    choch: "CHOCH",
    impulse: "Impulse confirmed",
    pullback: "Pullback detected",
    retest: "Retest confirmation",
    volume: "Volume confirmation",
    structure: "Structure intact",
    risk: "Stop defined",
    targets: "Targets defined",
    rr: "Risk:Reward",
    entry_setup: "Entry setup",
    risk_reward: "Risk:Reward",
    oi: "Open interest",
    liquidation: "Liquidations",
  };
  return fallback || map[id] || id.replace(/_/g, " ");
}

/**
 * Map backend setup status + entry conditions → trader-facing plan state.
 * ENTRY_READY only when entry engine already returned a candidate AND
 * every hard condition is PASS and retest is PASS (no new math).
 */
export function derivePlanState(input: {
  setupStatus: string;
  marketSignal: string;
  conditions: Array<{ id?: string; verdict?: string }>;
}): TradePlanState {
  const status = String(input.setupStatus || "WAITING").toUpperCase();
  const market = String(input.marketSignal || "WAITING").toUpperCase();
  const verdicts = new Map(
    input.conditions.map((c) => [String(c.id || ""), normalizeVerdict(c.verdict)]),
  );

  if (status === "WAITING") return "WAITING";
  if (status === "INVALIDATED") return "INVALIDATED";
  if (status === "CONFLICT") return "CONFLICT";

  if (
    status === "LONG_ENTRY_CANDIDATE" ||
    status === "SHORT_ENTRY_CANDIDATE" ||
    status === "ENTRY_CANDIDATE"
  ) {
    const hardOk = [...HARD_ENTRY_IDS].every((id) => {
      const v = verdicts.get(id);
      // If backend omitted the id, do not invent ENTRY_READY
      if (v == null) return false;
      return v === "PASS" || v === "N/A";
    });
    const retest = verdicts.get("retest");
    if (hardOk && retest === "PASS") return "ENTRY_READY";
    if (status === "SHORT_ENTRY_CANDIDATE") return "SHORT_ENTRY_CANDIDATE";
    if (status === "LONG_ENTRY_CANDIDATE" || status === "ENTRY_CANDIDATE") {
      return "LONG_ENTRY_CANDIDATE";
    }
  }

  if (status === "NO_SETUP") {
    if (isBullishMarket(market)) return "BUY_BIAS";
    if (isBearishMarket(market)) return "SELL_BIAS";
    return "NO_SETUP";
  }

  // Unknown backend status — surface as NO_SETUP rather than inventing
  if (isBullishMarket(market)) return "BUY_BIAS";
  if (isBearishMarket(market)) return "SELL_BIAS";
  return "NO_SETUP";
}

export function statusMessageFor(
  state: TradePlanState,
  opts?: { waitingReason?: string; conflictDetail?: string },
): string {
  switch (state) {
    case "WAITING":
      return opts?.waitingReason || "WAITING FOR DATA / CONFIRMATION";
    case "NO_SETUP":
      return "NO ACTIVE SETUP — WAIT";
    case "BUY_BIAS":
      return "BULLISH BIAS — NO DEFINED ENTRY YET";
    case "SELL_BIAS":
      return "BEARISH BIAS — NO DEFINED ENTRY YET";
    case "LONG_ENTRY_CANDIDATE":
      return "WAIT FOR CONFIRMATION";
    case "SHORT_ENTRY_CANDIDATE":
      return "WAIT FOR CONFIRMATION";
    case "ENTRY_READY":
      return "ENTRY CONDITIONS MET (ANALYSIS ONLY — NOT A TRADE ORDER)";
    case "INVALIDATED":
      return "SETUP INVALIDATED — DO NOT ENTER";
    case "CONFLICT":
      return opts?.conflictDetail
        ? `CONFLICT — WAIT (${opts.conflictDetail})`
        : "CONFLICT — WAIT";
    default:
      return "WAIT";
  }
}

export function bucketCondition(verdict: ConditionVerdict): TradeCondition["bucket"] {
  const v = normalizeVerdict(verdict);
  if (v === "PASS") return "confirm";
  if (v === "N/A") return "na";
  if (v === "WAITING") return "missing";
  if (v === "FAIL") return "fail";
  return "missing";
}

/**
 * Build confirmations / missing / N/A lists.
 * Unavailable OI / liquidation → N/A (never FAIL/bearish).
 */
export function classifyConditions(
  conditions: Array<Record<string, unknown>>,
  marketConds?: Record<string, string>,
): TradeCondition[] {
  const out: TradeCondition[] = [];
  const seen = new Set<string>();

  for (const c of conditions) {
    const id = String(c.id || "");
    if (!id || seen.has(id)) continue;
    seen.add(id);
    let verdict = normalizeVerdict(c.verdict);
    // Soft data: never treat missing OI/liq as FAIL in presentation
    if ((id === "oi" || id === "liquidation") && (verdict === "FAIL" || verdict === "WAITING")) {
      verdict = "N/A";
    }
    out.push({
      id,
      label: conditionLabel(id, String(c.label || "")),
      verdict,
      detail: c.detail != null ? String(c.detail) : undefined,
      bucket: bucketCondition(verdict),
    });
  }

  if (marketConds) {
    for (const [id, raw] of Object.entries(marketConds)) {
      if (seen.has(id)) continue;
      seen.add(id);
      let verdict = normalizeVerdict(raw);
      if ((id === "oi" || id === "liquidation") && verdict !== "PASS") {
        verdict = "N/A";
      }
      out.push({
        id,
        label: conditionLabel(id),
        verdict,
        bucket: bucketCondition(verdict),
      });
    }
  }

  return out;
}

export function pickTarget(
  targets: Array<Record<string, unknown>>,
  name: string,
): TradePlanLevel {
  const t = targets.find((x) => String(x.name || "").toUpperCase() === name);
  if (!t) {
    return { label: name, price: null, rMultiple: null, display: "—" };
  }
  const price = numOrNull(t.target_price);
  const r = numOrNull(t.r_multiple);
  return {
    label: name,
    price,
    rMultiple: r,
    display: price == null ? "—" : fmtPrice(price),
  };
}

export function computePositionSize(input: {
  accountSize: number;
  riskPercent: number;
  entry: number | null;
  stop: number | null;
}): {
  maxRisk: number | null;
  riskPerUnit: number | null;
  positionSize: number | null;
  formula: string;
  valid: boolean;
  error?: string;
} {
  const { accountSize, riskPercent, entry, stop } = input;
  if (!(accountSize > 0) || !(riskPercent > 0)) {
    return {
      maxRisk: null,
      riskPerUnit: null,
      positionSize: null,
      formula: "—",
      valid: false,
      error: "Enter account size and risk %",
    };
  }
  if (entry == null || stop == null) {
    return {
      maxRisk: accountSize * (riskPercent / 100),
      riskPerUnit: null,
      positionSize: null,
      formula: "—",
      valid: false,
      error: "Entry/SL unavailable from setup engine",
    };
  }
  const riskPerUnit = Math.abs(entry - stop);
  if (!(riskPerUnit > 0)) {
    return {
      maxRisk: accountSize * (riskPercent / 100),
      riskPerUnit: 0,
      positionSize: null,
      formula: "—",
      valid: false,
      error: "Entry and stop are equal — no risk distance (setup has no usable SL yet)",
    };
  }
  const maxRisk = accountSize * (riskPercent / 100);
  const positionSize = maxRisk / riskPerUnit;
  return {
    maxRisk,
    riskPerUnit,
    positionSize,
    formula: `${maxRisk} / ${riskPerUnit}`,
    valid: true,
  };
}

function waitingReasonFromDeps(deps: Record<string, string>, timeframe: string): string {
  const entries = Object.entries(deps || {});
  const waiting = entries.filter(([, v]) => /WAIT|MISSING|UNAVAILABLE/i.test(String(v)));
  if (waiting.length === 0) return `WAITING FOR ${timeframe.toUpperCase()} CONFIRMATION`;
  const ohlcv = waiting.find(([k]) => /ohlcv|candle/i.test(k));
  if (ohlcv) {
    return `WAITING FOR ${timeframe.toUpperCase()} OHLCV`;
  }
  return `WAITING — ${waiting.map(([k]) => k).join(", ")}`;
}

function conflictDetailFromTrends(trend: Record<string, unknown>, mtf: Record<string, unknown>): string {
  const trends = asRecord(mtf.trends || trend);
  const parts = Object.entries(trends)
    .filter(([, v]) => v != null && String(v) !== "")
    .map(([tf, v]) => `${String(tf).toUpperCase()}: ${String(v).toUpperCase()}`);
  return parts.join(" · ");
}

export function buildTradePlan(input: {
  symbol: string;
  setupTab?: Record<string, unknown> | null;
  setupSignalFresh?: { value?: string | number | null; status?: string } | null;
  marketSignalFresh?: { value?: string | number | null; status?: string } | null;
  defaultTimeframe?: string;
}): TradePlanView {
  const symbol = input.symbol || "";
  const payload = asRecord(input.setupTab);
  const analysis = asRecord(payload.analysis || payload);
  const trade = asRecord(payload.trade_plan);
  const entry = asRecord(trade.entry || analysis.entry);
  const stop = asRecord(trade.stop || analysis.stop);
  const targets = asArray(trade.targets || analysis.targets);
  const rr = asRecord(trade.risk_reward || analysis.risk_reward);
  const conditionsRaw = asArray(payload.conditions || analysis.conditions);
  const deps = asRecord(payload.data_dependencies || analysis.data_dependencies) as Record<
    string,
    string
  >;
  const mtf = asRecord(payload.mtf || analysis.mtf);
  const trend = asRecord(payload.trend || analysis.trend);
  const setupState = asRecord(payload.setup_state);
  const timeframe = String(
    analysis.timeframe || input.defaultTimeframe || "15m",
  ).toLowerCase();

  const setupStatusRaw = String(
    setupState.status ||
      entry.status ||
      analysis.status ||
      input.setupSignalFresh?.value ||
      "WAITING",
  ).toUpperCase();

  const marketSignal = String(
    payload.market_signal ||
      analysis.market_signal ||
      input.marketSignalFresh?.value ||
      "WAITING",
  ).toUpperCase();

  const msConds = asRecord(
    payload.market_signal_conditions ||
      asRecord(analysis.market_signal_payload).conditions,
  ) as Record<string, string>;

  const classified = classifyConditions(conditionsRaw, msConds);
  const planState = derivePlanState({
    setupStatus: setupStatusRaw,
    marketSignal,
    conditions: conditionsRaw.map((c) => ({
      id: String(c.id || ""),
      verdict: String(c.verdict || ""),
    })),
  });

  const conflictDetail =
    planState === "CONFLICT" ? conflictDetailFromTrends(trend, mtf) : null;
  const waitingReason =
    planState === "WAITING" ? waitingReasonFromDeps(deps, timeframe) : undefined;

  const directionRaw = String(entry.direction || analysis.direction || "").toUpperCase();
  let direction: "LONG" | "SHORT" | null = null;
  if (directionRaw === "LONG" || directionRaw === "SHORT") direction = directionRaw;
  else if (planState === "LONG_ENTRY_CANDIDATE" || planState === "BUY_BIAS") direction = "LONG";
  else if (planState === "SHORT_ENTRY_CANDIDATE" || planState === "SELL_BIAS") direction = "SHORT";
  else if (planState === "ENTRY_READY") {
    if (setupStatusRaw.includes("SHORT")) direction = "SHORT";
    else if (setupStatusRaw.includes("LONG") || setupStatusRaw === "ENTRY_CANDIDATE")
      direction = "LONG";
  }

  const entryPrice = numOrNull(entry.entry_price);
  const stopPrice = numOrNull(stop.final_stop ?? stop.structural_stop);
  const riskPerUnit = numOrNull(stop.risk_per_unit) ??
    (entryPrice != null && stopPrice != null ? Math.abs(entryPrice - stopPrice) : null);

  const tp1 = pickTarget(targets, "TP1");
  const tp2 = pickTarget(targets, "TP2");
  const tp3 = pickTarget(targets, "TP3");

  const rrLines: string[] = [];
  const tp1R = numOrNull(rr.TP1_R) ?? tp1.rMultiple;
  const tp2R = numOrNull(rr.TP2_R) ?? tp2.rMultiple;
  const tp3R = numOrNull(rr.TP3_R) ?? tp3.rMultiple;
  rrLines.push(`TP1 ${tp1R != null ? fmtR(tp1R) : "—"}`);
  rrLines.push(`TP2 ${tp2R != null ? fmtR(tp2R) : "—"}`);
  rrLines.push(`TP3 ${tp3R != null ? fmtR(tp3R) : "—"}`);

  const confirmations = classified.filter((c) => c.bucket === "confirm");
  const missing = classified.filter((c) => c.bucket === "missing");
  const unavailable = classified.filter((c) => c.bucket === "na");
  const failures = classified.filter((c) => c.bucket === "fail");

  const invalidation = String(
    stop.invalidation_reason ||
      analysis.invalidation_reason ||
      (planState === "INVALIDATED" ? "Setup invalidated by structure" : ""),
  );

  const why: string[] = [];
  for (const c of confirmations) {
    why.push(`✓ ${c.label}${c.detail ? ` — ${c.detail}` : ""}`);
  }
  for (const c of missing) {
    why.push(`○ ${c.label}${c.detail ? ` — ${c.detail}` : ""}`);
  }
  for (const c of unavailable) {
    why.push(`N/A — ${c.label} unavailable`);
  }

  const mtfLines = Object.entries(asRecord(mtf.trends || trend)).map(
    ([tf, v]) => `${String(tf).toUpperCase()}: ${String(v ?? "WAITING").toUpperCase()}`,
  );

  let lifecycleStep: TradePlanView["lifecycleStep"] = "WAIT";
  if (planState === "WAITING") lifecycleStep = "WAIT";
  else if (planState === "NO_SETUP" || planState === "BUY_BIAS" || planState === "SELL_BIAS")
    lifecycleStep = "SETUP";
  else if (planState === "LONG_ENTRY_CANDIDATE" || planState === "SHORT_ENTRY_CANDIDATE")
    lifecycleStep = "CONFIRMATION";
  else if (planState === "ENTRY_READY") lifecycleStep = "ENTRY";
  else if (planState === "INVALIDATED") lifecycleStep = "EXIT";
  else if (planState === "CONFLICT") lifecycleStep = "WAIT";

  const candleLifecycle = [
    { title: "CANDLE 1", detail: "Setup detected (structure / impulse / pullback)" },
    { title: "CANDLE 2", detail: "Confirmation required (retest / conditions)" },
    {
      title: "ENTRY",
      detail: "Only if existing entry engine conditions pass — never auto-executed",
    },
  ];

  const dataNotes: string[] = [];
  const seenNoteKeys = new Set<string>();
  const noteKey = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "");
  for (const [k, v] of Object.entries(deps)) {
    if (/WAIT|MISSING|UNAVAILABLE|N\/A/i.test(String(v))) {
      seenNoteKeys.add(noteKey(k));
      dataNotes.push(`${k}: ${v}`);
    }
  }
  for (const c of unavailable) {
    const key = noteKey(c.id || c.label);
    // Skip duplicates like deps "OI: WAITING" + condition "OI: N/A"
    if ([...seenNoteKeys].some((s) => s.includes(key) || key.includes(s))) continue;
    seenNoteKeys.add(key);
    dataNotes.push(`${c.label}: N/A — data unavailable`);
  }

  return {
    symbol,
    timeframe,
    marketSignal,
    marketSignalDisplay: marketSignalDisplay(marketSignal),
    setupStatusRaw,
    planState,
    statusMessage: statusMessageFor(planState, { waitingReason, conflictDetail: conflictDetail || undefined }),
    direction,
    entry: {
      label: "ENTRY",
      price: entryPrice,
      rMultiple: null,
      display:
        planState === "WAITING" || planState === "NO_SETUP" || planState === "BUY_BIAS" || planState === "SELL_BIAS"
          ? entryPrice != null
            ? fmtPrice(entryPrice)
            : "—"
          : fmtPrice(entryPrice),
    },
    stop: {
      label: "STOP LOSS",
      price: stopPrice,
      rMultiple: null,
      display: fmtPrice(stopPrice),
    },
    riskPerUnit,
    targets: [tp1, tp2, tp3],
    rrLines,
    confirmations,
    missing,
    unavailable,
    failures,
    invalidation: invalidation || "—",
    why,
    mtfLines,
    conflictDetail,
    lifecycleStep,
    candleLifecycle,
    dataNotes,
    sourceOnly: true,
  };
}
