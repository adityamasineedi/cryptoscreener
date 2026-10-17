import { useEffect, useState } from "react";
import {
  fetchPassedRegime,
  type PassedRegimeResponse,
  type PassedRegimeSide,
} from "../api/client";
import { PassedBookHealth } from "./PassedBookHealth";
import { SymbolAllowlistLines } from "./SymbolAllowlistLines";

const POLL_MS = 5000;

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function trendTone(trend: string): string {
  if (trend === "BULLISH") return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
  if (trend === "BEARISH") return "border-red-500/40 bg-red-500/10 text-red-300";
  return "border-amber-500/40 bg-amber-500/10 text-amber-200";
}

/** Read-only status of the passed-book regime router. It has no controls. */
export function PassedRegimePanel() {
  const [snapshot, setSnapshot] = useState<PassedRegimeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;

    const poll = async () => {
      if (controller) return;
      controller = new AbortController();
      try {
        const next = await fetchPassedRegime(controller.signal);
        if (!active) return;
        setSnapshot(next);
        setError(null);
      } catch (err) {
        if (!active || (err instanceof DOMException && err.name === "AbortError")) return;
        setError(err instanceof Error ? err.message : "Passed regime status unavailable");
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

  const signals = snapshot?.latest_signals ?? [];
  const lastBar = snapshot?.last_evaluated_bar ?? null;
  const readOk = snapshot?.daily_read_ok ?? false;
  const bookClosed = snapshot?.book_open === false;

  return (
    <section
      className="rounded border border-terminal-border bg-white/[0.02] p-3"
      data-testid="passed-regime"
    >
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-terminal-text">Passed book regime</h2>
          <p className="mt-1 text-[11px] text-terminal-muted">
            Read-only. The BTC daily trend picks which passed sides may open new paper trades.
            Refreshes every {POLL_MS / 1000} seconds.
          </p>
        </div>
        <div className="text-right font-mono text-[10px] text-terminal-muted" aria-live="polite">
          {error ? (
            <span className="text-red-400" data-testid="passed-regime-error">
              {error}
              {snapshot ? " · showing last snapshot" : ""}
            </span>
          ) : null}
          {!error && snapshot?.timestamp ? `Updated ${fmtTime(snapshot.timestamp)}` : null}
        </div>
      </div>

      {!snapshot && !error ? (
        <div className="py-2 text-xs text-terminal-muted" data-testid="passed-regime-loading">
          Loading regime status…
        </div>
      ) : null}

      {snapshot ? (
        <>
          <PassedBookHealth snapshot={snapshot} />
          {bookClosed ? (
            <p
              className="mb-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200"
              data-testid="passed-regime-book-closed"
            >
              Book closed.{" "}
              {snapshot.book_note ||
                "New passed-regime entries are off because no side passed its regime window."}{" "}
              Signals below are recorded, not opened.
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <span
              className={`rounded border px-2 py-1 font-mono text-xs ${
                readOk ? trendTone(snapshot.trend) : "border-red-500/40 bg-red-500/10 text-red-300"
              }`}
              data-testid="passed-regime-trend"
            >
              {readOk ? snapshot.trend : "DAILY READ FAILED"}
            </span>
            <span className="text-terminal-muted">
              BTC daily bar{" "}
              <span className="font-mono text-terminal-text" data-testid="passed-regime-as-of">
                {snapshot.as_of_daily_bar || "—"}
              </span>
              {" · "}
              <span className="font-mono text-terminal-text" data-testid="passed-regime-daily-bars">
                {snapshot.daily_bars}
              </span>{" "}
              daily bars
              {snapshot.regime_bar ? (
                <>
                  {" · for 1h bar "}
                  <span className="font-mono text-terminal-text" data-testid="passed-regime-bar">
                    {snapshot.regime_bar}
                  </span>
                </>
              ) : null}
            </span>
          </div>
          {!readOk ? (
            <p className="mt-2 text-[11px] text-red-300" data-testid="passed-regime-read-failed">
              The BTC daily read failed ({snapshot.daily_read_error || "unknown"}). This is not a
              trend. The bar is retried on the next tick; nothing opens until the read succeeds.
            </p>
          ) : null}
          {readOk && snapshot.trend === "NEUTRAL" ? (
            <p className="mt-2 text-[11px] text-amber-200" data-testid="passed-regime-neutral-note">
              No new signals. Open positions keep their own stops and targets.
            </p>
          ) : null}
          {readOk && snapshot.short_history ? (
            <p className="mt-1 text-[11px] text-amber-200" data-testid="passed-regime-short-history">
              The trend is neutral because BTC daily history is short: {snapshot.daily_bars} closed
              bars, {snapshot.min_daily_bars} needed.
            </p>
          ) : null}

          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <SideTable
              title={bookClosed ? "Allowed book (closed)" : "Allowed book"}
              empty="No sides may open new trades."
              rows={snapshot.allowed}
              testId="passed-regime-allowed"
            />
            <SideTable
              title="Skipped book"
              empty="No sides are skipped."
              rows={snapshot.skipped}
              testId="passed-regime-skipped"
            />
          </div>
          <SymbolAllowlistLines
            allowlist={snapshot.symbol_allowlist}
            testId="passed-regime-allowlist"
          />

          <h3 className="mt-4 text-[11px] uppercase tracking-wide text-terminal-muted">
            Latest signals · {signals.length}
          </h3>
          <div className="mt-2 max-h-72 overflow-auto rounded border border-terminal-border/60">
            <table className="w-full min-w-[820px] border-collapse text-left text-[11px]">
              <thead className="sticky top-0 bg-black/90 text-[10px] uppercase tracking-wide text-terminal-muted">
                <tr>
                  <th className="px-2 py-2">Setup bar</th>
                  <th className="px-2 py-2">Symbol</th>
                  <th className="px-2 py-2">Strategy</th>
                  <th className="px-2 py-2">TF</th>
                  <th className="px-2 py-2">Side</th>
                  <th className="px-2 py-2">Trend</th>
                  <th className="px-2 py-2">Outcome</th>
                  <th className="px-2 py-2">Reason</th>
                </tr>
              </thead>
              <tbody data-testid="passed-regime-signals">
                {signals.map((row, index) => (
                  <tr
                    key={`${row.recorded_at}-${row.symbol}-${row.strategy_id}-${row.direction}-${index}`}
                    className="border-t border-terminal-border/40"
                  >
                    <td className="px-2 py-1.5 font-mono">{fmtTime(row.setup_bar_time)}</td>
                    <td className="px-2 py-1.5 font-mono">{row.symbol}</td>
                    <td className="px-2 py-1.5 font-mono">{row.strategy_id}</td>
                    <td className="px-2 py-1.5 font-mono">{row.timeframe}</td>
                    <td className="px-2 py-1.5 font-mono">{row.direction}</td>
                    <td className="px-2 py-1.5 font-mono">{row.trend}</td>
                    <td
                      className={`px-2 py-1.5 font-mono ${
                        row.outcome === "opened" ? "text-emerald-400" : "text-terminal-muted"
                      }`}
                    >
                      {row.outcome}
                    </td>
                    <td className="px-2 py-1.5 font-mono">{row.reason || "—"}</td>
                  </tr>
                ))}
                {!signals.length ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-4 text-center text-terminal-muted">
                      No signals yet
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <dl
            className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-terminal-muted"
            data-testid="passed-regime-meta"
          >
            <div>
              <dt className="inline">Watcher: </dt>
              <dd className="inline text-terminal-text" data-testid="passed-regime-enabled">
                {snapshot.enabled ? "enabled" : "disabled"}
              </dd>
            </div>
            {snapshot.watcher_loop_enabled != null ? (
              <div>
                <dt className="inline">Loop setting: </dt>
                <dd className="inline text-terminal-text" data-testid="passed-regime-loop">
                  {snapshot.watcher_loop_enabled ? "on" : "off"}
                </dd>
              </div>
            ) : null}
            <div>
              <dt className="inline">Opened: </dt>
              <dd className="inline text-terminal-text">{snapshot.opened_count}</dd>
            </div>
            <div>
              <dt className="inline">Last evaluated bar: </dt>
              <dd className="inline text-terminal-text" data-testid="passed-regime-last-bar">
                {lastBar
                  ? `${lastBar.symbol} ${lastBar.bar} · ${lastBar.trend} · ${lastBar.sides_evaluated} sides`
                  : "none"}
              </dd>
            </div>
            <div>
              <dt className="inline">Last skip: </dt>
              <dd className="inline text-terminal-text" data-testid="passed-regime-last-skip">
                {snapshot.last_skip || "none"}
              </dd>
            </div>
          </dl>
        </>
      ) : null}
    </section>
  );
}

function SideTable({
  title,
  empty,
  rows,
  testId,
}: {
  title: string;
  empty: string;
  rows: PassedRegimeSide[];
  testId: string;
}) {
  return (
    <div>
      <h3 className="text-[11px] uppercase tracking-wide text-terminal-muted">
        {title} · {rows.length}
      </h3>
      <div className="mt-2 overflow-auto rounded border border-terminal-border/60">
        <table className="w-full border-collapse text-left text-[11px]">
          <thead className="bg-black/90 text-[10px] uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-2 py-2">Strategy</th>
              <th className="px-2 py-2">TF</th>
              <th className="px-2 py-2">Side</th>
            </tr>
          </thead>
          <tbody data-testid={testId}>
            {rows.map((row, index) => (
              <tr
                key={`${row.strategy_id}-${row.timeframe}-${row.direction}-${index}`}
                className="border-t border-terminal-border/40"
              >
                <td className="px-2 py-1.5 font-mono">{row.strategy_id}</td>
                <td className="px-2 py-1.5 font-mono">{row.timeframe}</td>
                <td className="px-2 py-1.5 font-mono">{row.direction}</td>
              </tr>
            ))}
            {!rows.length ? (
              <tr>
                <td colSpan={3} className="px-3 py-3 text-center text-terminal-muted">
                  {empty}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
