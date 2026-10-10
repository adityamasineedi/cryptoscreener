import { useEffect, useState } from "react";
import {
  fetchGridStatus,
  runGridBacktest,
  runGridScan,
  runGridValidation,
  type GridBacktest,
  type GridConfigInput,
  type GridScanRow,
  type GridStatus,
} from "./client";
import { EquityCurve } from "./EquityCurve";
import { bookToJson, equityToCsv, fillsToCsv } from "./export";
import { GridChart } from "./GridChart";

type Tab = "scanner" | "configure" | "backtest";

const defaults: GridConfigInput = {
  symbol: "BTCUSDT",
  timeframe: "1h",
  grid_type: "arithmetic",
  range_low: 0,
  range_high: 0,
  grid_count: 8,
  capital: 1000,
  max_capital: 100000,
  fee_bps: 4,
  slippage_bps: 1,
  stop_price: 0,
  max_drawdown: 0.35,
  max_open_orders: 20,
  leverage: 1,
  paused: false,
  intrabar_path: "worst",
};

function pct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function num(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function GridTradingPanel() {
  const [status, setStatus] = useState<GridStatus | null>(null);
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState<Tab>("scanner");
  const [config, setConfig] = useState<GridConfigInput>(defaults);
  const [symbolsText, setSymbolsText] = useState("BTCUSDT, ETHUSDT");
  const [scanLimit, setScanLimit] = useState(400);
  const [rows, setRows] = useState<GridScanRow[]>([]);
  const [result, setResult] = useState<GridBacktest | null>(null);
  const [validation, setValidation] = useState<string>("");
  const [lookbackEnd, setLookbackEnd] = useState<number | string | null>(null);
  const [allowOverlap, setAllowOverlap] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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

  function patch(partial: Partial<GridConfigInput>) {
    setConfig((current) => ({ ...current, ...partial }));
  }

  async function onScan() {
    setBusy(true);
    setError(null);
    try {
      const symbols = symbolsText
        .split(/[\s,]+/)
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean);
      const body = await runGridScan({
        timeframe: config.timeframe,
        symbols,
        limit: scanLimit,
        fee_bps: config.fee_bps,
        slippage_bps: config.slippage_bps,
        grid_count: config.grid_count,
      });
      setRows(body.rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "scan failed");
    } finally {
      setBusy(false);
    }
  }

  function useRow(row: GridScanRow) {
    patch({
      symbol: row.symbol,
      timeframe: row.timeframe || config.timeframe,
      range_low: row.range_low ?? config.range_low,
      range_high: row.range_high ?? config.range_high,
      stop_price: row.stop_price ?? config.stop_price,
    });
    setLookbackEnd(row.window_end ?? null);
    setAllowOverlap(false);
    setTab("configure");
  }

  function download(filename: string, text: string, type: string) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function onBacktest() {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await runGridBacktest(config, {
          limit: scanLimit,
          after_time: lookbackEnd,
          allow_overlap: allowOverlap,
        }),
      );
      setTab("backtest");
    } catch (err) {
      setError(err instanceof Error ? err.message : "backtest failed");
    } finally {
      setBusy(false);
    }
  }

  async function onValidate() {
    setBusy(true);
    setError(null);
    try {
      const report = await runGridValidation(config, {
        limit: scanLimit,
        is_fraction: 0.7,
        train_bars: 80,
        test_bars: 40,
        grid_counts: [4, 8, 12],
        after_time: lookbackEnd,
        allow_overlap: allowOverlap,
      });
      setValidation(JSON.stringify(report, null, 2));
      setTab("backtest");
    } catch (err) {
      setError(err instanceof Error ? err.message : "validation failed");
    } finally {
      setBusy(false);
    }
  }

  if (!ready) {
    return (
      <section className="p-4 text-terminal-muted" data-testid="grid-trading">
        Loading grid trading…
      </section>
    );
  }

  if (!status?.enabled) {
    return (
      <section className="space-y-2 p-4 text-terminal-text" data-testid="grid-trading">
        <h1 className="font-display text-xl">Grid trading</h1>
        <p className="text-sm text-terminal-muted">
          Grid trading is off. Set GRID_TRADING_ENABLED to show this book.
        </p>
      </section>
    );
  }

  const summary = result?.summary;

  return (
    <section className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-terminal-text" data-testid="grid-trading">
      <header>
        <h1 className="font-display text-xl">Grid trading</h1>
        <p className="mt-1 rounded border border-terminal-border bg-terminal-panel px-3 py-2 text-sm text-terminal-muted">
          Backtest only. Not financial advice. Binance USDT-M futures, unleveraged long grid (
          {status.market}). Funding is excluded. Live orders are disabled.
        </p>
      </header>
      <div className="flex gap-2">
        {(["scanner", "configure", "backtest"] as Tab[]).map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setTab(name)}
            className={`rounded border px-3 py-1.5 text-sm capitalize ${
              tab === name
                ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                : "border-terminal-border text-terminal-muted"
            }`}
          >
            {name}
          </button>
        ))}
      </div>
      {error ? (
        <p className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm" role="alert">
          {error}
        </p>
      ) : null}

      {tab === "scanner" ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-terminal-muted">
              Symbols
              <input
                className="mt-1 block rounded border border-terminal-border bg-transparent px-2 py-1 text-sm"
                value={symbolsText}
                onChange={(event) => setSymbolsText(event.target.value)}
              />
            </label>
            <label className="text-xs text-terminal-muted">
              Timeframe
              <input
                className="mt-1 block w-20 rounded border border-terminal-border bg-transparent px-2 py-1 text-sm"
                value={config.timeframe}
                onChange={(event) => patch({ timeframe: event.target.value })}
              />
            </label>
            <label className="text-xs text-terminal-muted">
              Bars
              <input
                type="number"
                className="mt-1 block w-24 rounded border border-terminal-border bg-transparent px-2 py-1 text-sm"
                value={scanLimit}
                onChange={(event) => setScanLimit(Number(event.target.value))}
              />
            </label>
            <button
              type="button"
              disabled={busy}
              onClick={onScan}
              className="rounded bg-terminal-accent px-3 py-1.5 text-sm text-black disabled:opacity-50"
            >
              Scan
            </button>
          </div>
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-terminal-muted">
              <tr>
                <th className="py-1">Symbol</th>
                <th>Score</th>
                <th>Eligible</th>
                <th>Range</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.symbol} className="border-t border-terminal-border">
                  <td className="py-1">{row.symbol}</td>
                  <td>{num(row.score)}</td>
                  <td>{row.eligible ? "yes" : row.reason || "no"}</td>
                  <td>
                    {row.range_low != null && row.range_high != null
                      ? `${num(row.range_low, 4)} – ${num(row.range_high, 4)}`
                      : "—"}
                  </td>
                  <td>
                    <button type="button" className="text-terminal-accent" onClick={() => useRow(row)}>
                      Use
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {tab === "configure" ? (
        <div className="grid max-w-3xl grid-cols-2 gap-3 text-xs text-terminal-muted md:grid-cols-3">
          {(
            [
              ["symbol", "Symbol", "text"],
              ["timeframe", "Timeframe", "text"],
              ["range_low", "Range low", "number"],
              ["range_high", "Range high", "number"],
              ["grid_count", "Grid count", "number"],
              ["capital", "Capital", "number"],
              ["max_capital", "Max capital", "number"],
              ["fee_bps", "Fee bps", "number"],
              ["slippage_bps", "Slippage bps", "number"],
              ["stop_price", "Stop", "number"],
              ["max_drawdown", "Max drawdown", "number"],
              ["max_open_orders", "Max open orders", "number"],
              ["leverage", "Leverage", "number"],
            ] as const
          ).map(([key, label, kind]) => (
            <label key={key}>
              {label}
              <input
                type={kind}
                className="mt-1 block w-full rounded border border-terminal-border bg-transparent px-2 py-1 text-sm text-terminal-text"
                value={config[key]}
                onChange={(event) =>
                  patch({
                    [key]: kind === "number" ? Number(event.target.value) : event.target.value,
                  } as Partial<GridConfigInput>)
                }
              />
            </label>
          ))}
          <label>
            Grid type
            <select
              className="mt-1 block w-full rounded border border-terminal-border bg-transparent px-2 py-1 text-sm text-terminal-text"
              value={config.grid_type}
              onChange={(event) => patch({ grid_type: event.target.value as GridConfigInput["grid_type"] })}
            >
              <option value="arithmetic">arithmetic</option>
              <option value="geometric">geometric</option>
            </select>
          </label>
          <label>
            Intrabar path
            <select
              className="mt-1 block w-full rounded border border-terminal-border bg-transparent px-2 py-1 text-sm text-terminal-text"
              value={config.intrabar_path}
              onChange={(event) =>
                patch({ intrabar_path: event.target.value as GridConfigInput["intrabar_path"] })
              }
            >
              <option value="worst">worst</option>
              <option value="buy_then_sell">buy then sell</option>
              <option value="sell_then_buy">sell then buy</option>
            </select>
          </label>
          <label className="col-span-2 flex items-center gap-2 text-sm text-terminal-text md:col-span-3">
            <input
              type="checkbox"
              checked={Boolean(config.paused)}
              onChange={(event) => patch({ paused: event.target.checked })}
            />
            Pause / kill (no new fills; inventory marked to market)
          </label>
          {lookbackEnd != null ? (
            <div className="col-span-2 space-y-2 text-sm md:col-span-3">
              <p className="text-terminal-muted">
                Scanner window ends at {String(lookbackEnd)}. The backtest starts after that bar.
              </p>
              <label className="flex items-center gap-2 text-terminal-text">
                <input
                  type="checkbox"
                  checked={allowOverlap}
                  onChange={(event) => setAllowOverlap(event.target.checked)}
                />
                Allow overlap with the scanner window
              </label>
              {allowOverlap ? (
                <p className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2" role="alert">
                  This backtest overlaps the scanner lookback that chose the range.
                </p>
              ) : null}
            </div>
          ) : null}
          <div className="col-span-2 flex items-end gap-2 md:col-span-3">
            <button
              type="button"
              disabled={busy}
              onClick={onBacktest}
              className="rounded bg-terminal-accent px-3 py-1.5 text-sm text-black disabled:opacity-50"
            >
              Run backtest
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onValidate}
              className="rounded border border-terminal-border px-3 py-1.5 text-sm text-terminal-text disabled:opacity-50"
            >
              Validate
            </button>
          </div>
        </div>
      ) : null}

      {tab === "backtest" ? (
        <div className="space-y-3">
          {summary ? (
            <>
              <p className="text-sm text-terminal-muted">
                {summary.accepted ? "Accepted" : `Rejected (${summary.error})`} · {summary.market} ·{" "}
                {summary.bar_count} bars · fill rate {pct(summary.fill_rate)} · max drawdown{" "}
                {pct(summary.max_drawdown)} · grid {pct(summary.return)} · buy & hold{" "}
                {pct(summary.buy_and_hold_return)}
              </p>
              {(result?.warnings ?? summary.warnings ?? []).map((warning) => (
                <p key={warning} className="text-sm text-amber-200" role="status">
                  {warning}
                </p>
              ))}
              <table className="w-full text-left text-sm">
                <tbody>
                  {(
                    [
                      ["Fees paid", num(summary.fees_paid, 4)],
                      ["Realized PnL", num(summary.realized_pnl, 4)],
                      ["Unrealized PnL", num(summary.unrealized_pnl, 4)],
                      ["Net PnL", num(summary.net_pnl, 4)],
                      ["Time in range", pct(summary.pct_time_in_range)],
                      ["Avg profit / cycle", num(summary.avg_profit_per_grid_cycle, 4)],
                      ["Fill rate", pct(summary.fill_rate)],
                    ] as const
                  ).map(([label, value]) => (
                    <tr key={label} className="border-t border-terminal-border">
                      <th className="py-1 pr-3 font-normal text-terminal-muted">{label}</th>
                      <td>{value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(summary.events ?? []).length ? (
                <ul className="text-sm text-terminal-muted">
                  {summary.events?.map((event, index) => (
                    <li key={`${event.type}-${event.time}-${index}`}>
                      {event.type} at {String(event.time ?? "")}
                      {event.price != null ? ` (${num(event.price, 4)})` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
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
                  onClick={() =>
                    download("grid-equity.csv", equityToCsv(result?.equity_curve ?? []), "text/csv")
                  }
                >
                  Download equity CSV
                </button>
                <button
                  type="button"
                  className="rounded border border-terminal-border px-3 py-1.5 text-sm"
                  onClick={() =>
                    download(
                      "grid-book.json",
                      bookToJson(result?.fills ?? [], result?.equity_curve ?? []),
                      "application/json",
                    )
                  }
                >
                  Download JSON
                </button>
              </div>
              <EquityCurve points={result?.equity_curve ?? []} />
              <GridChart
                candles={result?.candles ?? []}
                levels={result?.levels ?? []}
                stopPrice={config.stop_price}
                fills={result?.fills ?? []}
              />
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase text-terminal-muted">
                  <tr>
                    <th className="py-1">Time</th>
                    <th>Side</th>
                    <th>Price</th>
                    <th>Qty</th>
                    <th>Fee</th>
                  </tr>
                </thead>
                <tbody>
                  {(result?.fills ?? []).slice(0, 200).map((fill, index) => (
                    <tr key={`${fill.time}-${index}`} className="border-t border-terminal-border">
                      <td className="py-1">{String(fill.time ?? "")}</td>
                      <td>{fill.side}</td>
                      <td>{num(fill.price, 4)}</td>
                      <td>{num(fill.qty, 6)}</td>
                      <td>{num(fill.fee, 4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p className="text-sm text-terminal-muted">Run a backtest from Configure.</p>
          )}
          {validation ? (
            <>
              <p className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm" role="alert">
                Parameter sweeps overfit. Rank is the share of nearby grid counts (±20%) and range
                widths (±10%) with positive net PnL, not the single best PnL.
              </p>
              <pre className="max-h-64 overflow-auto rounded border border-terminal-border p-3 text-xs text-terminal-muted">
                {validation}
              </pre>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
