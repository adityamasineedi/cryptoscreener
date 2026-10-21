import { useCallback, useEffect, useRef, useState } from "react";
import {
  closeLegacyPaperPositions,
  disablePaperTrade,
  enablePaperTrade,
  fetchDashboardReview,
  fetchPaperOpportunities,
  fetchPaperPositions,
  resetPaperTrade,
  type DashboardReview,
  type PaperOpportunity,
  type PaperPosition,
  type PaperStatus,
  type PaperWatcherBookLive,
} from "../api/client";
import { PassedRegimePanel } from "./PassedRegimePanel";
import { Book16PaperPanel } from "./Book16PaperPanel";
import {
  BookConfigHeader,
  BookRollupPanel,
  MissedBreakdown,
  RunnerHealthPanel,
  StopStatePanel,
} from "./PaperDashboardReview";
import { recordPaperAction, readPaperActions, type PaperActionEntry } from "../paper/actionLog";
import { splitCancelled } from "../paper/bookStats";
import { formatIst, formatUtc } from "../paper/zoneTime";
import {
  CLOSED_LIMIT,
  closedTruncationNote,
  opportunityAction,
  paperRiskLabel,
} from "../paper/paperLabels";

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtTime(iso: string | null | undefined): string {
  return formatIst(iso);
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const pct = Math.abs(n) <= 1 ? n * 100 : n;
  return `${pct.toFixed(2).replace(/\.?0+$/, "")}%`;
}

/** Stop → entry → TP1 price band for a paper trade. */
function fmtPriceRange(r: PaperPosition): string {
  const stop = fmt(r.stop_price, 4);
  const entry = fmt(r.entry_price, 4);
  const tp = fmt(r.tp1_price, 4);
  return `${stop} → ${entry} → ${tp}`;
}

/** Opened → closed wall-clock range. */
function fmtTimeRange(r: PaperPosition): string {
  const a = fmtTime(r.opened_at);
  const b = fmtTime(r.closed_at);
  if (a === "—" && b === "—") return "—";
  if (b === "—") return a;
  return `${a} → ${b}`;
}

