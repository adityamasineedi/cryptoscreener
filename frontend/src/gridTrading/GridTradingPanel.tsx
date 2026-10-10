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
    setTab("configure");
  }

  async function onBacktest() {
    setBusy(true);
    setError(null);
    try {
      setResult(await runGridBacktest(config, { limit: scanLimit }));
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
        <p className="mt-1 text-sm text-terminal-muted">
          Backtest only on Binance USDT-M futures candles ({status.market}). Live orders are
          disabled. This book is separate from paper trading.
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
            <pre className="max-h-64 overflow-auto rounded border border-terminal-border p-3 text-xs text-terminal-muted">
              {validation}
            </pre>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
