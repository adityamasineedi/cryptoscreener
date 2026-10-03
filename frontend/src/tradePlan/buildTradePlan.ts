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

export type EvidenceBucket =
  | "mtf"
  | "directional"
  | "data"
  | "missing"
  | "fail"
  | "na";

export type TradeCondition = {
  id: string;
  label: string;
  verdict: ConditionVerdict;
  detail?: string;
  /** Presentation bucket */
  bucket: EvidenceBucket | "confirm";
};

export type DataAvailabilityStatus = "LIVE" | "STALE" | "WAITING" | "UNAVAILABLE";

export type DataAvailabilityItem = {
  id: string;
  label: string;
  status: DataAvailabilityStatus;
};

export type MtfTfLine = {
  tf: string;
  trend: string;
  role: "major" | "primary" | "setup" | "entry" | string;
};

export type MtfAlignmentView = {
  /** Raw backend MTF_ALIGNMENT */
  raw: string;
  /** Presentation label e.g. ALIGNED BULLISH / MIXED / CONFLICT */
  display: string;
  tone: "aligned" | "mixed" | "conflict" | "waiting" | "unavailable";
  htf: MtfTfLine[];
  setup: MtfTfLine | null;
  confirmation: MtfTfLine | null;
  allLines: MtfTfLine[];
  /** Compact header lines: "4H BULLISH" */
  summaryLines: string[];
  conflictNote: string | null;
  reason: string | null;
};

export type ProvisionalInvalidation = {
  price: number;
  display: string;
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
  /** Short WHY under status, from existing MTF state only */
  statusWhy: string | null;
  direction: "LONG" | "SHORT" | null;
  entry: TradePlanLevel;
  stop: TradePlanLevel;
  riskPerUnit: number | null;
  targets: TradePlanLevel[];
  rrLines: string[];
  /** @deprecated use directionalEvidence — kept for older callers */
  confirmations: TradeCondition[];
  missing: TradeCondition[];
  unavailable: TradeCondition[];
  failures: TradeCondition[];
  directionalEvidence: TradeCondition[];
  dataAvailable: DataAvailabilityItem[];
  missingEntryConfirmations: TradeCondition[];
  failedEntryGates: TradeCondition[];
  unavailableNotConfirmed: TradeCondition[];
  mtfAlignment: MtfAlignmentView;
  provisionalInvalidation: ProvisionalInvalidation | null;
  levelsActionable: boolean;
  currentStage: string;
  invalidation: string;
  why: string[];
  mtfLines: string[];
  conflictDetail: string | null;
  lifecycleStep: "SETUP" | "CONFIRMATION" | "ENTRY" | "STOP_TARGET" | "EXIT" | "WAIT";
  lifecycleWaitingNote: string | null;
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

/** Entry-engine soft availability metrics — never directional from availability alone */
const AVAILABILITY_ONLY_IDS = new Set(["oi", "liquidation"]);

/** Shown in MTF Alignment section, not as generic confirmations */
const MTF_SECTION_IDS = new Set(["mtf", "mtf_alignment", "htf_trend", "primary_trend"]);

/** Structural / directional condition ids (when they carry a real result) */
const DIRECTIONAL_IDS = new Set([
  "trend",
  "bos",
  "impulse",
  "volume",
  "choch",
  "pullback",
  "retest",
  "supply_demand",
]);

function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

function asArray(v: unknown): Array<Record<string, unknown>> {
  return Array.isArray(v) ? (v as Array<Record<string, unknown>>) : [];
}

function numOrNull(v: unknown): number | null {
  // Number(null) === 0 in JS — treat null/undefined/"" as missing
  if (v == null || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Valid trade entry price (rejects null and non-positive). */
function validEntryPrice(v: unknown): number | null {
  const n = numOrNull(v);
  if (n == null || !(n > 0)) return null;
  return n;
}

/** Condition ids that are aliases across entry vs market-signal payloads */
const CONDITION_ALIASES: Record<string, string> = {
  mtf_alignment: "mtf",
  risk_reward: "rr",
};

/** Soft/contextual N/A — not a missing data feed */
const CONTEXT_NA_IDS = new Set(["choch", "supply_demand", "entry_setup"]);

function fmtPrice(v: number | null): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: 8 });
}

