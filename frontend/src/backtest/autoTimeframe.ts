/** Choose one setup timeframe from stored OHLCV coverage.

Preference when coverage ties: 1h (screener setup), then 4h, then 15m.
Returns 1h when coverage is empty so the frozen default stays in place.
*/

export const AUTO_SETUP_PREFERENCE = ["1h", "4h", "15m"] as const;

export type CoverageBarRow = {
  symbol: string;
  timeframe: string;
  bars?: number | null;
};

const MIN_BARS = 50;

export function pickAutoSetupTimeframe(
  rows: CoverageBarRow[],
  symbols: string[],
): string {
  const wanted = new Set(symbols.map((s) => s.toUpperCase()));
  const covered = new Map<string, number>();
  for (const row of rows) {
    const symbol = String(row.symbol || "").toUpperCase();
    const timeframe = String(row.timeframe || "");
    if (!wanted.has(symbol)) continue;
    if (!AUTO_SETUP_PREFERENCE.includes(timeframe as (typeof AUTO_SETUP_PREFERENCE)[number])) {
      continue;
    }
    if ((Number(row.bars) || 0) < MIN_BARS) continue;
    covered.set(timeframe, (covered.get(timeframe) || 0) + 1);
  }

  let best: string = AUTO_SETUP_PREFERENCE[0];
  let bestCount = -1;
  for (const tf of AUTO_SETUP_PREFERENCE) {
    const count = covered.get(tf) || 0;
    if (count > bestCount) {
      best = tf;
      bestCount = count;
    }
  }
  return bestCount > 0 ? best : "1h";
}
