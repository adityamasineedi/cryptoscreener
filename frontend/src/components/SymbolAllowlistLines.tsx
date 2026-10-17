import type { PassedRegimeSide, PassedSymbolAllowlist } from "../api/client";

/** "BTCUSDT" -> "BTC" for the compact lines. */
export function coinLabel(symbol: string): string {
  return symbol.endsWith("USDT") && symbol.length > 4 ? symbol.slice(0, -4) : symbol;
}

function sideList(rows: PassedRegimeSide[]): string {
  return rows.length ? rows.map((row) => `${row.strategy_id} ${row.direction}`).join(", ") : "none";
}

/** The two lines shown for one coin with an allowlist entry. */
export function allowlistLines(
  symbol: string,
  entry: { allowed_sides: PassedRegimeSide[]; blocked_sides: PassedRegimeSide[] },
): { allowed: string; blocked: string } {
  const coin = coinLabel(symbol);
  return {
    allowed: `Allowed sides on ${coin}: ${sideList(entry.allowed_sides)}`,
    blocked: `Blocked on ${coin} (failed ${coin} test): ${sideList(entry.blocked_sides)}`,
  };
}

function day(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "…";
}

/**
 * Per-coin allowlist, one compact block per coin with an entry. Coins without
 * an entry run every passed side and are not listed.
 */
export function SymbolAllowlistLines({
  allowlist,
  testId = "symbol-allowlist",
}: {
  allowlist: PassedSymbolAllowlist | null | undefined;
  testId?: string;
}) {
  if (!allowlist) return null;
  if (allowlist.applied === false) {
    return (
      <p className="mt-1 text-[11px] text-amber-200" data-testid={testId}>
        Per-coin allowlist off: every side in scope ran on every coin.
      </p>
    );
  }
  const symbols = Object.keys(allowlist.symbols || {}).sort();
  if (!symbols.length) return null;
  return (
    <div className="mt-1 space-y-1 font-mono text-[11px]" data-testid={testId}>
      {symbols.map((symbol) => {
        const entry = allowlist.symbols[symbol];
        const lines = allowlistLines(symbol, entry);
        const window = entry.tested_window;
        return (
          <div key={symbol} data-testid={`${testId}-${symbol}`}>
            <div className="text-emerald-300">{lines.allowed}</div>
            <div className="text-terminal-muted">
              {lines.blocked}
              {window?.start || entry.decided_at
                ? ` · tested ${day(window?.start)} → ${day(window?.end)}, decided ${day(entry.decided_at)}`
                : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}
