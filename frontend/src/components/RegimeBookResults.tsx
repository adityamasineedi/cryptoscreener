import { useEffect, useState } from "react";
import { fetchRegimeBacktest, type RegimeBacktestStatus } from "../api/client";

function money(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function RegimeBookResults({ active }: { active: boolean }) {
  const [job, setJob] = useState<RegimeBacktestStatus | null>(null);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    const poll = async () => {
      try {
        const next = await fetchRegimeBacktest();
        if (alive) setJob(next);
      } catch {
        /* The run button reports start errors. */
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 2000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [active]);

  if (!active || !job || job.status === "idle") return null;
  const summary = job.summary;
  return (
    <section className="mb-4 rounded border border-emerald-500/30 bg-emerald-500/5 p-3" data-testid="regime-backtest">
      <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">Regime book results</h2>
      <p className="mt-1 text-[12px] text-terminal-text">
        BTC daily trend picks the side on each closed 1h bar. Bearish runs the passed shorts. Bullish runs
        COMBO_03_TRANSITION_B long. Neutral opens nothing.
      </p>
      <p className="mt-1 font-mono text-[11px] text-terminal-muted" data-testid="regime-backtest-status">
        {job.status}
        {job.current ? ` · ${job.current}` : ""}
        {job.error ? ` · ${job.error}` : ""}
      </p>
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
