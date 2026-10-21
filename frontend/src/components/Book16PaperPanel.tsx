import { useEffect, useState } from "react";
import { fetchBook16Paper, type Book16PaperResponse, type Book16PaperTrade } from "../api/client";
import { formatIst, formatUtc } from "../paper/zoneTime";

const POLL_MS = 30000;

function fmtTime(iso: string | null | undefined): string {
  return formatIst(iso);
}

function usd(v: number | null | undefined, signed = false): string {
  if (v == null || Number.isNaN(v)) return "—";
  const s = Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  return `${signed ? (v < 0 ? "−" : "+") : v < 0 ? "−" : ""}$${s}`;
}

function px(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function tone(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-terminal-text";
  return v > 0 ? "text-emerald-300" : "text-rose-300";
}

const EXIT_LABEL: Record<string, string> = {
  signal: "close < SMA200",
  stop: "stop",
  stop_gap: "stop (gap)",
};

/**
 * Read-only virtual paper book for 4h book 16 (252-bar closing-high breakout, BTC/ETH LONG). It mirrors the
 * frozen forward test BOOK16_4H_FWD-1, which keeps the official ledger. No orders; no controls.
 */
export function Book16PaperPanel() {
  const [snapshot, setSnapshot] = useState<Book16PaperResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;

    const poll = async () => {
      if (controller) return;
      controller = new AbortController();
      try {
        const next = await fetchBook16Paper(controller.signal);
        if (!active) return;
        setSnapshot(next);
        setError(null);
      } catch (err) {
        if (!active || (err instanceof DOMException && err.name === "AbortError")) return;
        setError(err instanceof Error ? err.message : "Book 16 paper unavailable");
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

  const symbols = snapshot?.symbols ?? [];
  const closed: Book16PaperTrade[] = symbols
    .flatMap((s) => snapshot?.books[s]?.closed ?? [])
    .sort((a, b) => String(b.exit_time).localeCompare(String(a.exit_time)));
  const pooledNet = closed.reduce((sum, t) => sum + Number(t.net_pnl || 0), 0);
  const healthy = snapshot?.status === "COLLECTING" || snapshot?.status === "WAITING_FOR_FIRST_FORWARD_BAR";

  return (
    <section className="rounded border border-terminal-border bg-white/[0.02] p-3" data-testid="book16-paper">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-terminal-text">4h book 16 · 252-bar breakout (BTC/ETH, LONG)</h2>
          <p className="mt-1 text-[11px] text-terminal-muted" title={formatUtc(snapshot?.start)}>
            Virtual paper from {fmtTime(snapshot?.start)}, mirror of forward test {snapshot?.mirrors ?? "BOOK16_4H_FWD-1"}{" "}
            (the official record). $10,000 per coin, 0.5% risk per trade; after fees, slippage and funding. No
            orders. Updates after each 4h close. Forward read at 30 trades; at ~13/year that is about 2.3 years.
          </p>
        </div>
        <div className="text-right font-mono text-[10px] text-terminal-muted" aria-live="polite">
          {error ? (
            <span className="text-red-400" data-testid="book16-paper-error">
              {error}
              {snapshot ? " · showing last snapshot" : ""}
            </span>
          ) : null}
          {!error && snapshot ? (
            <span title={formatUtc(snapshot.updated_utc)}>Book updated {fmtTime(snapshot.updated_utc)}</span>
          ) : null}
        </div>
      </div>

      {!snapshot && !error ? <div className="py-2 text-xs text-terminal-muted">Loading book 16 paper…</div> : null}

      {snapshot ? (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px]">
            <span
              className={`rounded border px-2 py-1 font-mono text-xs ${
                healthy
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                  : "border-red-500/40 bg-red-500/10 text-red-300"
              }`}
              data-testid="book16-paper-status"
            >
              {snapshot.status}
            </span>
            {snapshot.loop_enabled === false ? (
              <span className="text-amber-200">Loop off (PAPER_BOOK16_4H_ENABLED=false)</span>
            ) : null}
            {snapshot.error ? <span className="text-red-300">{snapshot.error}</span> : null}
            <span className="text-terminal-muted">
              Closed trades <span className="font-mono text-terminal-text">{closed.length}</span> · pooled net{" "}
              <span className={`font-mono ${tone(pooledNet)}`} data-testid="book16-paper-net">
                {usd(pooledNet, true)}
              </span>
            </span>
          </div>

          <div className="mb-2 grid gap-2 sm:grid-cols-2">
            {symbols.map((s) => {
              const b = snapshot.books[s];
              const o = b?.open;
              return (
                <div key={s} className="rounded border border-terminal-border/70 p-2 text-[11px]" data-testid={`book16-paper-${s}`}>
                  <div className="flex items-center justify-between font-mono">
                    <span className="text-terminal-text">{s}</span>
                    <span className={tone((b?.equity ?? 10000) - 10000)}>equity {usd(b?.equity)}</span>
                  </div>
                  <div className="mt-1 text-terminal-muted">
                    Last 4h bar {fmtTime(b?.last_bar)} · forward bars {b?.forward_bars ?? 0}
                    {b?.pending_closed?.length ? ` · ${b.pending_closed.length} closed, waiting for funding` : ""}
                  </div>
                  {o ? (
                    <div className="mt-1 font-mono text-terminal-text" data-testid={`book16-paper-open-${s}`}>
                      OPEN since {fmtTime(o.entry_time)} @ {px(o.entry_price)} · stop {px(o.stop_price)} · mark{" "}
                      {px(o.mark_price)} · <span className={tone(o.unrealized_usd)}>{usd(o.unrealized_usd, true)}</span>
                    </div>
                  ) : (
                    <div className="mt-1 text-terminal-muted">Flat — waits for a close at a new 252-bar high.</div>
                  )}
                </div>
              );
            })}
          </div>

          {closed.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px] text-left text-[11px]" data-testid="book16-paper-closed">
                <thead className="text-terminal-muted">
                  <tr>
                    <th className="px-2 py-1">Coin</th>
                    <th className="px-2 py-1">Entry</th>
                    <th className="px-2 py-1">Exit</th>
                    <th className="px-2 py-1">Reason</th>
                    <th className="px-2 py-1 text-right">Entry px</th>
                    <th className="px-2 py-1 text-right">Exit px</th>
                    <th className="px-2 py-1 text-right">Funding</th>
                    <th className="px-2 py-1 text-right">Net</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {closed.map((t) => (
                    <tr key={t.trade_id} className="border-t border-terminal-border/40">
                      <td className="px-2 py-1">{t.symbol}</td>
                      <td className="px-2 py-1" title={formatUtc(t.entry_time)}>
                        {fmtTime(t.entry_time)}
                      </td>
                      <td className="px-2 py-1" title={formatUtc(t.exit_time)}>
                        {fmtTime(t.exit_time)}
                      </td>
                      <td className="px-2 py-1">{EXIT_LABEL[t.exit_reason ?? ""] ?? t.exit_reason}</td>
                      <td className="px-2 py-1 text-right">{px(t.entry_price)}</td>
                      <td className="px-2 py-1 text-right">{px(t.exit_price)}</td>
                      <td className="px-2 py-1 text-right" title={`coverage ${t.funding_coverage_status}`}>
                        {usd(t.funding, true)}
                        {t.funding_coverage_status !== "COMPLETE" ? ` (${t.funding_coverage_status})` : ""}
                      </td>
                      <td className={`px-2 py-1 text-right ${tone(t.net_pnl)}`}>{usd(t.net_pnl, true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="rounded border border-dashed border-terminal-border px-3 py-3 text-center text-[11px] text-terminal-muted">
              No closed trades yet. About 13 a year are expected across both coins.
            </div>
          )}
        </>
      ) : null}
    </section>
  );
}
