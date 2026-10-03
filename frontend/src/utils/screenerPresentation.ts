import type { FreshValue, ScreenerRow } from "../types/market";

/** Concise WAIT / N/A cell labels from backend methodology (presentation only). */
export function shortDependencyLabel(
  methodology?: string | null,
  status?: string | null,
): string {
  const st = String(status || "").toUpperCase();
  if (st === "UNAVAILABLE") return "N/A";

  const m = (methodology || "").trim();
  if (!m) return st === "WAITING" ? "WAITING" : st === "UNAVAILABLE" ? "N/A" : "WAITING";

  const head = m.split("—")[0]?.trim() || m;
  const requires = head.match(/^Requires\s+(.+)$/i);
  if (requires?.[1]) {
    let dep = requires[1].trim();
    dep = dep.replace(/\s+coverage\.?$/i, "");
    dep = dep.replace(/\s+data\.?$/i, "");
    dep = dep.replace(/^confirmed\s+/i, "");
    // Normalize common phrases to short tokens
    if (/15\s*m\s*ohlcv/i.test(dep)) return "WAIT — 15M OHLCV";
    if (/1\s*h\s*ohlcv/i.test(dep)) return "WAIT — 1H OHLCV";
    if (/4\s*h\s*ohlcv/i.test(dep)) return "WAIT — 4H OHLCV";
    if (/ohlcv/i.test(dep)) return "WAIT — OHLCV";
    if (/market\s*structure|structure/i.test(dep)) return "WAIT — Structure";
    if (/volume|rvol/i.test(dep)) return "WAIT — Volume";
    if (/supply\/?demand|zone/i.test(dep)) return "WAIT — Zone";
    if (/liquidation/i.test(dep)) return "WAITING";
    if (/open\s*interest|\boi\b/i.test(dep)) return "WAIT — OI";
    // Keep short remainder
    const short = dep.length > 28 ? `${dep.slice(0, 25)}…` : dep;
    return `WAIT — ${short}`;
  }

  if (/WAITING\s+FOR\s+OHLCV/i.test(m)) return "WAIT — OHLCV";
  if (/WAITING\s+FOR\s+15M/i.test(m)) return "WAIT — 15M OHLCV";
  if (/insufficient/i.test(m)) return "INSUFFICIENT DATA";
  if (/^N\/A\b/i.test(m) || /not applicable/i.test(m)) return "N/A";

  return st === "UNAVAILABLE" ? "N/A" : "WAITING";
}

/** Compact empty-cell label for screener tables. */
export function compactEmptyLabel(fv: FreshValue | undefined): string {
  if (!fv) return "WAITING";
  return shortDependencyLabel(fv.methodology, fv.status);
}

/** Full explanation for tooltips — never invent. */
export function dependencyTooltip(
  fv: FreshValue | undefined,
  fallback?: string,
): string {
  if (!fv) return fallback || "Waiting for data";
  const parts: string[] = [];
  if (fv.methodology) parts.push(fv.methodology);
  parts.push(`Status: ${fv.status}`);
  if (fv.source) parts.push(`Source: ${fv.source}`);
  return parts.join("\n");
}

export function liquidationTooltip(fv: FreshValue | undefined): string {
  const st = String(fv?.status || "WAITING").toUpperCase();
  if (st === "WAITING" || st === "UNAVAILABLE") {
    if (fv?.methodology) return fv.methodology;
    return "Liquidation feed is currently unavailable.";
  }
  return dependencyTooltip(fv);
}

/** Presentation mapping for entry/exit / setup states. */
export function formatSetupStateLabel(raw: unknown): string {
  const s = String(raw ?? "").toUpperCase().trim();
  switch (s) {
    case "NO_SETUP":
      return "NO SETUP";
    case "LONG_ENTRY_CANDIDATE":
      return "LONG CANDIDATE";
    case "SHORT_ENTRY_CANDIDATE":
      return "SHORT CANDIDATE";
    case "ENTRY_READY":
      return "ENTRY READY";
    case "INVALIDATED":
      return "INVALIDATED";
    case "CONFLICT":
      return "CONFLICT";
    case "WAITING":
      return "WAITING";
    case "BUY_BIAS":
      return "BUY BIAS";
    case "SELL_BIAS":
      return "SELL BIAS";
    case "":
      return "WAITING";
    default:
      return s.replace(/_/g, " ");
  }
}

