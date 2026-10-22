import type { DashboardReview, PaperLedgerBook, PaperPosition } from "../api/client";
import { rollupBooks } from "../paper/bookStats";
import { formatIst, formatUtc } from "../paper/zoneTime";

function money(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function num(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function age(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function coinLine(coins: unknown): string {
  if (!Array.isArray(coins) || coins.length === 0) return "none listed";
  return coins
    .map((coin) => {
      if (typeof coin === "string") return coin;
      if (coin && typeof coin === "object") {
        const row = coin as { symbol?: string; group?: string; risk_percent?: number; timeframe?: string };
        const risk = row.risk_percent != null ? ` ${(row.risk_percent * 100).toFixed(0)}%` : "";
        const group = row.group ? ` ${row.group}` : "";
        const tf = row.timeframe ? ` ${row.timeframe}` : "";
        return `${row.symbol || "—"}${group}${tf}${risk}`;
      }
      return String(coin);
    })
    .join(", ");
}

export function BookConfigHeader({ review }: { review: DashboardReview | null }) {
  if (!review) {
    return (
      <p className="mt-1 max-w-3xl text-[11px] text-terminal-muted">
        Reading which watchers can open trades, which books are armed, and each book’s coins and risk.
      </p>
    );
  }
  const open = review.watchers.filter((row) => row.can_open).map((row) => row.id);
  return (
    <div className="mt-1 max-w-3xl text-[11px] text-terminal-muted" data-testid="paper-book-config">
      <p>
        Watchers that can open trades:{" "}
        <span className="text-terminal-text">{open.length ? open.join(", ") : "none"}</span>
        . Armed books:{" "}
        <span className="text-terminal-text">{review.armed_books.length ? review.armed_books.join(", ") : "none"}</span>
        . No real orders.
      </p>
      <div className="mt-2 flex flex-col gap-1">
        {review.watchers.map((row) => (
          <p key={row.id}>
            <span className="text-terminal-text">{row.id}</span>{" "}
            {row.can_open ? "can open" : "cannot open"} · {row.risk}. Coins: {coinLine(row.coins)}.
          </p>
        ))}
      </div>
    </div>
  );
}

export function BookRollupPanel({
  open,
  ledgerBooks,
  ledgerError,
  startingEquity,
  combined,
  candle12,
}: {
  open: PaperPosition[];
  ledgerBooks: PaperLedgerBook[] | null;
  ledgerError?: string | null;
  startingEquity: number;
  combined: { equity: number | null; realized: number | null; openRisk: number | null; trades: number | null };
  candle12: DashboardReview["candle12_v2"] | null;
}) {
  // Open risk comes from the engine's open rows; every other number is the ledger view's default view (D187).
  const openRisk = new Map(rollupBooks(open, [], startingEquity).map((row) => [row.id, row.openRisk]));
  const books = ledgerBooks ?? [];
  return (
    <section data-testid="paper-book-rollups">
      <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">Per book</h2>
      <p className="mb-2 text-[11px] text-terminal-muted" data-testid="paper-rollup-scope">
        VALID closed trades from each book&apos;s forward start (the default view). Nominal R is PnL over the
        risk the book meant to take (equity at open × risk %); R on actual risk is PnL over the risk the position
        really carried after the 2× leverage cap.
      </p>
      {ledgerBooks == null ? (
        <p className="mb-2 text-[11px] text-terminal-muted" data-testid="paper-rollup-pending">
          {ledgerError ? `Ledger view unavailable: ${ledgerError}` : "Reading the ledger view…"}
        </p>
      ) : null}
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
        {books.map((row) => (
          <article
            key={row.id}
            className="rounded border border-terminal-border bg-white/[0.02] p-2 text-[11px]"
            data-testid={`paper-book-${row.id}`}
          >
            <div className="text-terminal-text">
              {row.id} · {row.label}
            </div>
            <div className="mt-1 text-terminal-muted" title={row.forward_start_cite}>
              {row.forward_start ? (
                <>
                  From <span title={formatUtc(row.forward_start)}>{formatIst(row.forward_start)}</span>
                </>
              ) : (
                "Disabled: history only"
              )}
            </div>
            <div className="text-terminal-muted">Equity {money(row.equity)}</div>
            <div className="text-terminal-muted">Realized PnL {money(row.pnl_usd)}</div>
            <div className="text-terminal-muted" data-testid={`paper-book-${row.id}-r`}>
              Nominal R {num(row.r_nominal)} · R on actual risk {num(row.r_actual)}
            </div>
            <div className="text-terminal-muted">Trades {row.trades}</div>
            <div className="text-terminal-muted" data-testid={`paper-book-${row.id}-capped`}>
              Shrunk by 2× leverage cap {row.capped}
            </div>
            <div className="text-terminal-muted">Open risk {money(openRisk.get(row.id) ?? 0)}</div>
          </article>
        ))}
        <article className="rounded border border-terminal-border bg-white/[0.02] p-2 text-[11px]" data-testid="paper-candle12-rollup">
          <div className="text-terminal-text">CANDLE12_V2 · BTC 1h LONG</div>
          <div className="mt-1 text-terminal-muted">Equity {money(candle12?.equity)}</div>
          <div className="text-terminal-muted">Realized PnL {money(candle12?.realized_pnl)}</div>
          <div className="text-terminal-muted">Net R {num(candle12?.net_r)}</div>
          <div className="text-terminal-muted">Trades {candle12?.trades ?? "—"}</div>
          <div className="text-terminal-muted">Open risk {money(candle12?.open_risk)}</div>
        </article>
      </div>
      <p className="mt-2 text-[11px] text-terminal-muted" data-testid="paper-combined-label">
        all streams combined · equity {money(combined.equity)} · realized {money(combined.realized)} · open risk{" "}
        {money(combined.openRisk)} · closed trades in view {combined.trades ?? "—"}
      </p>
    </section>
  );
}

export function StopStatePanel({ review }: { review: DashboardReview | null }) {
  const stops = review?.stops;
  return (
    <section className="rounded border border-terminal-border p-3 text-[11px]" data-testid="paper-stop-state">
      <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">Stop state</h2>
      <p className="mt-1 text-terminal-muted">
        Counter start{" "}
        <span title={formatUtc(stops?.counter_start?.first_run_utc)}>
          {formatIst(stops?.counter_start?.first_run_utc)}
        </span>
        {stops?.counter_start?.ruling ? ` · ${stops.counter_start.ruling}` : ""}
      </p>
      {!stops?.engine_loaded ? (
        <p className="mt-1 text-terminal-muted">The paper engine is not loaded in this response, so streaks are blank.</p>
      ) : null}
      <div className="mt-2 grid gap-2 md:grid-cols-2">
        {Object.entries(stops?.books || {}).map(([name, book]) => (
          <article key={name} className="rounded border border-terminal-border/70 p-2">
            <div className="text-terminal-text">{name}</div>
            <div>Halted: {book.halted ? "yes" : "no"}</div>
            {book.halts.map((halt) => (
              <div key={halt.halt}>
                {halt.halt} until <span title={formatUtc(halt.resets_at)}>{formatIst(halt.resets_at)}</span>
              </div>
            ))}
            <div>
              Paused coins:{" "}
              {book.paused_coins.length
                ? book.paused_coins.map((row) => (
                    <span key={row.symbol} title={formatUtc(row.resets_at)}>
                      {row.symbol} until {formatIst(row.resets_at)}{" "}
                    </span>
                  ))
                : "none"}
            </div>
            <div>
              Loss streak {book.consecutive_losses ?? "—"} / limit {stops?.limits.consecutive_loss_halt ?? "—"}
            </div>
            <div>
              Drawdown {book.drawdown_pct == null ? "—" : `${(book.drawdown_pct * 100).toFixed(2)}%`} / limit{" "}
              {stops?.limits.peak_drawdown_halt_pct == null
                ? "—"
                : `${(stops.limits.peak_drawdown_halt_pct * 100).toFixed(0)}%`}
            </div>
            <div>
              Strategy drawdown {num(book.strategy_drawdown_r)}R / limit {num(stops?.limits.strategy_drawdown_halt_r)}R
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

export function RunnerHealthPanel({ review }: { review: DashboardReview | null }) {
  return (
    <section className="rounded border border-terminal-border p-3 text-[11px]" data-testid="paper-runner-health">
      <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">Runner health</h2>
      <table className="mt-2 w-full">
        <thead className="text-terminal-muted">
          <tr>
            <th className="py-1 text-left font-normal">Runner</th>
            <th className="py-1 text-left font-normal">Last heartbeat</th>
            <th className="py-1 text-left font-normal">Age</th>
            <th className="py-1 text-left font-normal">Status</th>
          </tr>
        </thead>
        <tbody>
          {(review?.runners || []).map((row) => (
            <tr key={row.runner} className="border-t border-terminal-border/60">
              <td className="py-1" title={row.detail}>
                {row.runner}
              </td>
              <td className="py-1" title={formatUtc(row.last_heartbeat)}>
                {formatIst(row.last_heartbeat)}
              </td>
              <td className="py-1">{age(row.age_seconds)}</td>
              <td className="py-1">{row.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function MissedBreakdown({ review }: { review: DashboardReview | null }) {
  const missed = review?.missed;
  if (!missed) return null;
  const reasons = ["sleep", "crash", "late bar", "ohlcv_catchup", "other"];
  return (
    <section className="rounded border border-terminal-border p-3 text-[11px]" data-testid="paper-missed-breakdown">
      <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">
        Missed signals {missed.counter}
      </h2>
      <p className="mt-1 text-terminal-muted">{missed.note}</p>
      <p className="mt-1 text-terminal-muted">
        Retained {missed.retained_rows}. Unevaluated bars {missed.unevaluated_bars}. Rows in the counter with no
        retained reason {missed.unretained}.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {reasons.map((name) => (
          <span key={name} className="rounded border border-terminal-border px-1.5 py-0.5">
            {name} {missed.by_reason[name] ?? 0}
          </span>
        ))}
      </div>
      <p className="mt-2 text-terminal-muted">
        By book:{" "}
        {Object.entries(missed.by_book)
          .map(([book, count]) => `${book} ${count}`)
          .join(", ") || "—"}
      </p>
      <p className="text-terminal-muted">
        By day:{" "}
        {Object.entries(missed.by_day)
          .map(([day, count]) => `${day} ${count}`)
          .join(", ") || "—"}
      </p>
      <p className="mt-1 text-terminal-muted">
        After {formatIst(missed.after_restart_utc)} ({missed.after_restart.length} retained
        {missed.after_restart_unknown.length ? `, ${missed.after_restart_unknown.length} with no known cause` : ", none with an unknown cause"}
        ).
      </p>
    </section>
  );
}