function fmtPriceCompact(v: number): string {
  return v.toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: 0,
  });
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
    mtf: "MTF",
    mtf_alignment: "MTF alignment",
    trend: "Trend",
    bos: "BOS",
    choch: "CHOCH",
    impulse: "Impulse",
    pullback: "Pullback",
    retest: "Retest",
    volume: "Volume",
    structure: "Structure",
    risk: "Risk",
    targets: "Targets",
    rr: "R:R",
    entry_setup: "Entry setup",
    risk_reward: "R:R",
    oi: "OI",
    liquidation: "Liquidation",
    supply_demand: "supply/demand",
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
  opts?: { waitingReason?: string },
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
      return "CONFLICT — WAIT";
    default:
      return "WAIT";
  }
}

/** @deprecated use categorizeEvidence — kept for unit tests of verdict→bucket */
export function bucketCondition(verdict: ConditionVerdict): TradeCondition["bucket"] {
  const v = normalizeVerdict(verdict);
  if (v === "PASS") return "confirm";
  if (v === "N/A") return "na";
  if (v === "WAITING") return "missing";
  if (v === "FAIL") return "fail";
  return "missing";
}

function isAvailabilityDetail(detail: string | undefined): boolean {
  if (!detail) return false;
  return /^(available|live|stale|waiting|n\/a|unavailable|mandatory)$/i.test(detail.trim());
}

/**
 * Assign a presentation evidence bucket without inventing trading meaning.
 * Availability-only OI/liquidation never become directional evidence.
 */
export function evidenceBucketFor(c: {
  id: string;
  verdict: ConditionVerdict;
  detail?: string;
}): EvidenceBucket {
  const id = c.id;
  const verdict = normalizeVerdict(c.verdict);
  const detail = c.detail;

  if (MTF_SECTION_IDS.has(id)) return "mtf";

  if (AVAILABILITY_ONLY_IDS.has(id)) {
    if (verdict === "PASS" && (isAvailabilityDetail(detail) || !detail)) return "data";
    if (verdict === "N/A" || verdict === "WAITING") return "na";
    if (verdict === "FAIL") {
      // Soft feeds: absence is N/A presentation, not a failed entry gate
      return "na";
    }
    return "data";
  }

  if (verdict === "WAITING") return "missing";
  if (verdict === "N/A") return "na";
  if (verdict === "FAIL") return "fail";

  if (verdict === "PASS") {
    if (DIRECTIONAL_IDS.has(id)) return "directional";
    if (id === "entry_setup") return "directional";
    if (id === "structure" || id === "risk" || id === "targets" || id === "rr") {
      return "directional";
    }
    return "directional";
  }

  return "missing";
}

/**
 * Build flat condition list from entry + market-signal payloads.
 * Soft OI / liquidation absence → N/A (never FAIL/bearish).
 */
export function classifyConditions(
  conditions: Array<Record<string, unknown>>,
  marketConds?: Record<string, string>,
): TradeCondition[] {
  const out: TradeCondition[] = [];
  const seen = new Set<string>();

  const markSeen = (id: string) => {
    seen.add(id);
    const alias = CONDITION_ALIASES[id];
    if (alias) seen.add(alias);
    for (const [from, to] of Object.entries(CONDITION_ALIASES)) {
      if (to === id) seen.add(from);
    }
  };

  for (const c of conditions) {
    const id = String(c.id || "");
    if (!id || seen.has(id)) continue;
    markSeen(id);
    let verdict = normalizeVerdict(c.verdict);
    // Soft data: never treat missing OI/liq as FAIL in presentation
    if ((id === "oi" || id === "liquidation") && (verdict === "FAIL" || verdict === "WAITING")) {
      verdict = "N/A";
    }
    const detail = c.detail != null && String(c.detail) !== "" ? String(c.detail) : undefined;
    const bucket = evidenceBucketFor({ id, verdict, detail });
    out.push({
      id,
      label: conditionLabel(id, String(c.label || "")),
      verdict,
      detail,
      bucket: bucket === "directional" ? "confirm" : bucket,
    });
  }

  if (marketConds) {
    for (const [id, raw] of Object.entries(marketConds)) {
      if (seen.has(id)) continue;
      // Prefer entry-engine mtf/rr over market-signal aliases
      if (CONDITION_ALIASES[id] && seen.has(CONDITION_ALIASES[id])) continue;
      markSeen(id);
      let verdict = normalizeVerdict(raw);
      if ((id === "oi" || id === "liquidation") && verdict !== "PASS") {
        verdict = "N/A";
      }
      let detail: string | undefined;
      if (verdict === "N/A" && id === "choch") detail = "not confirmed on setup TF";
      if (verdict === "N/A" && id === "supply_demand") detail = "no zone interaction yet";
      if (verdict === "N/A" && id === "liquidation") detail = "stream not live";
      const bucket = evidenceBucketFor({ id, verdict, detail });
      out.push({
        id,
        label: conditionLabel(id),
        verdict,
        detail,
        bucket: bucket === "directional" ? "confirm" : bucket,
      });
    }
  }

  return out;
}

