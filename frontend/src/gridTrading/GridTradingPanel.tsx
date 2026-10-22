import { useEffect, useState } from "react";
import { BACKTEST_LARGE_SYMBOLS } from "../backtest/v1Config";
import {
  fetchGridStatus,
  runGridBacktest,
  runGridScan,
  type GridBacktest,
  type GridConfigInput,
  type GridFill,
  type GridScanRow,
  type GridStatus,
} from "./client";
import { EquityCurve } from "./EquityCurve";
import { bookToJson, equityToCsv, fillsToCsv } from "./export";
import { GridChart } from "./GridChart";

const TIMEFRAMES = ["15m", "1h", "4h", "1d"] as const;
const LEVERAGES = [1, 2, 3, 4, 5] as const;

/** Locked research defaults. The range, stop, and spacing come from the OHLCV scan. */
const locked: Omit<
  GridConfigInput,
  "symbol" | "timeframe" | "range_low" | "range_high" | "stop_price" | "leverage" | "max_open_grids"
> = {
  grid_type: "geometric",
  grid_count: 8,
  capital: 1000,
  max_capital: 100000,
  fee_bps: 2,
  slippage_bps: 1,
  max_drawdown: 0.35,
  max_open_orders: 20,
  exit_above_range: true,
  paused: false,
  funding_mode: "charge",
  intrabar_path: "worst",
};

type GridTrade = {
  slot: number | null;
  entryTime: GridFill["time"] | null;
  exitTime: GridFill["time"] | null;
  entry: number | null;
  exit: number | null;
  qty: number;
  fees: number;
  net: number | null;
  outcome: string;
};

