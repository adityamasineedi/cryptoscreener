import type { OhlcvRangeRow } from "../api/client";

/** Hard cap the backtest API applies to `limit`. */
export const API_BAR_CAP = 20000;
/** Used for Max when the DB bar count for the selection is not known. */
export const FALLBACK_MAX_BARS = 12200;

export type MaxLookback = {
  bars: number;
  source: "db" | "api-cap" | "fallback";
  /** Smallest stored bar count among the selected cells; null when a cell has no row. */
  dbBars: number | null;
};

/**
 * How many bars Max can request for the selected symbol × timeframe cells.
 * The run applies one N to every cell, so the smallest cell decides. Only bars before the
 * seal (2026-07-15) count when the coverage row reports them (`sealed_bars`).
 */
export function maxLookbackBars(
  coverage: OhlcvRangeRow[],
  symbols: string[],
  timeframes: string[],
): MaxLookback {
  const fallback = (dbBars: number | null): MaxLookback => ({
    bars: FALLBACK_MAX_BARS,
    source: "fallback",
    dbBars,
  });
  if (!symbols.length || !timeframes.length) return fallback(null);

  const byCell = new Map<string, number>();
  for (const row of coverage) {
    // Max ends at the seal (owner D202): only the bars before it count.
    byCell.set(`${row.symbol}|${row.timeframe}`, Number(row.sealed_bars ?? row.bars));
  }
  let min = Infinity;
  for (const symbol of symbols) {
    for (const timeframe of timeframes) {
      const bars = byCell.get(`${symbol}|${timeframe}`);
      if (bars == null || !Number.isFinite(bars)) return fallback(null);
      min = Math.min(min, bars);
    }
  }
  if (min <= 0) return fallback(0);
  if (min > API_BAR_CAP) return { bars: API_BAR_CAP, source: "api-cap", dbBars: min };
  return { bars: min, source: "db", dbBars: min };
}

/** "BTC/1h 2022-10-13→2026-10-12 · 35,040 bars" */
export function coverageRowLabel(row: OhlcvRangeRow): string {
  const day = (value: string | null | undefined) => (value ? value.slice(0, 10) : "?");
  const bars = Number.isFinite(Number(row.bars)) ? Number(row.bars) : 0;
  return `${row.symbol.replace("USDT", "")}/${row.timeframe} ${day(row.start)}→${day(row.end)} · ${bars.toLocaleString("en-US")} bars`;
}
