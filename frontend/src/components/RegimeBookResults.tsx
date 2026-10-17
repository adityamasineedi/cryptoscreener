import type { RegimeBacktestStatus } from "../api/client";

function money(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function day(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "…";
}

function requestedLabel(job: RegimeBacktestStatus): string {
  const req = job.window?.requested;
  if (!req) return "—";
  if (req.mode === "dates") return `Calendar ${req.start_date || "…"} → ${req.end_date || "…"} (UTC, end inclusive)`;
  return `Latest ${(req.limit ?? 0).toLocaleString()} ${req.timeframe || "1h"} bars`;
}

/** Results of the regime-book run. The panel only shows the job it is given. */
export function RegimeBookResults({ job }: { job: RegimeBacktestStatus | null }) {
  if (!job || job.status === "idle") return null;
  const summary = job.summary;
  const evaluated = job.window?.evaluated;
  const cap = job.window?.cap;
  const fixed = job.fixed_config;
  return (
    <section className="mb-4 rounded border border-emerald-500/30 bg-emerald-500/5 p-3" data-testid="regime-backtest">
      <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">Regime book results · current run</h2>
      <p className="mt-1 text-[12px] text-terminal-text">
        BTC daily trend picks the side on each closed 1h bar. Bearish runs the passed shorts. Bullish runs
        COMBO_03_TRANSITION_B long. Neutral opens nothing.
      </p>
      <p className="mt-1 font-mono text-[11px] text-terminal-muted" data-testid="regime-backtest-status">
        {job.status}
        {job.current ? ` · ${job.current}` : ""}
        {job.error ? ` · ${job.error}` : ""}
      </p>
      <div className="mt-1 font-mono text-[11px] text-terminal-text" data-testid="regime-backtest-period">
        <div>Requested: {requestedLabel(job)}</div>
        <div>
          Evaluated: {evaluated?.period_start ? `${day(evaluated.period_start)} → ${day(evaluated.period_end)} UTC` : "…"}
          {evaluated?.bars_evaluated != null
            ? ` · ${evaluated.bars_evaluated.toLocaleString()} 1h bars evaluated · ${(evaluated.warmup_bars ?? 0).toLocaleString()} warm-up bars not traded`
            : ""}
          {job.symbols?.length ? ` · ${job.symbols.length} coin${job.symbols.length === 1 ? "" : "s"}` : ""}
        </div>
        {cap?.applied ? (
          <div className="text-amber-200" data-testid="regime-backtest-cap">
            Cap applied: only the latest {(cap.max_eval_bars ?? 0).toLocaleString()} 1h bars were evaluated for{" "}
            {(cap.symbols || []).join(", ")}.
          </div>
        ) : null}
        {job.skipped_symbols?.length ? (
          <div className="text-amber-200">Skipped (no 1h candles in the window): {job.skipped_symbols.join(", ")}</div>
        ) : null}
        {fixed ? (
          <div className="text-terminal-muted">
            Book sizing: principal ${money(fixed.principal_usd)} · leverage {fixed.leverage ?? "—"}× · taker{" "}
            {fixed.taker_fee_pct ?? "—"}% / maker {fixed.maker_fee_pct ?? "—"}%
          </div>
        ) : null}
      </div>
      {summary ? (
        <>
          <div className="mt-2 flex flex-wrap gap-3 text-[12px]">
            <span>Trades {summary.trades ?? 0}</span>
            <span>Wins {summary.wins ?? 0}</span>
            <span>Losses {summary.losses ?? 0}</span>
            <span>Net ${money(summary.pnl_usd_net)}</span>
            <span>Equity ${money(summary.final_equity_usd)}</span>
            <span>Max DD {summary.max_drawdown_pct == null ? "—" : `${summary.max_drawdown_pct.toFixed(1)}%`}</span>
          </div>
          <table className="mt-2 w-full text-left text-[12px]">
            <thead className="text-[10px] uppercase tracking-wide text-terminal-muted">
              <tr>
                <th className="px-2 py-1">Strategy</th>
                <th className="px-2 py-1">Side</th>
                <th className="px-2 py-1">Trades</th>
                <th className="px-2 py-1">Net $</th>
              </tr>
            </thead>
            <tbody>
              {(summary.by_strategy || []).map((row) => (
                <tr key={`${row.strategy_id}-${row.direction}`} className="border-t border-terminal-border">
                  <td className="px-2 py-1 font-mono">{row.strategy_id}</td>
                  <td className="px-2 py-1">{row.direction}</td>
                  <td className="px-2 py-1">{row.trades ?? 0}</td>
                  <td className="px-2 py-1 font-mono">{money(row.pnl_usd_net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </section>
  );
}