function pct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function num(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function px(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
  return value.toFixed(digits);
}

function stamp(value: GridFill["time"] | null | undefined): string {
  if (value == null || value === "") return "—";
  const raw = typeof value === "number" ? value : Date.parse(String(value));
  if (!Number.isFinite(raw)) return String(value);
  const ms = raw > 1e12 ? raw : raw * 1000;
  return new Date(ms).toISOString().replace("T", " ").slice(0, 16);
}

/** Pair each slot's buy with the next sell. Unmatched buys stay open. */
export function gridTrades(fills: GridFill[]): GridTrade[] {
  const open = new Map<string, GridFill[]>();
  const trades: GridTrade[] = [];
  for (const fill of fills) {
    const key = fill.slot == null ? "flat" : String(fill.slot);
    if (fill.side === "buy") {
      const stack = open.get(key) ?? [];
      stack.push(fill);
      open.set(key, stack);
      continue;
    }
    const stack = open.get(key) ?? [];
    const entry = stack.shift();
    if (!stack.length) open.delete(key);
    if (entry) {
      const fees = (entry.fee || 0) + (fill.fee || 0);
      const gross = (fill.price - entry.price) * entry.qty;
      trades.push({
        slot: fill.slot,
        entryTime: entry.time,
        exitTime: fill.time,
        entry: entry.price,
        exit: fill.price,
        qty: entry.qty,
        fees,
        net: gross - fees,
        outcome: fill.reason || "grid",
      });
    } else {
      trades.push({
        slot: fill.slot,
        entryTime: null,
        exitTime: fill.time,
        entry: null,
        exit: fill.price,
        qty: fill.qty,
        fees: fill.fee || 0,
        net: null,
        outcome: fill.reason || "exit",
      });
    }
  }
  for (const stack of open.values()) {
    for (const entry of stack) {
      trades.push({
        slot: entry.slot,
        entryTime: entry.time,
        exitTime: null,
        entry: entry.price,
        exit: null,
        qty: entry.qty,
        fees: entry.fee || 0,
        net: null,
        outcome: "open",
      });
    }
  }
  return trades;
}

export function GridTradingPanel({ embedded = false }: { embedded?: boolean }) {
  const [status, setStatus] = useState<GridStatus | null>(null);
  const [ready, setReady] = useState(false);
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>("1h");
  const [bars, setBars] = useState(400);
  const [leverage, setLeverage] = useState<number>(2);
  const [maxOpenGrids, setMaxOpenGrids] = useState<number>(locked.grid_count);
  const [picked, setPicked] = useState<GridScanRow | null>(null);
  const [result, setResult] = useState<GridBacktest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [largeCaps, setLargeCaps] = useState<GridScanRow[]>([]);
  const [largeCapNote, setLargeCapNote] = useState<string | null>(null);

  useEffect(() => {
    if (!status?.enabled) return;
    let alive = true;
    setLargeCapNote("Loading large caps…");
    runGridScan({
      timeframe,
      symbols: [...BACKTEST_LARGE_SYMBOLS],
      limit: bars,
      fee_bps: locked.fee_bps,
      slippage_bps: locked.slippage_bps,
      grid_count: locked.grid_count,
    })
      .then((body) => {
        if (!alive) return;
        const rows = [...(body.rows || [])].sort((a, b) => (b.score || 0) - (a.score || 0));
        setLargeCaps(rows);
        const end = rows.find((row) => row.window_end)?.window_end;
        setLargeCapNote(end ? `Latest bar ${stamp(end)} UTC` : null);
      })
      .catch((err) => {
        if (!alive) return;
        setLargeCaps([]);
        setLargeCapNote(err instanceof Error ? err.message : "Large-cap scan failed");
      });
    return () => {
      alive = false;
    };
  }, [status?.enabled, timeframe, bars]);

  useEffect(() => {
    let alive = true;
    fetchGridStatus()
      .then((body) => {
        if (alive) setStatus(body);
      })
      .catch(() => {
        if (alive) setStatus(null);
      })
      .finally(() => {
        if (alive) setReady(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  function download(filename: string, text: string, type: string) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function onBacktest() {
    const nextSymbol = symbol.trim().toUpperCase();
    if (!nextSymbol) {
      setError("Enter a symbol.");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const scan = await runGridScan({
        timeframe,
        symbols: [nextSymbol],
        limit: bars,
        fee_bps: locked.fee_bps,
        slippage_bps: locked.slippage_bps,
        grid_count: locked.grid_count,
      });
      const row = scan.rows.find((item) => item.symbol === nextSymbol) ?? scan.rows[0];
      if (!row || row.range_low == null || row.range_high == null || row.stop_price == null) {
        setPicked(row ?? null);
        setError(row?.reason ? `No grid range from OHLCV (${row.reason}).` : "No grid range from OHLCV.");
        return;
      }
      const config: GridConfigInput = {
        ...locked,
        symbol: nextSymbol,
        timeframe,
        range_low: row.range_low,
        range_high: row.range_high,
        stop_price: row.stop_price,
        leverage,
        max_open_grids: maxOpenGrids,
      };
      setPicked(row);
      setResult(await runGridBacktest(config, { limit: bars }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "backtest failed");
    } finally {
      setBusy(false);
    }
  }

  if (!ready) {
    return (
      <section className={embedded ? "text-terminal-muted" : "p-4 text-terminal-muted"} data-testid="grid-trading">
        Loading grid trading…
      </section>
    );
  }

  if (!status?.enabled) {
    return (
      <section className={embedded ? "space-y-2 text-terminal-text" : "space-y-2 p-4 text-terminal-text"} data-testid="grid-trading">
        <h1 className="font-display text-xl">Grid trading</h1>
        <p className="text-sm text-terminal-muted">
          Grid trading is off. Set GRID_TRADING_ENABLED to show this book.
        </p>
      </section>
    );
  }

  const summary = result?.summary;
  const trades = gridTrades(result?.fills ?? []);
  const netLabel =
    summary?.funding_status === "unavailable" || summary?.funding_status === "incomplete"
      ? "PnL before funding"
      : "Net PnL";
  const netValue =
    summary?.funding_status === "unavailable" || summary?.funding_status === "incomplete"
      ? summary?.pnl_before_funding
      : summary?.net_pnl;

  return (
    <section className={embedded ? "space-y-4 text-terminal-text" : "min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-terminal-text"} data-testid="grid-trading">
      <header>
        <h1 className="font-display text-xl">Grid backtest</h1>
        <p className="mt-1 max-w-3xl text-xs text-terminal-muted">
          Binance USDT-M futures, long grid at 1x–5x ({status.market}). Range, stop, and 8 geometric
          levels are taken from stored OHLCV. Live orders stay off. Not financial advice.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-terminal-muted">
          Symbol
          <input
            aria-label="Symbol"
            className="mt-1 block w-36 rounded border border-terminal-border bg-transparent px-2 py-1 font-mono text-sm uppercase text-terminal-text"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
          />
        </label>
        <label className="text-xs text-terminal-muted">
          Timeframe
          <select
            aria-label="Timeframe"
            className="mt-1 block rounded border border-terminal-border bg-terminal-bg px-2 py-1 text-sm text-terminal-text"
            value={timeframe}
            onChange={(event) => setTimeframe(event.target.value as (typeof TIMEFRAMES)[number])}
          >
            {TIMEFRAMES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-terminal-muted">
          Bars
          <input
            aria-label="Bars"
            type="number"
            min={30}
            className="mt-1 block w-24 rounded border border-terminal-border bg-transparent px-2 py-1 text-sm text-terminal-text"
            value={bars}
            onChange={(event) => setBars(Number(event.target.value))}
          />
        </label>
        <label className="text-xs text-terminal-muted">
          Leverage
          <select
            aria-label="Leverage"
            className="mt-1 block rounded border border-terminal-border bg-terminal-bg px-2 py-1 text-sm text-terminal-text"
            value={leverage}
            onChange={(event) => setLeverage(Number(event.target.value))}
          >
            {LEVERAGES.map((item) => (
              <option key={item} value={item}>
                {item}x
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-terminal-muted">
          Max open grids
          <input
            aria-label="Max open grids"
            type="number"
            min={1}
            max={locked.grid_count}
            className="mt-1 block w-20 rounded border border-terminal-border bg-transparent px-2 py-1 text-sm text-terminal-text"
            value={maxOpenGrids}
            onChange={(event) => setMaxOpenGrids(Number(event.target.value))}
          />
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={onBacktest}
          className="rounded bg-terminal-accent px-3 py-1.5 text-sm text-black disabled:opacity-50"
        >
          {busy ? "Running…" : "Run backtest"}
        </button>
      </div>

      <div data-testid="grid-large-caps">
        <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold text-terminal-text">Large caps</h2>
          <span className="font-mono text-[10px] text-terminal-muted">{largeCapNote || "—"}</span>
        </div>
        <div className="overflow-auto rounded border border-terminal-border/60">
          <table className="w-full min-w-[720px] border-collapse text-left text-[11px]">
            <thead className="bg-black/80 text-[10px] uppercase tracking-wide text-terminal-muted">
              <tr>
                <th className="px-2 py-2">Coin</th>
                <th className="px-2 py-2">Score</th>
                <th className="px-2 py-2">Last close</th>
                <th className="px-2 py-2">Grid low</th>
                <th className="px-2 py-2">Grid high</th>
                <th className="px-2 py-2">Stop</th>
                <th className="px-2 py-2">In range</th>
              </tr>
            </thead>
            <tbody>
              {largeCaps.length ? (
                largeCaps.map((row) => (
                  <tr
                    key={row.symbol}
                    className="cursor-pointer border-t border-terminal-border/70 hover:bg-white/[0.03]"
                    onClick={() => setSymbol(row.symbol)}
                  >
                    <td className="px-2 py-1.5 font-mono">{row.symbol.replace("USDT", "")}</td>
                    <td className="px-2 py-1.5 font-mono">{num(row.score)}</td>
                    <td className="px-2 py-1.5 font-mono">{px(row.last_close)}</td>
                    <td className="px-2 py-1.5 font-mono">{px(row.range_low)}</td>
                    <td className="px-2 py-1.5 font-mono">{px(row.range_high)}</td>
                    <td className="px-2 py-1.5 font-mono">{px(row.stop_price)}</td>
                    <td className="px-2 py-1.5">
                      {row.range_low == null ? row.reason || "—" : row.inside_grid ? "Yes" : "No"}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={7} className="px-3 py-4 text-center text-terminal-muted">
                    {largeCapNote || "No large-cap scan yet"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {error ? (
        <p className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm" role="alert">
          {error}
        </p>
      ) : null}

      {picked?.range_low != null && picked.range_high != null ? (
        <div className="grid gap-2 rounded border border-terminal-border/80 bg-black/20 px-3 py-2 text-xs term-md:grid-cols-4" data-testid="grid-auto-range">
          <div>
            <div className="text-terminal-muted">Recommended range</div>
            <div className="font-mono text-terminal-text">
              {px(picked.range_low)} – {px(picked.range_high)}
            </div>
          </div>
          <div>
            <div className="text-terminal-muted">Stop</div>
            <div className="font-mono">{px(picked.stop_price)}</div>
          </div>
          <div>
            <div className="text-terminal-muted">Grid</div>
            <div className="font-mono">
              geometric · {locked.grid_count} levels · ${locked.capital} margin · {leverage}x
            </div>
          </div>
          <div>
            <div className="text-terminal-muted">Scan</div>
            <div className="font-mono">
              score {num(picked.score)} · {picked.eligible ? "eligible" : picked.reason || "filtered"}
            </div>
          </div>
        </div>
      ) : null}

      {summary ? (
        <div className="space-y-3">
          <p className="text-sm text-terminal-muted">
            {summary.accepted ? "Accepted" : `Rejected (${summary.error})`} · {summary.market} ·{" "}
            {summary.bar_count} bars
          </p>
          {(result?.warnings ?? summary.warnings ?? []).map((warning) => (
            <p key={warning} className="text-sm text-amber-200" role="status">
              {warning}
            </p>
          ))}
          <div className="grid gap-2 term-md:grid-cols-4">
            {(
              [
                [netLabel, num(netValue, 4)],
                ["Realized PnL", num(summary.realized_pnl, 4)],
                ["Unrealized PnL", num(summary.unrealized_pnl, 4)],
                ["Fees paid", num(summary.fees_paid, 4)],
                ["Funding paid", num(summary.funding_paid, 4)],
                ["Funding coverage", pct(summary.funding_coverage_pct)],
                ["Grid return", pct(summary.return)],
                ["Buy & hold", pct(summary.buy_and_hold_return)],
                ["Max drawdown", pct(summary.max_drawdown)],
                ["Fill rate", pct(summary.fill_rate)],
                ["Round trips", num(summary.round_trips, 0)],
                ["Time in range", pct(summary.pct_time_in_range)],
                ["Leverage", `${num(summary.leverage ?? 1, 0)}x · ${num(summary.max_open_grids, 0)} open max`],
                ["Liquidation (worst)", px(summary.worst_case_liquidation_price)],
                ["Stop hits", num(summary.stop_hits ?? 0, 0)],
                ["Take-profit exits", num(summary.take_profit_exits ?? 0, 0)],
                ["Liquidations", num(summary.liquidations ?? 0, 0)],
                ["Drawdown kills", num(summary.drawdown_kills ?? 0, 0)],
                ["Blocked buys", num(summary.blocked_buys ?? 0, 0)],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="rounded border border-terminal-border/80 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-terminal-muted">{label}</div>
                <div className="font-mono text-sm">{value}</div>
              </div>
            ))}
          </div>
          <EquityCurve points={result?.equity_curve ?? []} />
          <GridChart
            candles={result?.candles ?? []}
            levels={result?.levels ?? []}
            stopPrice={picked?.stop_price ?? 0}
            fills={result?.fills ?? []}
          />

          <div className="overflow-auto rounded border border-terminal-border/60" data-testid="grid-trade-blotter">
            <table className="w-full min-w-[860px] border-collapse text-left text-[11px]">
              <thead className="sticky top-0 bg-black/80 text-[10px] uppercase tracking-wide text-terminal-muted">
                <tr>
                  <th className="px-2 py-2">#</th>
                  <th className="px-2 py-2">Slot</th>
                  <th className="px-2 py-2">Entry time</th>
                  <th className="px-2 py-2">Exit time</th>
                  <th className="px-2 py-2">Side</th>
                  <th className="px-2 py-2">Entry</th>
                  <th className="px-2 py-2">Exit</th>
                  <th className="px-2 py-2">Qty</th>
                  <th className="px-2 py-2">Fees</th>
                  <th className="px-2 py-2">Net</th>
                  <th className="px-2 py-2">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {trades.length ? (
                  trades.map((trade, index) => (
                    <tr key={`${trade.slot}-${trade.entryTime}-${index}`} className="border-t border-terminal-border/70">
                      <td className="px-2 py-1.5 font-mono">{index + 1}</td>
                      <td className="px-2 py-1.5 font-mono">{trade.slot ?? "—"}</td>
                      <td className="px-2 py-1.5 font-mono">{stamp(trade.entryTime)}</td>
                      <td className="px-2 py-1.5 font-mono">{stamp(trade.exitTime)}</td>
                      <td className="px-2 py-1.5">buy</td>
                      <td className="px-2 py-1.5 font-mono">{px(trade.entry)}</td>
                      <td className="px-2 py-1.5 font-mono">{px(trade.exit)}</td>
                      <td className="px-2 py-1.5 font-mono">{num(trade.qty, 6)}</td>
                      <td className="px-2 py-1.5 font-mono">{num(trade.fees, 4)}</td>
                      <td className={`px-2 py-1.5 font-mono ${trade.net != null && trade.net < 0 ? "text-red-300" : "text-emerald-300"}`}>
                        {num(trade.net, 4)}
                      </td>
                      <td className="px-2 py-1.5">{trade.outcome}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={11} className="px-3 py-6 text-center text-terminal-muted">
                      No grid trades in this window
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded border border-terminal-border px-3 py-1.5 text-sm"
              onClick={() => download("grid-fills.csv", fillsToCsv(result?.fills ?? []), "text/csv")}
            >
              Download fills CSV
            </button>
            <button
              type="button"
              className="rounded border border-terminal-border px-3 py-1.5 text-sm"
              onClick={() => download("grid-equity.csv", equityToCsv(result?.equity_curve ?? []), "text/csv")}
            >
              Download equity CSV
            </button>
            <button
              type="button"
              className="rounded border border-terminal-border px-3 py-1.5 text-sm"
              onClick={() =>
                download("grid-book.json", bookToJson(result?.fills ?? [], result?.equity_curve ?? []), "application/json")
              }
            >
              Download JSON
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
