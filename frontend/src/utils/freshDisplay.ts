import type { DataStatus, FreshValue, ScreenerRow } from "../types/market";

/** Human lane text that matches the badge — never hardcode "LIVE stream". */
export function statusLaneLabel(
  status: DataStatus | string,
  kind: "price" | "volume" | "fundamental" | "performance" | "generic" = "generic",
): string {
  const s = String(status || "WAITING").toUpperCase();
  if (kind === "performance") {
    return s === "HISTORICAL" ? "from 1D candles (not live)" : s.toLowerCase();
  }
  if (kind === "price") {
    if (s === "LIVE") return "ticker / mark stream";
    if (s === "STALE") return "last known ticker (stale)";
    if (s === "UNAVAILABLE") return "last known (feed timed out)";
    if (s === "WAITING") return "waiting for ticker";
    if (s === "CACHED") return "cached ticker";
  }
  if (kind === "volume") {
    if (s === "LIVE") return "24h quote volume (ticker)";
    if (s === "STALE") return "last known 24h quote volume (stale)";
    if (s === "UNAVAILABLE") return "last known 24h quote volume (feed timed out)";
    if (s === "WAITING") return "waiting for volume";
  }
  if (kind === "fundamental") {
    if (s === "LIVE" || s === "CACHED") return "provider fundamentals";
    if (s === "STALE") return "provider fundamentals (stale)";
    if (s === "UNAVAILABLE") return "not available from provider";
    if (s === "WAITING") return "waiting for provider";
  }
  return s.toLowerCase();
}

/** Format volume/mcap style ratios: prefer percent when < 1. */
export function formatRatio(v: number | string, asPercentPreferred = true): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  if (asPercentPreferred && Math.abs(n) < 1) {
    return `${(n * 100).toFixed(2)}%`;
  }
  if (Math.abs(n) >= 100) return n.toFixed(2);
  if (Math.abs(n) >= 1) return n.toFixed(4);
  return n.toFixed(6);
}

/** TVL: zero is not a meaningful DeFi claim — show as N/A. */
export function normalizeTvlFresh(fv: FreshValue | undefined): FreshValue | undefined {
  if (!fv) return fv;
  if (fv.value === null || fv.value === undefined) return fv;
  if (Number(fv.value) === 0) {
    return {
      ...fv,
      value: null,
      status: "UNAVAILABLE",
      methodology:
        fv.methodology ||
        "N/A — zero / not applicable (no DeFi TVL for this asset)",
    };
  }
  return fv;
}

export function isLastKnownStatus(status: string): boolean {
  return status === "STALE" || status === "UNAVAILABLE";
}

/** Prefer existing LIVE field when snapshot is older / degraded. */
export function preferFresherFresh<T>(
  prev: FreshValue<T> | undefined,
  next: FreshValue<T> | undefined,
): FreshValue<T> | undefined {
  if (!next) return prev;
  if (!prev) return next;
  if (prev.value == null) return next;
  if (next.value == null && prev.value != null && prev.status === "LIVE") return prev;

  const prevTs = prev.timestamp ? Date.parse(prev.timestamp) : 0;
  const nextTs = next.timestamp ? Date.parse(next.timestamp) : 0;

  // Keep client LIVE tick if it is newer than REST snapshot
  if (prev.status === "LIVE" && prevTs >= nextTs) {
    return prev;
  }
  // Don't let a timed-out UNAVAILABLE snapshot wipe a newer LIVE value
  if (
    prev.status === "LIVE" &&
    (next.status === "UNAVAILABLE" || next.status === "STALE") &&
    prevTs + 5000 >= nextTs
  ) {
    return prev;
  }
  return next;
}

const STREAM_FIELDS = [
  "price",
  "change_24h_pct",
  "volume_24h",
  "quote_volume_24h",
  "high_24h",
  "low_24h",
  "funding_rate",
] as const;

export function mergeScreenerRowPreferLive(
  prev: ScreenerRow | undefined,
  next: ScreenerRow,
): ScreenerRow {
  if (!prev) return next;
  const out: ScreenerRow = { ...next };
  for (const key of STREAM_FIELDS) {
    const p = prev[key] as FreshValue | undefined;
    const n = next[key] as FreshValue | undefined;
    const merged = preferFresherFresh(p, n);
    if (merged) {
      (out as unknown as Record<string, FreshValue>)[key] = merged;
    }
  }
  return out;
}
