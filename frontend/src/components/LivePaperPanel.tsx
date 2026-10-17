import { useEffect, useState } from "react";
import {
  fetchPaperPositions,
  fetchPassedRegime,
  type PaperPosition,
  type PaperStatus,
  type PassedRegimeResponse,
} from "../api/client";
import { liveAutoStrategies, splitLivePaperRows } from "../paper/livePaper";

const POLL_MS = 15000;

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function LiveRows({ rows }: { rows: PaperPosition[] }) {
  if (!rows.length) {
    return <p className="text-[12px] text-terminal-muted">None.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-[12px]">
        <thead className="text-[10px] uppercase tracking-wide text-terminal-muted">
          <tr>
            <th className="px-2 py-1">Symbol</th>
            <th className="px-2 py-1">Strategy</th>
            <th className="px-2 py-1">Side</th>
            <th className="px-2 py-1">Opened</th>
            <th className="px-2 py-1">Stop → entry → TP1</th>
            <th className="px-2 py-1">Result</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const snippet = row.signal_snippet || {};
            const strategy = String(snippet.strategy_id || row.strategy_id || "—");
            const closed = String(row.status || "").toUpperCase() !== "OPEN";
            return (
              <tr key={row.id} className="border-t border-terminal-border" data-testid="live-paper-trade">
                <td className="px-2 py-1.5 font-mono">{row.symbol}</td>
                <td className="px-2 py-1.5 font-mono">{strategy}</td>
                <td className="px-2 py-1.5">{row.side}</td>
                <td className="px-2 py-1.5 whitespace-nowrap">{fmtTime(row.opened_at)}</td>
                <td className="px-2 py-1.5 font-mono whitespace-nowrap">
                  {fmt(row.stop_price, 4)} → {fmt(row.entry_price, 4)} → {fmt(row.tp1_price, 4)}
                </td>
                <td className="px-2 py-1.5 font-mono">
                  {closed ? `${row.exit_reason || row.status} ${fmt(row.pnl_usd)}` : fmt(row.unrealized_pnl_usd)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function LivePaperPanel() {
  const [status, setStatus] = useState<PaperStatus | null>(null);
  const [regime, setRegime] = useState<PassedRegimeResponse | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;

    const poll = async () => {
      if (controller) return;
      controller = new AbortController();
      try {
        const [book, passed] = await Promise.all([
          fetchPaperPositions(100),
          fetchPassedRegime(controller.signal),
        ]);
        if (!active) return;
        setStatus(book.status);
        setRegime(passed);
        setPositions([...(book.open || []), ...(book.closed || [])]);
        setError(null);
      } catch (err) {
        if (!active || (err instanceof DOMException && err.name === "AbortError")) return;
        setError(err instanceof Error ? err.message : "Live paper status unavailable");
      } finally {
        controller = null;
      }
    };

    void poll();
    const interval = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      active = false;
      window.clearInterval(interval);
      controller?.abort();
    };
  }, []);

  const book = liveAutoStrategies(status, regime);
  const { open, closed, cancelled } = splitLivePaperRows(positions);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4 text-terminal-text">
      <header className="border-b border-terminal-border pb-3">
        <h1 className="font-display text-lg tracking-tight">Live paper</h1>
        <p className="mt-1 max-w-3xl text-[12px] text-terminal-muted">
          Strategies that open paper trades on their own. Research setups, 15m chances, and
          observation books are not on this page. No exchange orders are sent.
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
          <span
            className={`rounded border px-1.5 py-0.5 ${
              status?.enabled
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-terminal-border text-terminal-muted"
            }`}
            data-testid="live-paper-auto"
          >
            Paper auto {status == null ? "…" : status.enabled ? "ON" : "OFF"}
          </span>
          <span className="rounded border border-terminal-border px-1.5 py-0.5 text-terminal-muted">
            Daily trend {regime?.trend || "—"}
          </span>
          <span className="rounded border border-terminal-border px-1.5 py-0.5 font-mono text-terminal-muted">
            {book.rows.length} auto strategies
          </span>
        </div>
        {error ? (
          <p className="mt-2 text-[12px] text-rose-300" data-testid="live-paper-error">
            {error}
          </p>
        ) : null}
      </header>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Allowed to auto-trade
        </h2>
        {book.rows.length ? (
          <div className="overflow-x-auto">
            <ul className="mb-2 space-y-1 text-[11px] text-terminal-muted" data-testid="live-auto-coins">
              {[...new Map(book.rows.map((row) => [row.book, row.coins])).entries()].map(
                ([name, coins]) => (
                  <li key={name}>
                    <span className="text-terminal-text">{name} coins:</span> {coins}
                  </li>
                ),
              )}
            </ul>
            <table className="w-full text-left text-[12px]">
              <thead className="text-[10px] uppercase tracking-wide text-terminal-muted">
                <tr>
                  <th className="px-2 py-1">Strategy</th>
                  <th className="px-2 py-1">Side</th>
                  <th className="px-2 py-1">Timeframe</th>
                  <th className="px-2 py-1">Book</th>
                  <th className="px-2 py-1">Stake</th>
                </tr>
              </thead>
              <tbody>
                {book.rows.map((row) => (
                  <tr
                    key={`${row.book}-${row.strategyId}-${row.direction}`}
                    className="border-t border-terminal-border"
                    data-testid="live-auto-strategy"
                  >
                    <td className="px-2 py-1.5 font-mono">{row.strategyId}</td>
                    <td className="px-2 py-1.5">{row.direction}</td>
                    <td className="px-2 py-1.5">{row.timeframe}</td>
                    <td className="px-2 py-1.5">{row.book}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap">{row.stake}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-[12px] text-terminal-muted" data-testid="live-auto-empty">
            No strategy is allowed to auto-trade right now.
          </p>
        )}
        {book.notes.length ? (
          <ul className="mt-2 space-y-1 text-[11px] text-amber-200/90" data-testid="live-auto-notes">
            {book.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}
      </section>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Open live paper trades
        </h2>
        <LiveRows rows={open} />
      </section>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Closed live paper trades
        </h2>
        <LiveRows rows={closed} />
      </section>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Cancelled ({cancelled.length})
        </h2>
        <LiveRows rows={cancelled} />
      </section>
    </div>
  );
}
