import type { FreshValue } from "../types/market";
import { isLastKnownStatus } from "../utils/freshDisplay";
import {
  compactEmptyLabel,
  shortDependencyLabel,
} from "../utils/screenerPresentation";

const colors: Record<string, string> = {
  LIVE: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  HISTORICAL: "bg-violet-500/20 text-violet-300 border-violet-500/40",
  CACHED: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  STALE: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  UNAVAILABLE: "bg-rose-500/20 text-rose-300 border-rose-500/40",
  WAITING: "bg-slate-500/20 text-slate-300 border-slate-500/40",
};

/** Lane hint: LIVE market stream vs HISTORICAL backfill vs CACHED fundamentals */
export function dataLane(status: string, source?: string): string {
  if (status === "HISTORICAL") return "HISTORICAL";
  if (status === "LIVE") return "LIVE";
  if (status === "CACHED") return "CACHED";
  if (source === "ohlcv_store" || source === "binance_rest") return status;
  return status;
}

const SOURCE_LABELS: Record<string, string> = {
  binance_ws: "Binance Futures WebSocket",
  binance_rest: "Binance Futures REST",
  coingecko: "CoinGecko",
  defillama: "DefiLlama",
  calc: "Local calculation",
  mtf_engine: "MTF engine",
  volume_engine: "Volume engine",
  market_structure: "Market structure engine",
  supply_demand: "Supply/Demand engine",
  tech_rating: "Tech rating",
  entry_exit_engine: "Entry/Exit engine",
};

function ago(ts: string | null | undefined): string {
  if (!ts) return "n/a";
  const ms = Date.now() - new Date(ts).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "n/a";
  if (ms < 1000) return `${ms.toFixed(0)} ms ago`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} seconds ago`;
  if (ms < 3_600_000) return `${(ms / 60_000).toFixed(1)} minutes ago`;
  return `${(ms / 3_600_000).toFixed(1)} hours ago`;
}

function tooltip(fv: FreshValue): string {
  const source = SOURCE_LABELS[fv.source] || fv.source || "unknown";
  const method = fv.methodology ? `\nMethod: ${fv.methodology}` : "";
  return `Source: ${source}\nUpdated: ${ago(fv.timestamp)}\nStatus: ${fv.status}${method}`;
}

/**
 * Human reason from methodology for empty/waiting cells.
 * Prefer the real blocker (mapping missing, needs OHLCV, etc.) over "Waiting for live data…".
 */
export function dependencyLabel(methodology?: string | null): string | null {
  if (!methodology) return null;
  const m = methodology.trim();
  if (!m) return null;

  // "holder_count: no asset/chain/contract mapping…" → keep the reason clause
  const colon = m.match(/^[^:]+:\s*(.+)$/);
  if (colon?.[1]) {
    const reason = colon[1].split("—")[0]?.trim() || colon[1].trim();
    if (reason) return reason;
  }

  if (/^Requires\b/i.test(m)) {
    return m.split("—")[0]?.trim() || m;
  }
  if (
    /insufficient history|unavailable|not configured|requires configured|not listed|asset_not_covered|no asset|never fabricated|providers\.yaml|assets\.yaml/i.test(
      m,
    )
  ) {
    // Prefer the actionable clause after an em-dash when present
    const parts = m.split("—").map((p) => p.trim()).filter(Boolean);
    const head =
      parts.length > 1 && /requires|never|not |no /i.test(parts[parts.length - 1] || "")
        ? parts[parts.length - 1]
        : parts[0] || m;
    return head.length > 140 ? `${head.slice(0, 137)}…` : head;
  }
  if (/INSUFFICIENT/i.test(m) && /Requires\b/i.test(m)) {
    const req = m.match(/Requires[^.]+/i);
    return req ? req[0].trim() : "INSUFFICIENT DATA";
  }
  return m.length > 120 ? `${m.slice(0, 117)}…` : m;
}

export function emptyFreshLabel(
  fv: FreshValue | undefined,
  compact = false,
): string {
  // Compact table cells: short WAITING / N/A / WAIT — X (full reason in tooltip)
  if (compact) return compactEmptyLabel(fv);

  const dep = dependencyLabel(fv?.methodology);
  if (fv?.status === "WAITING") {
    if (dep) return dep;
    return "Waiting for data…";
  }
  if (fv?.status === "UNAVAILABLE") {
    if (dep) return dep;
    return "Data unavailable";
  }
  if (dep) return dep;
  return "Data unavailable";
}

export function StatusBadge({
  status,
  compact = false,
}: {
  status: FreshValue["status"] | string;
  compact?: boolean;
}) {
  const short: Record<string, string> = {
    UNAVAILABLE: "UNAV",
    WAITING: "WAIT",
    HISTORICAL: "HIST",
    CACHED: "CACH",
    INSUFFICIENT_DATA: "INSUF",
  };
  const label = compact ? short[String(status)] ?? String(status) : String(status);
  return (
    <span
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-[10px] font-medium tracking-wide ${colors[status] ?? colors.WAITING}`}
      title={String(status)}
    >
      {label}
    </span>
  );
}