export function PaperTradePanel() {
  const [status, setStatus] = useState<PaperStatus | null>(null);
  const [open, setOpen] = useState<PaperPosition[]>([]);
  const [closed, setClosed] = useState<PaperPosition[]>([]);
  const [opps, setOpps] = useState<PaperOpportunity[]>([]);
  const [oppMeta, setOppMeta] = useState<{
    ready: number;
    near: number;
    forming: number;
    waiting: number;
    watch: number;
    blocked: number;
    note: string;
    watcherOwns: boolean;
  }>({
    ready: 0,
    near: 0,
    forming: 0,
    waiting: 0,
    watch: 0,
    blocked: 0,
    note: "",
    watcherOwns: false,
  });
  const [tierFilter, setTierFilter] = useState<string>("ALL");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [oppLoading, setOppLoading] = useState(false);
  const [review, setReview] = useState<DashboardReview | null>(null);
  const [actions, setActions] = useState<PaperActionEntry[]>(() => readPaperActions());
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [typedAction, setTypedAction] = useState("");
  const [showDuplicateFills, setShowDuplicateFills] = useState(false);
  const warmOnce = useRef(false);

  const refreshBook = useCallback(async () => {
    try {
      const data = await fetchPaperPositions(CLOSED_LIMIT);
      setStatus(data.status);
      setOpen(data.open || []);
      setClosed(data.closed || []);
      try {
        localStorage.setItem("cs.paper.auto", data.status.enabled ? "1" : "0");
      } catch {
        /* ignore */
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load paper book");
    }
  }, []);

  const refreshOpps = useCallback(async (warm = 0) => {
    setOppLoading(true);
    try {
      const chances = await fetchPaperOpportunities(120, warm);
      setOpps(chances.rows || []);
      setOppMeta({
        ready: chances.ready || 0,
        near: chances.near || 0,
        forming: chances.forming || 0,
        waiting: chances.waiting || 0,
        watch: chances.watch || 0,
        blocked: chances.blocked || 0,
        note: chances.note || "",
        watcherOwns: chances.v1_watcher_owns_entries === true,
      });
    } catch (e) {
      // Don't clear Auto status when opportunities time out
      setError(e instanceof Error ? e.message : "Failed to load trade chances");
    } finally {
      setOppLoading(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    async function tick(first: boolean) {
      if (!alive) return;
      await refreshBook();
      if (!alive) return;
      const warm = first && !warmOnce.current ? 8 : 0;
      if (first) warmOnce.current = true;
      await refreshOpps(warm);
    }
    tick(true);
    const id = window.setInterval(() => tick(false), 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [refreshBook, refreshOpps]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const next = await fetchDashboardReview();
        if (alive) setReview(next);
      } catch {
        /* The safety lock stays on until a review payload arrives. */
      }
    };
    load();
    const id = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  function ask(action: string) {
    setTypedAction("");
    setPendingAction(action);
    setError(null);
  }

  function note(action: string) {
    setActions(recordPaperAction(action));
  }

  async function runConfirmed() {
    const action = pendingAction;
    if (!action || typedAction !== action) return;
    setPendingAction(null);
    setBusy(true);
    try {
      if (action === "RESET") {
        const next = await resetPaperTrade();
        setStatus(next);
        await refreshBook();
        await refreshOpps(0);
      } else if (action === "CLOSE LEGACY") {
        const result = await closeLegacyPaperPositions(true);
        if (!result.ok) {
          setError(result.message || result.error || "Legacy cleanup failed");
          return;
        }
        setStatus(result.status);
        await refreshBook();
      } else if (action === "AUTO OFF" || action === "AUTO ON") {
        if (!status) return;
        const next = status.enabled === true ? await disablePaperTrade() : await enablePaperTrade();
        setStatus(next);
        try {
          localStorage.setItem("cs.paper.auto", next.enabled ? "1" : "0");
        } catch {
          /* ignore */
        }
        await refreshBook();
      }
      note(action);
    } catch (e) {
      setError(e instanceof Error ? e.message : `${action} failed`);
    } finally {
      setBusy(false);
    }
  }

  function toggleEnabled() {
    // Before status loads the current state is unknown: send nothing.
    if (!status) return;
    ask(status.enabled === true ? "AUTO OFF" : "AUTO ON");
  }

  function onReset() {
    if (review == null || review.reset_blocked) return;
    ask("RESET");
  }

  function onCloseLegacy() {
    ask("CLOSE LEGACY");
  }

  const statusLoaded = status != null;
  const autoOn = status?.enabled === true;
  const legacyAutoOn = status?.legacy_auto_entry_enabled === true;
  const watcherOwns = status?.v1_watcher_owns_entries ?? oppMeta.watcherOwns;
  const telegram = status?.telegram;
  const watcher = status?.v1_watcher;
  const monitor = status?.monitor;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-4 text-terminal-text">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-terminal-border pb-3">
        <div>
          <h1 className="font-display text-lg tracking-tight">Paper Trade</h1>
          <BookConfigHeader review={review} />
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={busy || !statusLoaded}
            onClick={toggleEnabled}
            data-testid="paper-auto-toggle"
            title={status ? (autoOn ? "Paper auto entries enabled" : "Paper auto entries paused") : "Loading…"}
            className={`rounded border px-3 py-1.5 text-[11px] uppercase tracking-wide ${
              autoOn
                ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
                : "border-terminal-border text-terminal-muted"
            }`}
          >
            {!statusLoaded ? "Auto …" : autoOn ? "Auto ON" : "Auto OFF"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onCloseLegacy}
            data-testid="paper-close-legacy"
            title="Archive open RESEARCH 15M paper positions. Type CLOSE LEGACY to confirm."
            className="rounded border border-sky-500/40 px-3 py-1.5 text-[11px] uppercase tracking-wide text-sky-200 hover:bg-sky-500/10"
          >
            Close legacy
          </button>
          <button
            type="button"
            disabled={busy || review == null || review.reset_blocked}
            onClick={onReset}
            data-testid="paper-reset"
            title={
              review == null
                ? "Reset stays off until the dashboard review loads"
                : review.reset_blocked
                  ? `Reset is off: ${review.forward_tests
                      .filter((row) => row.running)
                      .map((row) => `${row.id} (${row.why})`)
                      .join("; ")}`
                  : "Type RESET to confirm"
            }
            className="rounded border border-terminal-border px-3 py-1.5 text-[11px] uppercase tracking-wide text-terminal-muted hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Reset
          </button>
        </div>
      </div>
      {review == null || review.reset_blocked ? (
        <p className="text-[11px] text-amber-200/90" data-testid="paper-reset-locked">
          Reset is off
          {review == null
            ? " until the dashboard review loads."
            : `: ${review.forward_tests
                .filter((row) => row.running)
                .map((row) => `${row.id} (${row.why})`)
                .join("; ")}.`}
        </p>
      ) : null}
      {pendingAction ? (
        <form
          className="flex flex-wrap items-center gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[12px]"
          data-testid="paper-confirm"
          onSubmit={(event) => {
            event.preventDefault();
            void runConfirmed();
          }}
        >
          <label>
            Type {pendingAction} to confirm
            <input
              className="ml-2 rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono text-terminal-text"
              value={typedAction}
              onChange={(event) => setTypedAction(event.target.value)}
              data-testid="paper-confirm-input"
              autoComplete="off"
            />
          </label>
          <button
            type="submit"
            disabled={typedAction !== pendingAction || busy}
            data-testid="paper-confirm-submit"
            className="rounded border border-amber-400/50 px-2 py-1 text-[11px] uppercase disabled:opacity-40"
          >
            Confirm
          </button>
          <button type="button" className="text-[11px] text-terminal-muted" onClick={() => setPendingAction(null)}>
            Cancel
          </button>
        </form>
      ) : null}
      <ol className="text-[10px] text-terminal-muted" data-testid="paper-action-log">
        {actions.map((entry) => (
          <li key={`${entry.at}-${entry.action}`} title={formatUtc(entry.at)}>
            {formatIst(entry.at)} · {entry.action}
          </li>
        ))}
      </ol>

      {error ? (
        <div className="rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-200">
          {error}
        </div>
      ) : null}

      <MonitorPanel
        autoOn={autoOn}
        monitor={monitor}
        watcher={watcher}
        telegram={telegram}
        status={status}
        review={review}
      />
      <PassedRegimePanel />
      <MissedBreakdown review={review} />
      <Book16PaperPanel />
      <BookRollupPanel
        open={open}
        closed={closed}
        startingEquity={status?.starting_equity ?? 1000}
        combined={{
          equity: status?.equity ?? null,
          realized: status?.realized_pnl_usd ?? null,
          openRisk: status?.open_risk_usd ?? null,
          trades: status?.closed_count ?? null,
        }}
        candle12={review?.candle12_v2 ?? null}
      />
      <StopStatePanel review={review} />
      <RunnerHealthPanel review={review} />

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        <Stat
          label="Equity · all streams combined"
          value={`$${fmt(status?.equity)}`}
          title="Pooled paper equity: starting equity + realized PnL of every stream (v1, passed book, research). New positions size from this number."
        />
        <Stat
          label="Realized PnL · all streams combined"
          value={`$${fmt(status?.realized_pnl_usd)}`}
          tone={(status?.realized_pnl_usd ?? 0) >= 0 ? "up" : "down"}
        />
        <Stat label="Open" value={String(status?.open_count ?? 0)} />
        <Stat
          label="Open risk $"
          value={`$${fmt(status?.open_risk_usd)}`}
          title="Sum of open position risk_usd"
        />
        <Stat label="Ready" value={String(oppMeta.ready)} tone={oppMeta.ready > 0 ? "up" : undefined} />
        <Stat label="Near" value={String(oppMeta.near)} />
        <Stat label="Forming" value={String(oppMeta.forming)} />
        <Stat label="Waiting" value={String(oppMeta.waiting)} />
      </div>

      <section>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">
            Trade chances — {opps.length} symbols in 15m setup cache
            {oppLoading ? " · updating…" : ""}
          </h2>
          <div className="flex flex-wrap gap-1">
            {["ALL", "READY", "NEAR", "FORMING", "WAITING", "WATCH", "BLOCKED"].map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTierFilter(t)}
                className={`rounded px-2 py-0.5 text-[10px] uppercase tracking-wide ${
                  tierFilter === t
                    ? "bg-white/10 text-terminal-text"
                    : "text-terminal-muted hover:bg-white/5"
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        {oppMeta.note ? (
          <p className="mb-2 text-[11px] text-amber-200/90">{oppMeta.note}</p>
        ) : null}
        <OpportunityTable
          rows={tierFilter === "ALL" ? opps : opps.filter((r) => r.tier === tierFilter)}
          empty="No setups in cache yet — open Screener so symbols warm, then refresh."
          autoOn={autoOn}
          watcherOwns={watcherOwns}
          legacyAutoOn={legacyAutoOn}
        />
      </section>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Open positions
        </h2>
        <TradeTable
          rows={open}
          empty="No open paper positions — LEGACY 15m and COMBO_02 v1 are tracked separately."
          mode="open"
        />
      </section>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Closed trades
        </h2>
        {closedTruncationNote(closed.length, CLOSED_LIMIT) ? (
          <p className="mb-2 text-[11px] text-amber-200/90" data-testid="paper-closed-truncated">
            {closedTruncationNote(closed.length, CLOSED_LIMIT)}
          </p>
        ) : null}
        <TradeTable
          rows={closed.filter((r) => String(r.status || "").toUpperCase() !== "CANCELLED")}
          empty="No closed paper trades yet."
          mode="closed"
        />
      </section>

      <section>
        {(() => {
          const split = splitCancelled(closed);
          const rows = showDuplicateFills ? [...split.visible, ...split.duplicates] : split.visible;
          return (
            <>
              <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
                Cancelled
                <span className="ml-2 font-mono normal-case text-terminal-muted/80">({rows.length})</span>
              </h2>
              {split.duplicates.length ? (
                <p className="mb-2 text-[11px] text-terminal-muted" data-testid="paper-duplicate-fills">
                  duplicate_fill {split.duplicates.length}{" "}
                  <button
                    type="button"
                    className="underline"
                    onClick={() => setShowDuplicateFills((on) => !on)}
                  >
                    {showDuplicateFills ? "hide" : "show"}
                  </button>
                </p>
              ) : null}
              <TradeTable rows={rows} empty="No cancelled paper trades." mode="cancelled" />
            </>
          );
        })()}
      </section>
    </div>
  );
}

function MonitorPanel({
  autoOn,
  monitor,
  watcher,
  telegram,
  status,
  review,
}: {
  autoOn: boolean;
  monitor: PaperStatus["monitor"];
  watcher: PaperStatus["v1_watcher"];
  telegram: PaperStatus["telegram"];
  status: PaperStatus | null;
  review: DashboardReview | null;
}) {
  const liveBooks = watcher?.live?.length ? watcher.live : [];
  const books: Array<PaperWatcherBookLive | { symbol: string; timeframe?: string; tier?: string; risk_percent?: number }> =
    liveBooks.length ? liveBooks : watcher?.books || [];
  // watcher.last_skip is the last reason noted, even after that book opened and
  // its row was cleared. Show it only while a book row still carries it.
  const headerSkip =
    watcher?.last_skip && liveBooks.some((book) => book.last_skip === watcher.last_skip)
      ? watcher.last_skip
      : null;
  const tgReady = Boolean(telegram?.ready);
  const tgReason = telegram?.reason || null;
  const v1Watcher = review?.watchers.find((row) => row.id === "V1");
  const bookRisk =
    status?.v1_book_risk && Object.keys(status.v1_book_risk).length
      ? Object.entries(status.v1_book_risk)
          .map(([sym, row]) => `${sym.replace("USDT", "")} ${row.risk_pct_display || fmtPct(row.risk_percent)}`)
          .join(" · ")
      : v1Watcher
        ? v1Watcher.risk
        : "—";
  const openWatchers = (review?.watchers || []).filter((row) => row.can_open).map((row) => row.id);
  return (
    <section className="rounded border border-terminal-border bg-terminal-panel/40 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">
            Auto monitor · Telegram
          </h2>
          <p className="mt-1 max-w-3xl text-[12px] text-terminal-text/90" data-testid="paper-auto-line">
            {review
              ? `Watchers that can open trades: ${openWatchers.length ? openWatchers.join(", ") : "none"}. Armed: ${
                  review.armed_books.length ? review.armed_books.join(", ") : "none"
                }.`
              : "Reading which watchers can open trades."}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
            <span
              className={`rounded border px-1.5 py-0.5 ${
                autoOn
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                  : "border-terminal-border text-terminal-muted"
              }`}
            >
              Paper Auto {status == null ? "…" : autoOn ? "ON" : "OFF"}
            </span>
            <span className="rounded border border-terminal-border bg-white/5 px-1.5 py-0.5 font-mono text-terminal-text">
              source={monitor?.auto_source || "V1_PAPER_WATCHER"}
            </span>
            <span className="rounded border border-terminal-border bg-white/5 px-1.5 py-0.5 text-terminal-muted">
              TF {watcher?.timeframe || "1h"} · path {watcher?.path || "A"}
            </span>
            <span
              className="rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-emerald-200"
              title="COMBO_02 v1 watcher position sizing"
            >
              v1 risk {bookRisk}
            </span>
            <span
              className="rounded border border-terminal-border bg-white/5 px-1.5 py-0.5 text-terminal-muted"
              title={status?.risk_percent_label || "RESEARCH_15M default only"}
            >
              research default {fmtPct(status?.risk_percent_legacy_research_15m ?? status?.risk_percent)}
            </span>
            {status?.last_skip_reason ? (
              <span
                className="max-w-full truncate rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-100"
                title={status.last_skip_reason}
                data-testid="engine-last-skip-chip"
              >
                engine last skip: {status.last_skip_reason}
              </span>
            ) : null}
            {headerSkip ? (
              <span
                className="max-w-full truncate rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-100"
                title={headerSkip}
                data-testid="watcher-last-skip-chip"
              >
                last skip: {headerSkip}
              </span>
            ) : null}
          </div>
        </div>
        <div
          className={`min-w-[14rem] rounded border px-3 py-2 text-[11px] ${
            tgReady
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
              : "border-amber-500/40 bg-amber-500/10 text-amber-100"
          }`}
        >
          <div className="text-[10px] uppercase tracking-wide opacity-80">Telegram</div>
          <div className="mt-1 font-mono">
            {tgReady ? "READY · delivery on" : "NOT READY"}
          </div>
          <div className="mt-1 text-[10px] leading-relaxed opacity-90">
            {review?.telegram_text || "Reading Telegram delivery flags."}
            {tgReason ? (
              <>
                <br />
                <span className="text-amber-50">{tgReason}</span>
              </>
            ) : (
              <>
                <br />
                enabled={String(telegram?.enabled)} · configured=
                {String(telegram?.configured)} · subscribed=
                {String(telegram?.subscribed)}
              </>
            )}
          </div>
        </div>
      </div>

      {books.length ? (
        <div className="mt-3 overflow-x-auto rounded border border-terminal-border/80">
          <table className="min-w-full text-left font-mono text-[11px]">
            <thead className="bg-black/20 text-[10px] uppercase tracking-wide text-terminal-muted">
              <tr>
                <th className="px-2 py-1.5">Book</th>
                <th className="px-2 py-1.5">Tier / risk</th>
                <th className="px-2 py-1.5">Tip status</th>
                <th className="px-2 py-1.5">Detail</th>
                <th className="px-2 py-1.5">Gates</th>
                <th className="px-2 py-1.5">Watcher</th>
                <th className="px-2 py-1.5">1h / 4h bars</th>
              </tr>
            </thead>
            <tbody>
              {books.map((b) => {
                const live: PaperWatcherBookLive | null = liveBooks.length
                  ? (b as PaperWatcherBookLive)
                  : null;
                const gates = live?.gates;
                const wait = live?.waiting_next_closed_bar;
                const consumed = wait || live?.tip_status === "WAITING_NEXT_BAR";
                const gateTxt = !gates
                  ? "—"
                  : consumed
                    ? "consumed"
                    : `T${gates.trend ? "✓" : "·"} B${gates.bos ? "✓" : "·"} H${gates.htf ? "✓" : "·"}`;
                const syncing = live?.tip_status === "SYNCING_LIVE";
                const noData =
                  syncing ||
                  live?.tip_status === "NO_1H_DATA" ||
                  live?.tip_status === "NO_4H_DATA";
                return (
                  <tr key={b.symbol} className="border-t border-terminal-border/70">
                    <td className="px-2 py-1.5 text-terminal-text">{b.symbol}</td>
                    <td className="px-2 py-1.5">
                      {b.tier || "—"} · {fmtPct(b.risk_percent)}
                    </td>
                    <td
                      className={`px-2 py-1.5 ${syncing ? "text-amber-200" : noData ? "text-rose-300" : ""}`}
                      title={live?.tip_reason || ""}
                    >
                      {live?.tip_status || "—"}
                    </td>
                    <td className="max-w-[18rem] px-2 py-1.5 text-terminal-muted">
                      <div className="truncate" title={live?.tip_reason || ""}>
                        {live?.tip_reason || "—"}
                      </div>
                      {live?.last_skip ? (
                        <div
                          className="truncate text-amber-100"
                          title={`${live.last_skip} @ ${live.last_skip_bar || "no closed 1h bar"}`}
                        >
                          last skip: {live.last_skip} @ {live.last_skip_bar || "—"}
                        </div>
                      ) : null}
                    </td>
                    <td
                      className="px-2 py-1.5"
                      title={
                        consumed
                          ? "This 1h tip is already watermarked. Gates are the consumed bar, not a queued open."
                          : undefined
                      }
                    >
                      {gateTxt}
                    </td>
                    <td className="px-2 py-1.5">
                      {syncing
                        ? "syncing live"
                        : noData
                        ? "needs OHLCV"
                        : wait
                          ? "waiting next 1h close"
                          : live?.seeded
                            ? "seeded / watching"
                            : "warming"}
                    </td>
                    <td className="px-2 py-1.5 text-terminal-muted">
                      {live ? `${live.bars_1h ?? 0} / ${live.bars_4h ?? 0}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-3 text-[11px] text-terminal-muted">
          Watcher books not loaded yet — restart backend with PAPER_V1_WATCHER_ENABLED=true.
        </p>
      )}
    </section>
  );
}

function Stat({
  label,
  value,
  tone,
  title,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
  title?: string;
}) {
  const color =
    tone === "up" ? "text-emerald-300" : tone === "down" ? "text-rose-300" : "text-terminal-text";
  return (
    <div
      className="rounded border border-terminal-border bg-terminal-panel/60 px-3 py-2"
      title={title}
    >
      <div className="text-[10px] uppercase tracking-wide text-terminal-muted">{label}</div>
      <div className={`mt-1 font-mono text-sm ${color}`}>{value}</div>
    </div>
  );
}

function tierClass(tier: string): string {
  if (tier === "READY") return "text-emerald-300";
  if (tier === "NEAR") return "text-amber-300";
  if (tier === "FORMING") return "text-sky-300";
  if (tier === "BLOCKED") return "text-rose-300";
  if (tier === "WAITING") return "text-terminal-muted";
  return "text-terminal-muted";
}

function OpportunityTable({
  rows,
  empty,
  autoOn,
  watcherOwns,
  legacyAutoOn,
}: {
  rows: PaperOpportunity[];
  empty: string;
  autoOn: boolean;
  watcherOwns: boolean;
  legacyAutoOn: boolean;
}) {
  if (!rows.length) {
    return (
      <div className="rounded border border-dashed border-terminal-border px-3 py-6 text-center text-[12px] text-terminal-muted">
        {empty}
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded border border-terminal-border">
      <table className="min-w-full text-left font-mono text-[11px]">
        <thead className="bg-black/20 text-[10px] uppercase tracking-wide text-terminal-muted">
          <tr>
            <th className="px-2 py-1.5">Tier</th>
            <th className="px-2 py-1.5">Symbol</th>
            <th className="px-2 py-1.5">Status</th>
            <th className="px-2 py-1.5">Trend</th>
            <th className="px-2 py-1.5">BOS</th>
            <th className="px-2 py-1.5">Impulse</th>
            <th className="px-2 py-1.5">Pullback</th>
            <th className="px-2 py-1.5">Entry</th>
            <th className="px-2 py-1.5">Stop</th>
            <th className="px-2 py-1.5">TP1</th>
            <th className="px-2 py-1.5">Pass</th>
            <th className="px-2 py-1.5">Chance</th>
            <th className="px-2 py-1.5">Action</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.symbol}-${r.tier}-${r.status}`} className="border-t border-terminal-border/70">
              <td className={`px-2 py-1.5 font-semibold ${tierClass(r.tier)}`}>{r.tier}</td>
              <td className="px-2 py-1.5 text-terminal-text">{r.symbol}</td>
              <td className="px-2 py-1.5">{r.status}</td>
              <td className="px-2 py-1.5">{r.setup_trend || "—"}</td>
              <td className="px-2 py-1.5">{r.bos || "—"}</td>
              <td className="px-2 py-1.5">{r.impulse || "—"}</td>
              <td className="px-2 py-1.5">{r.pullback || "—"}</td>
              <td className="px-2 py-1.5">{fmt(r.entry_price, 4)}</td>
              <td className="px-2 py-1.5">{fmt(r.stop_price, 4)}</td>
              <td className="px-2 py-1.5">{fmt(r.tp1_price, 4)}</td>
              <td className="px-2 py-1.5">{r.pass_count}/8</td>
              <td className="max-w-[14rem] truncate px-2 py-1.5 text-terminal-muted" title={r.chance}>
                {r.chance}
              </td>
              <td className="px-2 py-1.5" data-testid="opportunity-action">
                {opportunityAction(r, { autoOn, watcherOwns, legacyAutoOn })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TradeTable({
  rows,
  empty,
  mode,
}: {
  rows: PaperPosition[];
  empty: string;
  mode: "open" | "closed" | "cancelled";
}) {
  if (!rows.length) {
    return (
      <div className="rounded border border-dashed border-terminal-border px-3 py-6 text-center text-[12px] text-terminal-muted">
        {empty}
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded border border-terminal-border">
      <table className="min-w-full text-left font-mono text-[11px]">
        <thead className="bg-black/20 text-[10px] uppercase tracking-wide text-terminal-muted">
          <tr>
            <th className="px-2 py-1.5">Symbol</th>
            <th className="px-2 py-1.5">Stream</th>
            <th className="px-2 py-1.5">Side</th>
            <th className="px-2 py-1.5">Path</th>
            <th className="px-2 py-1.5">Version</th>
            <th className="px-2 py-1.5">Price src</th>
            {mode === "cancelled" ? (
              <>
                <th className="px-2 py-1.5">Range (stop → entry → tp1)</th>
                <th className="px-2 py-1.5">Qty</th>
                <th className="px-2 py-1.5">Reason</th>
                <th className="px-2 py-1.5">Time range</th>
              </>
            ) : (
              <>
                <th className="px-2 py-1.5">Entry</th>
                <th className="px-2 py-1.5">Stop</th>
                <th className="px-2 py-1.5">TP1</th>
                <th className="px-2 py-1.5">Risk</th>
                <th className="px-2 py-1.5">Qty</th>
                {mode === "open" ? (
                  <>
                    <th className="px-2 py-1.5">Mark</th>
                    <th className="px-2 py-1.5" title="Gross: mark vs entry × qty. Excludes entry/exit fees and slippage; closed PnL is net.">
                      Current profit (excl. fees)
                    </th>
                    <th className="px-2 py-1.5">uR</th>
                  </>
                ) : (
                  <>
                    <th className="px-2 py-1.5">Exit</th>
                    <th className="px-2 py-1.5">Reason</th>
                    <th className="px-2 py-1.5">PnL</th>
                    <th className="px-2 py-1.5">R</th>
                  </>
                )}
                <th className="px-2 py-1.5">Opened</th>
                {mode === "closed" ? <th className="px-2 py-1.5">Closed</th> : null}
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const snippet = (r.signal_snippet || {}) as Record<string, unknown>;
            const path = String(snippet.path || "—");
            const ver = String(snippet.combo_version || "—");
            const source = String(snippet.source || "");
            const stream =
              source === "V1_PAPER_WATCHER" ||
              String(snippet.strategy_id || "") === "COMBO_02_V1"
                ? "V1"
                : source === "V2_CANDIDATE_PAPER_WATCHER" ||
                    String(snippet.strategy_id || "") === "COMBO_02_V2_RESEARCH"
                  ? "V2"
                  : "LEGACY";
            // Cap-book rows carry their own stream id (e.g. PASSED|COMBO_02_V2|SHORT).
            const passedStream =
              source === "PASSED_CAP_BOOK" &&
              typeof snippet.stream === "string" &&
              snippet.stream.trim() !== ""
                ? snippet.stream
                : null;
            const streamLabel =
              stream === "LEGACY" && passedStream ? passedStream : stream;
            const side = r.side ? String(r.side) : "—";
            const priceSource =
              typeof snippet.price_source === "string" && snippet.price_source.trim() !== ""
                ? snippet.price_source
                : "—";
            const riskPct = snippet.risk_percent as number | null | undefined;
            const riskLabel = paperRiskLabel(riskPct, r.risk_usd, snippet.paper_execution);
            return (
            <tr key={r.id} className="border-t border-terminal-border/70">
              <td className="px-2 py-1.5 text-terminal-text">{r.symbol}</td>
              <td
                className={`px-2 py-1.5 whitespace-nowrap ${
                  stream === "V1"
                    ? "text-emerald-300"
                    : stream === "V2"
                      ? "text-amber-200"
                      : "text-terminal-muted"
                }`}
                title={source || stream}
                data-testid="paper-stream"
              >
                {streamLabel}
              </td>
              <td className="px-2 py-1.5 whitespace-nowrap" data-testid="paper-side">
                {side}
              </td>
              <td className="px-2 py-1.5 whitespace-nowrap" title={path}>
                {path}
              </td>
              <td
                className="px-2 py-1.5 whitespace-nowrap text-terminal-muted"
                title={ver}
              >
                {ver === "v1-combo02-long-htf" ? "v1" : ver === "experimental-path-b" ? "exp" : ver}
              </td>
              <td
                className="px-2 py-1.5 whitespace-nowrap text-terminal-muted"
                title={priceSource}
                data-testid="paper-price-source"
              >
                {priceSource}
              </td>
              {mode === "cancelled" ? (
                <>
                  <td className="px-2 py-1.5 whitespace-nowrap" title="stop → entry → tp1">
                    {fmtPriceRange(r)}
                  </td>
                  <td className="px-2 py-1.5">{fmt(r.quantity, 4)}</td>
                  <td className="px-2 py-1.5 text-amber-200/90">{r.exit_reason || "—"}</td>
                  <td
                    className="px-2 py-1.5 text-terminal-muted whitespace-nowrap"
                    title={`${formatUtc(r.opened_at)} → ${formatUtc(r.closed_at)}`}
                  >
                    {fmtTimeRange(r)}
                  </td>
                </>
              ) : (
                <>
                  <td className="px-2 py-1.5">{fmt(r.entry_price, 4)}</td>
                  <td className="px-2 py-1.5">{fmt(r.stop_price, 4)}</td>
                  <td className="px-2 py-1.5">{fmt(r.tp1_price, 4)}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap" title={riskLabel}>
                    {riskLabel}
                  </td>
                  <td className="px-2 py-1.5">{fmt(r.quantity, 4)}</td>
                  {mode === "open" ? (
                    <>
                      <td className="px-2 py-1.5">{fmt(r.mark_price, 4)}</td>
                      <td
                        className={`px-2 py-1.5 ${
                          r.unrealized_pnl_usd == null
                            ? "text-terminal-muted"
                            : r.unrealized_pnl_usd >= 0
                              ? "text-emerald-300"
                              : "text-rose-300"
                        }`}
                        data-testid="paper-current-profit"
                      >
                        {fmt(r.unrealized_pnl_usd)}
                      </td>
                      <td className="px-2 py-1.5">{fmt(r.unrealized_r, 2)}</td>
                    </>
                  ) : (
                    <>
                      <td className="px-2 py-1.5">{fmt(r.exit_price, 4)}</td>
                      <td className="px-2 py-1.5">{r.exit_reason || "—"}</td>
                      <td
                        className={`px-2 py-1.5 ${
                          r.pnl_usd == null
                            ? "text-terminal-muted"
                            : r.pnl_usd >= 0
                              ? "text-emerald-300"
                              : "text-rose-300"
                        }`}
                        data-testid="paper-closed-pnl"
                      >
                        {fmt(r.pnl_usd)}
                      </td>
                      <td className="px-2 py-1.5">{fmt(r.r_multiple, 2)}</td>
                    </>
                  )}
                  <td className="px-2 py-1.5 text-terminal-muted" title={formatUtc(r.opened_at)}>
                    {fmtTime(r.opened_at)}
                  </td>
                  {mode === "closed" ? (
                    <td
                      className="px-2 py-1.5 text-terminal-muted whitespace-nowrap"
                      data-testid="paper-closed-at"
                      title={formatUtc(r.closed_at)}
                    >
                      {fmtTime(r.closed_at)}
                    </td>
                  ) : null}
                </>
              )}
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