export function formatMarketSignalLabel(raw: unknown): string {
  const s = String(raw ?? "").toUpperCase().trim();
  if (s === "STRONG_BUY") return "STRONG BUY";
  if (s === "STRONG_SELL") return "STRONG SELL";
  return s || "WAITING";
}

/** Trade-view STATUS summary from existing setup/market fields. */
export function tradeStatusLabel(row: ScreenerRow): { text: string; title: string } {
  const setupFv = row.setup_signal;
  const setup = String(setupFv?.value ?? "").toUpperCase();
  const method = setupFv?.methodology || undefined;

  if (!setup || setup === "WAITING") {
    const text = shortDependencyLabel(method, setupFv?.status || "WAITING");
    return {
      text: text === "WAITING" ? "WAITING" : text,
      title: method || "Waiting for setup engine",
    };
  }
  if (setup === "NO_SETUP") {
    return { text: "WAIT — no setup", title: method || "NO_SETUP" };
  }
  if (setup === "CONFLICT") {
    return { text: "CONFLICT", title: method || "CONFLICT" };
  }
  if (setup === "INVALIDATED") {
    return { text: "INVALIDATED", title: method || "INVALIDATED" };
  }
  if (setup === "ENTRY_READY") {
    return { text: "ENTRY READY", title: method || "ENTRY_READY" };
  }
  if (setup === "LONG_ENTRY_CANDIDATE") {
    return { text: "LONG CANDIDATE", title: method || setup };
  }
  if (setup === "SHORT_ENTRY_CANDIDATE") {
    return { text: "SHORT CANDIDATE", title: method || setup };
  }
  return {
    text: formatSetupStateLabel(setup),
    title: method || setup,
  };
}

/** True when Entry/SL/TP/R:R should show an em dash. */
export function isMissingLevel(fv: FreshValue | undefined): boolean {
  if (!fv || fv.value === null || fv.value === undefined) return true;
  const n = Number(fv.value);
  if (Number.isFinite(n)) return false;
  const s = String(fv.value).toUpperCase();
  return s === "" || s === "WAITING" || s === "N/A" || s === "NONE";
}

export function formatCompactNumber(v: number | string): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
  if (abs >= 1) return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return n.toPrecision(4);
}

export function formatPriceCell(v: number | string): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  if (n >= 1000) return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  if (n >= 1) return `$${n.toLocaleString(undefined, { maximumFractionDigits: 4 })}`;
  return `$${n.toPrecision(4)}`;
}

export function formatPctCell(v: number | string): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

