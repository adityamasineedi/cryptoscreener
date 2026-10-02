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
  market_signal: 110,
  trend: 100,
  setup_signal: 180,
  setup_entry: 110,
  setup_sl: 110,
  setup_tp1: 110,
  setup_rr: 75,
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