function normalizeDataStatus(raw: string | undefined | null): DataAvailabilityStatus | null {
  if (raw == null || raw === "") return null;
  const s = String(raw).toUpperCase();
  if (s === "LIVE") return "LIVE";
  if (s === "STALE" || s === "CACHED") return "STALE";
  if (s === "WAITING" || s.includes("WAIT")) return "WAITING";
  if (s === "UNAVAILABLE" || s === "N/A" || s === "NA" || s === "ERROR") return "UNAVAILABLE";
  if (s === "LIVE" || s.includes("LIVE")) return "LIVE";
  return null;
}

/**
 * Build Data Available rows from entry conditions + data_dependencies.
 * Availability is never treated as directional confirmation.
 */
export function buildDataAvailable(input: {
  conditions: TradeCondition[];
  deps: Record<string, string>;
}): DataAvailabilityItem[] {
  const out: DataAvailabilityItem[] = [];
  const seen = new Set<string>();

  const push = (id: string, label: string, status: DataAvailabilityStatus) => {
    if (seen.has(id)) return;
    if (status === "UNAVAILABLE" || status === "WAITING") return;
    seen.add(id);
    out.push({ id, label, status });
  };

  const depOi = normalizeDataStatus(input.deps.OI ?? input.deps.oi);
  const depLiq = normalizeDataStatus(
    input.deps.Liquidations ?? input.deps.liquidations ?? input.deps.Liquidation,
  );

  for (const c of input.conditions) {
    if (!AVAILABILITY_ONLY_IDS.has(c.id)) continue;
    if (c.id === "oi") {
      const fromDep = depOi;
      if (fromDep === "LIVE" || fromDep === "STALE") {
        push("oi", "OI", fromDep);
      } else if (c.verdict === "PASS" && isAvailabilityDetail(c.detail)) {
        push("oi", "OI", "LIVE");
      }
    }
    if (c.id === "liquidation") {
      const fromDep = depLiq;
      if (fromDep === "LIVE" || fromDep === "STALE") {
        push("liquidation", "Liquidation", fromDep);
      } else if (c.verdict === "PASS" && isAvailabilityDetail(c.detail)) {
        push("liquidation", "Liquidation", "LIVE");
      }
    }
  }

  // Deps may mark LIVE even when conditions omitted
  if (depOi === "LIVE" || depOi === "STALE") push("oi", "OI", depOi);
  if (depLiq === "LIVE" || depLiq === "STALE") push("liquidation", "Liquidation", depLiq);

  return out;
}