export function formatFundingCell(v: number | string): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(4)}%`;
}

/** Normalize tech rating display — never show raw Requires… as cell text. */
export function formatTechRatingDisplay(fv: FreshValue | undefined): {
  text: string;
  title: string;
} {
  if (!fv || fv.value === null || fv.value === undefined) {
    return {
      text: compactEmptyLabel(fv),
      title: dependencyTooltip(fv),
    };
  }
  const raw = String(fv.value);
  if (/^Requires\b/i.test(raw) || raw.toUpperCase() === "WAITING") {
    return {
      text: shortDependencyLabel(fv.methodology || raw, fv.status),
      title: fv.methodology || raw,
    };
  }
  if (/INSUFFICIENT/i.test(raw)) {
    return { text: "INSUFFICIENT DATA", title: fv.methodology || raw };
  }
  return { text: raw, title: dependencyTooltip(fv) };
}

export type SignalFilter = "ALL" | "BUY" | "SELL" | "NEUTRAL" | "WAITING";
export type SetupFilter =
  | "ALL"
  | "ENTRY_READY"
  | "LONG_ENTRY_CANDIDATE"
  | "SHORT_ENTRY_CANDIDATE"
  | "NO_SETUP"
  | "CONFLICT"
  | "WAITING";

export function matchesSignalFilter(row: ScreenerRow, filter: SignalFilter): boolean {
  if (filter === "ALL") return true;
  const s = String(row.market_signal?.value ?? "WAITING").toUpperCase();
  if (filter === "BUY") return s === "BUY" || s === "STRONG_BUY";
  if (filter === "SELL") return s === "SELL" || s === "STRONG_SELL";
  if (filter === "NEUTRAL") return s === "NEUTRAL";
  if (filter === "WAITING") return s === "WAITING" || s === "";
  return true;
}

export function matchesSetupFilter(row: ScreenerRow, filter: SetupFilter): boolean {
  if (filter === "ALL") return true;
  const s = String(row.setup_signal?.value ?? "WAITING").toUpperCase();
  if (filter === "WAITING") return s === "WAITING" || s === "";
  return s === filter;
}

export function shouldResetTableScroll(
  prevMode: "trade" | "technical",
  nextMode: "trade" | "technical",
): boolean {
  return prevMode !== nextMode;
}

export const TRADE_COL_IDS = [
  "symbol",
  "price",
  "change",
  "market_signal",
  "trend",
  "setup_signal",
  "v1_status",
  "setup_entry",
  "setup_sl",
  "setup_tp1",
  "setup_rr",
  "status",
] as const;

export const TECHNICAL_PINNED_IDS = ["symbol", "price", "market_signal", "setup_signal"] as const;

export const TECHNICAL_CORE_IDS = [
  "rating",
  "trend",
  "setup_bos",
  "impulse",
  "pullback",
  "v1_status",
  "setup_entry",
  "setup_sl",
  "setup_tp1",
  "setup_rr",
] as const;

export const TECHNICAL_MARKET_IDS = [
  "mcap",
  "fdv",
  "vol",
  "oi",
  "funding",
  "rvol",
  "structure",
  "zone",
  "liq",
  "entry",
] as const;

export const COL_WIDTHS: Record<string, number> = {
  symbol: 100,
  price: 148,
  change: 85,
  market_signal: 120,
  trend: 110,
  setup_signal: 140,
  v1_status: 200,
  setup_entry: 130,
  setup_sl: 130,
  setup_tp1: 130,
  setup_rr: 110,
  status: 190,
  rating: 120,
  setup_bos: 80,
  impulse: 90,
  pullback: 100,
  mcap: 110,
  fdv: 110,
  vol: 110,
  oi: 100,
  funding: 100,
  rvol: 100,
  structure: 150,
  zone: 150,
  liq: 130,
  entry: 150,
};

export const POTENTIAL_LEVELS_TOOLTIP =
  "Reference levels generated by the general screener. " +
  "They are not active orders, not paper positions, and not Telegram eligible. " +
  "They may exist before setup confirmation.";

export const SCREEN_STATE_LEGEND_SHORT: Array<{ state: string; meaning: string }> = [
  { state: "NEUTRAL", meaning: "No actionable screen direction" },
  { state: "BULLISH local trend", meaning: "Local structure bullish — not a v1 entry" },
  { state: "WAITING", meaning: "Setup not completed / no confirmed entry" },
  { state: "CONFLICT", meaning: "Screener conditions disagree" },
  { state: "DISCOVERY ONLY", meaning: "Excluded from frozen COMBO_02 v1" },
  { state: "V1 ELIGIBLE", meaning: "Separate 1h/4h watcher confirmed entry" },
  { state: "V1 PAPER OPEN", meaning: "Persisted v1 paper trade exists" },
];

export const V1_DIFFERS_HELP = [
  "Trend = local market-structure classification.",
  "Signal = current directional/actionability classification.",
  "Setup = state of the screener’s potential setup.",
  "",
  "BULLISH trend does not mean a v1 LONG is allowed.",
  "COMBO_02 v1 requires:",
  "• Symbol approved for v1",
  "• Closed 1h bullish setup structure",
  "• Confirmed 1h BULLISH_BOS",
  "• Closed 4h and 1h BULLISH",
  "• HTF_ALIGNED",
  "• Path A only",
].join("\n");

/** Fail-closed: never treat a 15m screen row as V1 ELIGIBLE. */
export function displayV1Status(row: ScreenerRow): {
  text: string;
  title: string;
  tone: "eligible" | "open" | "waiting" | "blocked" | "discovery" | "unavailable";
} {
  const vs = row.v1_status;
  if (!vs) {
    return {
      text: "V1 DATA UNAVAILABLE",
      title: "No v1 watcher snapshot attached to this row.",
      tone: "unavailable",
    };
  }
  const code = String(vs.status || "").toUpperCase();
  const text = vs.label || code.replace(/_/g, " ");
  const title = vs.tooltip || text;

  // Safety: never green-light from screen fields alone
  if (code === "V1_ELIGIBLE") {
    // Only trust explicit API status — still display as eligible (read-only)
    return { text, title, tone: "eligible" };
  }
  if (code === "V1_PAPER_OPEN") return { text, title, tone: "open" };
  if (code === "DISCOVERY_ONLY") return { text: "DISCOVERY ONLY", title, tone: "discovery" };
  if (code === "V1_DATA_UNAVAILABLE") return { text: "V1 DATA UNAVAILABLE", title, tone: "unavailable" };
  if (code.startsWith("V1_BLOCKED")) return { text, title, tone: "blocked" };
  if (code.startsWith("V1_WAITING")) return { text, title, tone: "waiting" };
  return { text, title, tone: "unavailable" };
}

export function v1StatusClass(tone: ReturnType<typeof displayV1Status>["tone"]): string {
  switch (tone) {
    case "eligible":
      return "text-emerald-300";
    case "open":
      return "text-sky-300";
    case "blocked":
      return "text-amber-300";
    case "waiting":
      return "text-terminal-muted";
    case "discovery":
      return "text-terminal-muted/80";
    case "unavailable":
    default:
      return "text-amber-200/80";
  }
}

export function potentialLevelsForRow(row: ScreenerRow): {
  entry: number | null;
  stop: number | null;
  tp1: number | null;
  rr: number | null;
  referenceOnly: boolean;
  confirmed: boolean;
  tooltip: string;
} {
  const pl = row.potential_levels;
  if (pl) {
    return {
      entry: pl.entry,
      stop: pl.stop,
      tp1: pl.tp1,
      rr: pl.rr,
      referenceOnly: Boolean(pl.reference_only ?? !pl.is_confirmed),
      confirmed: Boolean(pl.is_confirmed),
      tooltip: pl.tooltip || POTENTIAL_LEVELS_TOOLTIP,
    };
  }
  const setup = String(row.setup_signal?.value ?? "").toUpperCase();
  const entry = isMissingLevel(row.setup_entry) ? null : Number(row.setup_entry?.value);
  const stop = isMissingLevel(row.setup_sl) ? null : Number(row.setup_sl?.value);
  const tp1 = isMissingLevel(row.setup_tp1) ? null : Number(row.setup_tp1?.value);
  const rr = isMissingLevel(row.setup_rr) ? null : Number(row.setup_rr?.value);
  const unconfirmed =
    setup === "" ||
    setup === "WAITING" ||
    setup === "CONFLICT" ||
    setup === "NO_SETUP" ||
    entry == null;
  return {
    entry: Number.isFinite(entry as number) ? entry : null,
    stop: Number.isFinite(stop as number) ? stop : null,
    tp1: Number.isFinite(tp1 as number) ? tp1 : null,
    rr: Number.isFinite(rr as number) ? rr : null,
    referenceOnly: unconfirmed,
    confirmed: !unconfirmed,
    tooltip:
      POTENTIAL_LEVELS_TOOLTIP +
      (unconfirmed ? " Reference only — setup not confirmed." : ""),
  };
}

export function formatPotentialLevel(
  value: number | null,
  opts: {
    kind: "entry" | "stop" | "tp1" | "rr";
    referenceOnly: boolean;
    entryMissing: boolean;
  },
): { text: string; muted: boolean; title: string } {
  const { kind, referenceOnly, entryMissing } = opts;
  const title =
    POTENTIAL_LEVELS_TOOLTIP +
    (referenceOnly ? " Reference only — setup not confirmed." : "");
  if (value == null || !Number.isFinite(value)) {
    return { text: "—", muted: true, title };
  }
  const formatted =
    kind === "rr" ? value.toFixed(2) : formatPriceCell(value).replace(/^\$/, "");
  if (kind === "entry" && (entryMissing || value == null)) {
    return { text: "—", muted: true, title };
  }
  if (referenceOnly || entryMissing) {
    if (kind === "entry") {
      return { text: "—", muted: true, title };
    }
    return {
      text: `candidate ${formatted}`,
      muted: true,
      title,
    };
  }
  return { text: formatted, muted: false, title };
}

export function stickyOffsets(pinnedIds: readonly string[]): Record<string, number> {
  let left = 0;
  const out: Record<string, number> = {};
  for (const id of pinnedIds) {
    out[id] = left;
    left += COL_WIDTHS[id] ?? 100;
  }
  return out;
}

export function futuresColumnIds(mode: "trade" | "technical"): string[] {
  if (mode === "trade") return [...TRADE_COL_IDS];
  return [...TECHNICAL_PINNED_IDS, ...TECHNICAL_CORE_IDS, ...TECHNICAL_MARKET_IDS];
}
