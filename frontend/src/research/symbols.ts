/** Shared research / backtest / OHLCV symbol helpers. */

/** Quick-pick chips — v1 books first, then common liquid alts. */
export const RESEARCH_SYMBOL_PRESETS = [
  "BTCUSDT",
  "ETHUSDT",
  "SOLUSDT",
  "BNBUSDT",
  "XRPUSDT",
  "DOGEUSDT",
  "ADAUSDT",
  "AVAXUSDT",
  "LINKUSDT",
  "DOTUSDT",
  "LTCUSDT",
  "NEARUSDT",
  "APTUSDT",
  "SUIUSDT",
  "ARBUSDT",
  "OPUSDT",
] as const;

/** Normalize a single user token to SYMBOLUSDT (or null if invalid). */
export function normalizeResearchSymbol(raw: string): string | null {
  let s = String(raw || "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
  if (!s) return null;
  if (!s.endsWith("USDT")) s = `${s}USDT`;
  if (s === "USDT" || !/^[A-Z0-9]+USDT$/.test(s)) return null;
  return s;
}

/** Parse comma / whitespace separated symbols (URL or free-form input). */
export function parseSymbolList(raw: string | null | undefined): string[] {
  if (!raw) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const part of String(raw).split(/[,\s]+/)) {
    const s = normalizeResearchSymbol(part);
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}

export function mergeSymbols(current: string[], add: string[]): string[] {
  const seen = new Set(current);
  const out = [...current];
  for (const s of add) {
    if (!seen.has(s)) {
      seen.add(s);
      out.push(s);
    }
  }
  return out;
}
