import { useCallback, useEffect, useRef, useState } from "react";
import {
  closeLegacyPaperPositions,
  disablePaperTrade,
  enablePaperTrade,
  fetchPaperOpportunities,
  fetchPaperPositions,
  resetPaperTrade,
  type PaperOpportunity,
  type PaperPosition,
  type PaperStatus,
  type PaperWatcherBookLive,
} from "../api/client";

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
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
  const warmOnce = useRef(false);

  const refreshBook = useCallback(async () => {
    try {
      const data = await fetchPaperPositions(100);
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

  async function toggleEnabled() {
    setBusy(true);
    try {
      const currentlyOn = status?.enabled ?? true;
      const next = currentlyOn ? await disablePaperTrade() : await enablePaperTrade();
      setStatus(next);
      try {
        localStorage.setItem("cs.paper.auto", next.enabled ? "1" : "0");
      } catch {
        /* ignore */
      }
      await refreshBook();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Toggle failed");
    } finally {
      setBusy(false);
    }
  }

  async function onReset() {
    if (!window.confirm("Reset paper book and equity to starting balance?")) return;
    setBusy(true);
    try {
      const next = await resetPaperTrade();
      setStatus(next);
      await refreshBook();
      await refreshOpps(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reset failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCloseLegacy() {
    if (
      !window.confirm(
        "Close/archive ALL open legacy RESEARCH 15M paper positions?\n\n" +
          "This does NOT touch COMBO_02 v1 watcher positions.\n" +
          "Reason: legacy_cleanup. Requires explicit confirmation.",
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const result = await closeLegacyPaperPositions(true);
      if (!result.ok) {
        setError(result.message || result.error || "Legacy cleanup failed");
      } else {
        setStatus(result.status);
        await refreshBook();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Legacy cleanup failed");
    } finally {
      setBusy(false);
    }
  }

  const autoOn = status?.enabled ?? true;
  const watcherOwns = status?.v1_watcher_owns_entries ?? oppMeta.watcherOwns;
  const telegram = status?.telegram;
  const watcher = status?.v1_watcher;
  const monitor = status?.monitor;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-4 text-terminal-text">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-terminal-border pb-3">
        <div>
          <h1 className="font-display text-lg tracking-tight">Paper Trade</h1>
          <p className="mt-1 max-w-xl text-[11px] text-terminal-muted">
            Parallel paper streams:{" "}
            <span className="text-terminal-text">COMBO_02 v1 1h watcher</span>{" "}
            (BTC / ETH / SOL) and 15m{" "}
            <span className="text-terminal-text">RESEARCH_15M</span> Trade chances,
            each with its own source label. LEGACY and v1 tracks stay open
            separately on the same symbol; Telegram only for COMBO_02 v1.
            Exits at
            stop or TP1 on mark/last. No real exchange orders. Freeze:{" "}
            <span className="font-mono text-terminal-text/80">v1-combo02-long-htf</span>.
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
            <span className="rounded border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 text-emerald-300">
              v1 core: BTC @ 2%
            </span>
            <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-amber-200">
              paper universe: ETH/SOL + liquid majors @ 2%
            </span>
            <span className="rounded border border-terminal-border bg-white/5 px-1.5 py-0.5 text-terminal-muted">
              research: 15m RESEARCH_15M stream
            </span>
            {status?.v1_profile?.enabled === false ? (
              <span className="rounded border border-rose-500/40 bg-rose-500/10 px-1.5 py-0.5 text-rose-200">
                v1 profile OFF
              </span>
            ) : null}
            {status?.v1_profile?.secondary_enabled === false ? (
              <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-amber-200">
                secondary disabled
              </span>
            ) : null}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={toggleEnabled}
            title={status ? (autoOn ? "Paper auto entries enabled" : "Paper auto entries paused") : "Loading…"}
            className={`rounded border px-3 py-1.5 text-[11px] uppercase tracking-wide ${
              autoOn
                ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
                : "border-terminal-border text-terminal-muted"
            }`}
          >
            {autoOn ? "Auto ON" : "Auto OFF"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onCloseLegacy}
            title="Archive open RESEARCH 15M / experimental paper positions only"
            className="rounded border border-sky-500/40 px-3 py-1.5 text-[11px] uppercase tracking-wide text-sky-200 hover:bg-sky-500/10"
          >
            Close legacy
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onReset}
            className="rounded border border-terminal-border px-3 py-1.5 text-[11px] uppercase tracking-wide text-terminal-muted hover:bg-white/5"
          >
            Reset
          </button>
        </div>
      </div>

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
      />

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        <Stat label="Equity" value={`$${fmt(status?.equity)}`} />
        <Stat
          label="Realized PnL"
          value={`$${fmt(status?.realized_pnl_usd)}`}
          tone={(status?.realized_pnl_usd ?? 0) >= 0 ? "up" : "down"}
        />
        <Stat label="Open" value={String(status?.open_count ?? 0)} />
        <Stat
          label="Open risk $"
          value={`$${fmt(status?.open_risk_usd)}`}
          title="Sum of open position risk_usd (v1 books: BTC/ETH/SOL 2% of equity)"
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
        <TradeTable
          rows={closed.filter((r) => String(r.status || "").toUpperCase() !== "CANCELLED")}
          empty="No closed paper trades yet."
          mode="closed"
        />
      </section>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Cancelled
          <span className="ml-2 font-mono normal-case text-terminal-muted/80">
            ({closed.filter((r) => String(r.status || "").toUpperCase() === "CANCELLED").length})
          </span>
        </h2>
        <TradeTable
          rows={closed.filter((r) => String(r.status || "").toUpperCase() === "CANCELLED")}
          empty="No cancelled paper trades."
          mode="cancelled"
        />
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
}: {
  autoOn: boolean;
  monitor: PaperStatus["monitor"];
  watcher: PaperStatus["v1_watcher"];
  telegram: PaperStatus["telegram"];
  status: PaperStatus | null;
}) {
  const liveBooks = watcher?.live?.length ? watcher.live : [];
  const books: Array<PaperWatcherBookLive | { symbol: string; timeframe?: string; tier?: string; risk_percent?: number }> =
    liveBooks.length ? liveBooks : watcher?.books || [];
  const tgReady = Boolean(telegram?.ready);
  const tgReason = telegram?.reason || null;
  const bookRisk =
    status?.v1_book_risk && Object.keys(status.v1_book_risk).length
      ? Object.entries(status.v1_book_risk)
          .map(([sym, row]) => `${sym.replace("USDT", "")} ${row.risk_pct_display || fmtPct(row.risk_percent)}`)
          .join(" · ")
      : "BTC/ETH/SOL 2%";
  return (
    <section className="rounded border border-terminal-border bg-terminal-panel/40 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">
            Auto monitor · Telegram
          </h2>
          <p className="mt-1 max-w-3xl text-[12px] text-terminal-text/90">
            {monitor?.explanation ||
              "Auto paper opens from the COMBO_02 v1 1h watcher (BTC/ETH/SOL)."}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
            <span
              className={`rounded border px-1.5 py-0.5 ${
                autoOn
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                  : "border-terminal-border text-terminal-muted"
              }`}
            >
              Paper Auto {autoOn ? "ON" : "OFF"}
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
            {watcher?.last_skip ? (
              <span
                className="max-w-full truncate rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-100"
                title={watcher.last_skip}
              >
                last skip: {watcher.last_skip}
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
            {telegram?.label || "PAPER_ENTRY / PAPER_EXIT from v1 watcher only"}
            <br />
            monitors {(telegram?.monitors || ["BTCUSDT", "ETHUSDT", "SOLUSDT"]).join(", ")} @{" "}
            {telegram?.timeframe || "1h"}
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
                const gateTxt = gates
                  ? `T${gates.trend ? "✓" : "·"} B${gates.bos ? "✓" : "·"} H${gates.htf ? "✓" : "·"}`
                  : "—";
                const wait = live?.waiting_next_closed_bar;
                const noData =
                  live?.tip_status === "NO_1H_DATA" || live?.tip_status === "NO_4H_DATA";
                return (
                  <tr key={b.symbol} className="border-t border-terminal-border/70">
                    <td className="px-2 py-1.5 text-terminal-text">{b.symbol}</td>
                    <td className="px-2 py-1.5">
                      {b.tier || "—"} · {fmtPct(b.risk_percent)}
                    </td>
                    <td
                      className={`px-2 py-1.5 ${noData ? "text-rose-300" : ""}`}
                      title={live?.tip_reason || ""}
                    >
                      {live?.tip_status || "—"}
                    </td>
                    <td className="max-w-[18rem] truncate px-2 py-1.5 text-terminal-muted" title={live?.tip_reason || ""}>
                      {live?.tip_reason || "—"}
                    </td>
                    <td className="px-2 py-1.5">{gateTxt}</td>
                    <td className="px-2 py-1.5">
                      {noData
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
}: {
  rows: PaperOpportunity[];
  empty: string;
  autoOn: boolean;
  watcherOwns: boolean;
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
              <td className="px-2 py-1.5">
                {r.already_open
                  ? "Open"
                  : watcherOwns
                    ? "Research only"
                    : r.tier === "READY" && autoOn
                      ? "Auto open"
                      : r.tier === "READY"
                        ? "Ready (Auto OFF)"
                        : "Watch"}
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
            <th className="px-2 py-1.5">Path</th>
            <th className="px-2 py-1.5">Version</th>
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
                    <th className="px-2 py-1.5">uPnL</th>
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
            const riskPct = snippet.risk_percent as number | null | undefined;
            const riskLabel =
              riskPct != null
                ? `${fmtPct(riskPct)} / $${fmt(r.risk_usd)}`
                : `$${fmt(r.risk_usd)}`;
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
              >
                {stream}
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
              {mode === "cancelled" ? (
                <>
                  <td className="px-2 py-1.5 whitespace-nowrap" title="stop → entry → tp1">
                    {fmtPriceRange(r)}
                  </td>
                  <td className="px-2 py-1.5">{fmt(r.quantity, 4)}</td>
                  <td className="px-2 py-1.5 text-amber-200/90">{r.exit_reason || "—"}</td>
                  <td className="px-2 py-1.5 text-terminal-muted whitespace-nowrap">
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
                          (r.unrealized_pnl_usd ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"
                        }`}
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
                          (r.pnl_usd ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"
                        }`}
                      >
                        {fmt(r.pnl_usd)}
                      </td>
                      <td className="px-2 py-1.5">{fmt(r.r_multiple, 2)}</td>
                    </>
                  )}
                  <td className="px-2 py-1.5 text-terminal-muted">{fmtTime(r.opened_at)}</td>
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