export function buildMtfAlignment(input: {
  mtf: Record<string, unknown>;
  trend: Record<string, unknown>;
  planState: TradePlanState;
}): MtfAlignmentView {
  const mtf = input.mtf;
  const trends = asRecord(mtf.trends || input.trend);
  const roles = asRecord(mtf.roles);
  const raw = String(mtf.MTF_ALIGNMENT || mtf.alignment || "").toUpperCase();
  const reason = mtf.reason != null ? String(mtf.reason) : null;

  const order = ["major", "primary", "setup", "entry"] as const;
  const byRole = new Map<string, MtfTfLine>();
  const allLines: MtfTfLine[] = [];

  // Prefer role map when present
  for (const [tf, role] of Object.entries(roles)) {
    const trend = String(trends[tf] ?? "WAITING").toUpperCase();
    const line: MtfTfLine = {
      tf: String(tf).toUpperCase(),
      trend,
      role: String(role),
    };
    byRole.set(String(role), line);
    allLines.push(line);
  }

  if (allLines.length === 0) {
    for (const [tf, v] of Object.entries(trends)) {
      if (v == null || String(v) === "") continue;
      allLines.push({
        tf: String(tf).toUpperCase(),
        trend: String(v).toUpperCase(),
        role: "",
      });
    }
  }

  // Stable order by role when available
  allLines.sort((a, b) => {
    const ai = order.indexOf(a.role as (typeof order)[number]);
    const bi = order.indexOf(b.role as (typeof order)[number]);
    if (ai === -1 && bi === -1) return a.tf.localeCompare(b.tf);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });

  const htf = [byRole.get("major"), byRole.get("primary")].filter(Boolean) as MtfTfLine[];
  const setup = byRole.get("setup") || null;
  const confirmation = byRole.get("entry") || null;

  const directional = allLines
    .map((l) => l.trend)
    .filter((t) => t === "BULLISH" || t === "BEARISH");
  const allBull =
    directional.length > 0 && directional.every((t) => t === "BULLISH");
  const allBear =
    directional.length > 0 && directional.every((t) => t === "BEARISH");
  const mixedDirs =
    directional.includes("BULLISH") && directional.includes("BEARISH");

  // Prefer authoritative backend MTF_ALIGNMENT; fall back to observed TF agreement.
  let display = raw || "WAITING";
  let tone: MtfAlignmentView["tone"] = "waiting";

  if (raw === "STRONG_LONG") {
    display = "ALIGNED BULLISH";
    tone = "aligned";
  } else if (raw === "STRONG_SHORT") {
    display = "ALIGNED BEARISH";
    tone = "aligned";
  } else if (raw === "MIXED") {
    display = "MIXED";
    tone = "mixed";
  } else if (raw === "CONFLICT") {
    display = "CONFLICT";
    tone = "conflict";
  } else if (raw === "WAITING") {
    display = "WAITING";
    tone = "waiting";
  } else if (raw === "INSUFFICIENT" || raw === "UNAVAILABLE") {
    display = raw;
    tone = "unavailable";
  } else if (allBull) {
    display = "ALIGNED BULLISH";
    tone = "aligned";
  } else if (allBear) {
    display = "ALIGNED BEARISH";
    tone = "aligned";
  } else if (mixedDirs || input.planState === "CONFLICT") {
    display = mixedDirs ? "MIXED" : "CONFLICT";
    tone = mixedDirs ? "mixed" : "conflict";
  }

  let conflictNote: string | null = null;
  if (tone === "mixed" || tone === "conflict" || input.planState === "CONFLICT") {
    const htfDirs = htf.map((l) => l.trend).filter((t) => t === "BULLISH" || t === "BEARISH");
    const lower = [setup, confirmation]
      .filter(Boolean)
      .map((l) => (l as MtfTfLine).trend)
      .filter((t) => t === "BULLISH" || t === "BEARISH");
    const htfBull = htfDirs.every((t) => t === "BULLISH") && htfDirs.length > 0;
    const htfBear = htfDirs.every((t) => t === "BEARISH") && htfDirs.length > 0;
    const lowerBear = lower.includes("BEARISH") && !lower.includes("BULLISH");
    const lowerBull = lower.includes("BULLISH") && !lower.includes("BEARISH");
    if (htfBull && lowerBear) {
      conflictNote = "Higher-timeframe bullish vs setup/lower timeframe bearish";
    } else if (htfBear && lowerBull) {
      conflictNote = "Higher-timeframe bearish vs setup/lower timeframe bullish";
    } else if (reason) {
      conflictNote = reason;
    } else if (mixedDirs) {
      conflictNote = "Timeframes currently disagree on direction";
    }
  }

  const summaryLines = allLines.map((l) => `${l.tf} ${l.trend}`);

  return {
    raw,
    display,
    tone,
    htf,
    setup,
    confirmation,
    allLines,
    summaryLines,
    conflictNote,
    reason,
  };
}