export function FreshCell({
  fv,
  format,
  className = "",
  showBadge = false,
  compact = false,
}: {
  fv: FreshValue | undefined;
  format?: (v: number | string) => string;
  className?: string;
  showBadge?: boolean;
  /** Table cells: truncate long waiting/unavailable labels */
  compact?: boolean;
}) {
  if (!fv || fv.value === null || fv.value === undefined) {
    const label = emptyFreshLabel(fv, compact);
    const status = fv?.status ?? "WAITING";
    return (
      <span
        className={`inline-flex max-w-full items-center gap-1 text-xs text-terminal-muted ${className}`}
        title={fv ? tooltip(fv) : label}
      >
        <span className="min-w-0 truncate">{label}</span>
        {showBadge ? <StatusBadge status={status} compact={compact} /> : null}
      </span>
    );
  }
  const raw = String(fv.value);
  // Compact tables: never show raw "Requires …" as the cell text
  if (compact && (/^Requires\b/i.test(raw) || raw.toUpperCase() === "INSUFFICIENT_DATA")) {
    const label =
      raw.toUpperCase() === "INSUFFICIENT_DATA"
        ? "INSUFFICIENT DATA"
        : shortDependencyLabel(fv.methodology || raw, fv.status);
    return (
      <span
        className={`inline-flex max-w-full items-center gap-1 text-xs text-terminal-muted ${className}`}
        title={tooltip(fv)}
      >
        <span className="min-w-0 truncate">{label}</span>
        {showBadge ? <StatusBadge status={fv.status} compact /> : null}
      </span>
    );
  }

  // INSUFFICIENT_DATA is a truthful value — keep it, annotate via methodology
  const text = format ? format(fv.value) : raw;
  const dep =
    fv.status === "WAITING" || String(fv.value) === "INSUFFICIENT_DATA"
      ? dependencyLabel(fv.methodology)
      : null;
  const lastKnown = isLastKnownStatus(fv.status);
  return (
    <span
      className={`inline-flex max-w-full flex-col items-start gap-0.5 font-mono text-sm ${className}`}
      title={tooltip(fv)}
    >
      <span className="inline-flex max-w-full items-center gap-1">
        <span
          className={`${
            compact && showBadge
              ? "shrink-0 whitespace-nowrap"
              : compact
                ? "min-w-0 whitespace-nowrap"
                : "min-w-0 truncate"
          } ${lastKnown ? "opacity-90" : ""}`}
        >
          {text}
        </span>
        {showBadge ? <StatusBadge status={fv.status} compact={compact} /> : null}
      </span>
      {!compact && lastKnown ? (
        <span className="truncate text-[9px] font-sans text-amber-200/80">
          Last known value — feed not currently {fv.status === "STALE" ? "fresh" : "live"}
        </span>
      ) : null}
      {!compact && dep ? (
        <span className="truncate text-[9px] font-sans text-terminal-muted">{dep}</span>
      ) : null}
    </span>
  );
}