export function statusWhyFor(input: {
  planState: TradePlanState;
  mtfAlignment: MtfAlignmentView;
}): string | null {
  if (input.planState !== "CONFLICT") return null;
  if (input.mtfAlignment.conflictNote) {
    return "Higher-timeframe and setup-timeframe directions are conflicting.";
  }
  if (input.mtfAlignment.reason) return input.mtfAlignment.reason;
  return "Timeframe directions are conflicting.";
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
  if (entry == null || stop == null || !(entry > 0) || !(stop > 0)) {
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
  // Soft feeds (OI / liquidations) must not dominate setup WAITING status copy
  const softKey = (k: string) => /^(oi|liquidations?)$/i.test(k.trim());
  const waiting = entries.filter(
    ([k, v]) => /WAIT|MISSING|UNAVAILABLE/i.test(String(v)) && !softKey(k),
  );
  if (waiting.length === 0) return `WAITING FOR ${timeframe.toUpperCase()} CONFIRMATION`;
  const ohlcv = waiting.find(([k]) => /ohlcv|candle|^[0-9]+[mhdw]$/i.test(k));
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

function enrichMissingDetail(
  c: TradeCondition,
  setupState: Record<string, unknown>,
): TradeCondition {
  if (c.detail) return c;
  // Surface existing setup_state reasons when condition.detail was empty — no invented copy
  const pullback = asRecord(setupState.pullback);
  const retest = asRecord(setupState.retest);
  if (c.id === "pullback" && pullback.reason) {
    return { ...c, detail: String(pullback.reason) };
  }
  if (c.id === "retest" && retest.reason) {
    return { ...c, detail: String(retest.reason) };
  }
  return c;
}

function failReasonFor(c: TradeCondition, planState: TradePlanState): TradeCondition {
  if (c.detail) return c;
  if (c.id === "entry_setup") {
    if (planState === "CONFLICT") {
      return { ...c, detail: "Entry conditions not satisfied" };
    }
    return { ...c, detail: "Entry conditions not satisfied" };
  }
  return c;
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

  const mtfAlignment = buildMtfAlignment({ mtf, trend, planState });
  const conflictDetail =
    planState === "CONFLICT" || mtfAlignment.tone === "mixed" || mtfAlignment.tone === "conflict"
      ? conflictDetailFromTrends(trend, mtf) || null
      : null;
  const waitingReason =
    planState === "WAITING" ? waitingReasonFromDeps(deps, timeframe) : undefined;
  const statusWhy = statusWhyFor({ planState, mtfAlignment });

  const directionRaw = String(entry.direction || analysis.direction || "").toUpperCase();
  let direction: "LONG" | "SHORT" | null = null;
  if (planState === "LONG_ENTRY_CANDIDATE" || planState === "BUY_BIAS") direction = "LONG";
  else if (planState === "SHORT_ENTRY_CANDIDATE" || planState === "SELL_BIAS") direction = "SHORT";
  else if (planState === "ENTRY_READY") {
    if (setupStatusRaw.includes("SHORT")) direction = "SHORT";
    else if (setupStatusRaw.includes("LONG") || setupStatusRaw === "ENTRY_CANDIDATE")
      direction = "LONG";
  } else if (
    (directionRaw === "LONG" || directionRaw === "SHORT") &&
    planState !== "WAITING" &&
    planState !== "NO_SETUP" &&
    planState !== "CONFLICT"
  ) {
    direction = directionRaw;
  }

  const entryPrice = validEntryPrice(entry.entry_price);
  const stopProvisional = Boolean(stop.provisional);
  const rawStop = numOrNull(stop.final_stop ?? stop.structural_stop);
  // Provisional BOS stops without a confirmed entry are not actionable trade levels
  const stopPrice =
    stopProvisional && entryPrice == null
      ? null
      : rawStop != null && rawStop > 0
        ? rawStop
        : null;
  // Require a real entry+stop; hide WAITING provisional ladders (entry null → was shown as 0)
  const levelsActionable =
    entryPrice != null && stopPrice != null && planState !== "WAITING" && planState !== "CONFLICT";
  const riskPerUnit =
    levelsActionable
      ? (numOrNull(stop.risk_per_unit) ?? Math.abs(entryPrice! - stopPrice!))
      : null;

  const emptyLevel = (name: string): TradePlanLevel => ({
    label: name,
    price: null,
    rMultiple: null,
    display: "—",
  });
  const tp1 = levelsActionable ? pickTarget(targets, "TP1") : emptyLevel("TP1");
  const tp2 = levelsActionable ? pickTarget(targets, "TP2") : emptyLevel("TP2");
  const tp3 = levelsActionable ? pickTarget(targets, "TP3") : emptyLevel("TP3");

  const rrLines: string[] = [];
  if (levelsActionable) {
    const tp1R = numOrNull(rr.TP1_R) ?? tp1.rMultiple;
    const tp2R = numOrNull(rr.TP2_R) ?? tp2.rMultiple;
    const tp3R = numOrNull(rr.TP3_R) ?? tp3.rMultiple;
    rrLines.push(`TP1 ${tp1R != null ? fmtR(tp1R) : "—"}`);
    rrLines.push(`TP2 ${tp2R != null ? fmtR(tp2R) : "—"}`);
    rrLines.push(`TP3 ${tp3R != null ? fmtR(tp3R) : "—"}`);
  }

  // Re-bucket with evidence categories
  const withBuckets: TradeCondition[] = classified.map((c) => {
    const bucket = evidenceBucketFor(c);
    return { ...c, bucket: bucket === "directional" ? "confirm" : bucket };
  });

  const dataAvailable = buildDataAvailable({ conditions: withBuckets, deps });
  const dataIds = new Set(dataAvailable.map((d) => d.id));

  const directionalEvidence = withBuckets.filter((c) => {
    if (MTF_SECTION_IDS.has(c.id) || AVAILABILITY_ONLY_IDS.has(c.id)) return false;
    if (dataIds.has(c.id)) return false;
    return evidenceBucketFor(c) === "directional";
  });

  const missingEntryConfirmations = withBuckets
    .filter((c) => {
      if (MTF_SECTION_IDS.has(c.id) || AVAILABILITY_ONLY_IDS.has(c.id)) return false;
      return evidenceBucketFor(c) === "missing";
    })
    .map((c) => enrichMissingDetail(c, setupState));

  const failedEntryGates = withBuckets
    .filter((c) => {
      if (MTF_SECTION_IDS.has(c.id) || AVAILABILITY_ONLY_IDS.has(c.id)) return false;
      return evidenceBucketFor(c) === "fail";
    })
    .map((c) => failReasonFor(c, planState));

  const unavailableNotConfirmed = withBuckets.filter((c) => {
    if (AVAILABILITY_ONLY_IDS.has(c.id) && dataIds.has(c.id)) return false;
    // Soft availability that is LIVE already listed under Data Available
    if (AVAILABILITY_ONLY_IDS.has(c.id) && c.verdict === "PASS") return false;
    return evidenceBucketFor(c) === "na";
  });

  // Legacy aliases
  const confirmations = directionalEvidence;
  const missing = missingEntryConfirmations;
  const unavailable = unavailableNotConfirmed;
  const failures = failedEntryGates;

  const invalidation = String(
    stop.invalidation_reason ||
      analysis.invalidation_reason ||
      (planState === "INVALIDATED" ? "Setup invalidated by structure" : ""),
  );

  const why: string[] = [];
  for (const c of directionalEvidence) {
    why.push(`✓ ${c.label}${c.detail ? ` — ${c.detail}` : ""}`);
  }
  for (const c of missingEntryConfirmations) {
    why.push(`○ ${c.label}${c.detail ? ` — ${c.detail}` : ""}`);
  }
  for (const c of unavailableNotConfirmed) {
    if (CONTEXT_NA_IDS.has(c.id) && c.detail) {
      why.push(`N/A — ${c.label}: ${c.detail}`);
    } else if (CONTEXT_NA_IDS.has(c.id)) {
      why.push(`N/A — ${c.label}`);
    } else {
      why.push(`N/A — ${c.label} unavailable`);
    }
  }

  const mtfLines = mtfAlignment.summaryLines.length
    ? mtfAlignment.summaryLines.map((l) => l.replace(" ", ": "))
    : Object.entries(asRecord(mtf.trends || trend)).map(
        ([tf, v]) => `${String(tf).toUpperCase()}: ${String(v ?? "WAITING").toUpperCase()}`,
      );

  let lifecycleStep: TradePlanView["lifecycleStep"] = "WAIT";
  let lifecycleWaitingNote: string | null = null;
  let currentStage = "WAIT";
  if (planState === "WAITING") {
    lifecycleStep = "WAIT";
    currentStage = "WAIT";
  } else if (planState === "CONFLICT") {
    lifecycleStep = "SETUP";
    lifecycleWaitingNote = "WAITING FOR CONFIRMATION";
    currentStage = "SETUP / WAIT";
  } else if (planState === "NO_SETUP" || planState === "BUY_BIAS" || planState === "SELL_BIAS") {
    lifecycleStep = "SETUP";
    currentStage = "SETUP";
  } else if (planState === "LONG_ENTRY_CANDIDATE" || planState === "SHORT_ENTRY_CANDIDATE") {
    lifecycleStep = "CONFIRMATION";
    currentStage = "CONFIRMATION";
  } else if (planState === "ENTRY_READY") {
    lifecycleStep = "ENTRY";
    currentStage = "ENTRY";
  } else if (planState === "INVALIDATED") {
    lifecycleStep = "EXIT";
    currentStage = "EXIT";
  }

  const candleLifecycle = [
    { title: "CANDLE 1", detail: "Setup detected (structure / impulse / pullback)" },
    { title: "CANDLE 2", detail: "Confirmation required (retest / conditions)" },
    {
      title: "ENTRY",
      detail: "Only if existing entry engine conditions pass — never auto-executed",
    },
  ];

  const provisionalInvalidation: ProvisionalInvalidation | null =
    stopProvisional && rawStop != null && rawStop > 0 && !levelsActionable
      ? { price: rawStop, display: fmtPriceCompact(rawStop) }
      : null;

  const dataNotes: string[] = [];
  const seenNoteKeys = new Set<string>();
  const noteKey = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "");
  for (const [k, v] of Object.entries(deps)) {
    if (/WAIT|MISSING|UNAVAILABLE|N\/A/i.test(String(v))) {
      // Soft LIVE feeds already shown under Data Available — skip noise
      if (/^(oi|liquidations?)$/i.test(k) && /LIVE|STALE/i.test(String(v))) continue;
      seenNoteKeys.add(noteKey(k));
      dataNotes.push(`${k}: ${v}`);
    }
  }
  for (const c of unavailableNotConfirmed) {
    const key = noteKey(c.id || c.label);
    if ([...seenNoteKeys].some((s) => s.includes(key) || key.includes(s))) continue;
    seenNoteKeys.add(key);
    if (CONTEXT_NA_IDS.has(c.id)) {
      dataNotes.push(c.detail ? `${c.label}: N/A — ${c.detail}` : `${c.label}: N/A`);
    } else if (AVAILABILITY_ONLY_IDS.has(c.id)) {
      dataNotes.push(`${c.label}: N/A — data unavailable`);
    } else {
      dataNotes.push(`${c.label}: N/A — not confirmed`);
    }
  }

  return {
    symbol,
    timeframe,
    marketSignal,
    marketSignalDisplay: marketSignalDisplay(marketSignal),
    setupStatusRaw,
    planState,
    statusMessage: statusMessageFor(planState, { waitingReason }),
    statusWhy,
    direction,
    entry: {
      label: "ENTRY",
      price: levelsActionable ? entryPrice : null,
      rMultiple: null,
      display: levelsActionable && entryPrice != null ? fmtPrice(entryPrice) : "—",
    },
    stop: {
      label: "STOP LOSS",
      price: levelsActionable ? stopPrice : null,
      rMultiple: null,
      display: levelsActionable && stopPrice != null ? fmtPrice(stopPrice) : "—",
    },
    riskPerUnit,
    targets: [tp1, tp2, tp3],
    rrLines,
    confirmations,
    missing,
    unavailable,
    failures,
    directionalEvidence,
    dataAvailable,
    missingEntryConfirmations,
    failedEntryGates,
    unavailableNotConfirmed,
    mtfAlignment,
    provisionalInvalidation,
    levelsActionable,
    currentStage,
    invalidation: invalidation || "—",
    why,
    mtfLines,
    conflictDetail,
    lifecycleStep,
    lifecycleWaitingNote,
    candleLifecycle,
    dataNotes,
    sourceOnly: true,
  };
}
