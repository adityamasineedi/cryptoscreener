import { useCallback, useEffect, useRef, useState } from "react";
import {
  disablePaperTrade,
  enablePaperTrade,
  fetchPaperOpportunities,
  fetchPaperPositions,
  resetPaperTrade,
  type PaperOpportunity,
  type PaperPosition,
  type PaperStatus,
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
  }>({ ready: 0, near: 0, forming: 0, waiting: 0, watch: 0, blocked: 0 });
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

  const autoOn = status?.enabled ?? true;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-4 text-terminal-text">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-terminal-border pb-3">
        <div>
          <h1 className="font-display text-lg tracking-tight">Paper Trade</h1>
          <p className="mt-1 max-w-xl text-[11px] text-terminal-muted">
            Auto-opens a virtual long on{" "}
            <span className="text-terminal-text">
              {status?.entry_mode_label || "Path A (Trend + BOS)"}
            </span>
            . Same gate as research backtest. Exits at stop or TP1 on mark/last. No real
            exchange orders.
          </p>
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

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        <Stat label="Equity" value={`$${fmt(status?.equity)}`} />
        <Stat
          label="Realized PnL"
          value={`$${fmt(status?.realized_pnl_usd)}`}
          tone={(status?.realized_pnl_usd ?? 0) >= 0 ? "up" : "down"}
        />
        <Stat label="Open" value={String(status?.open_count ?? 0)} />
        <Stat label="Ready" value={String(oppMeta.ready)} tone={oppMeta.ready > 0 ? "up" : undefined} />
        <Stat label="Near" value={String(oppMeta.near)} />
        <Stat label="Forming" value={String(oppMeta.forming)} />
        <Stat label="Waiting" value={String(oppMeta.waiting)} />
        <Stat label="Watch/Block" value={String(oppMeta.watch + oppMeta.blocked)} />
      </div>

      <section>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">
            Trade chances — {opps.length} symbols in setup cache
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
        <OpportunityTable
          rows={tierFilter === "ALL" ? opps : opps.filter((r) => r.tier === tierFilter)}
          empty="No setups in cache yet — open Screener so symbols warm, then refresh."
          autoOn={autoOn}
        />
      </section>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Open positions
        </h2>
        <TradeTable
          rows={open}
          empty="No open paper positions — waiting for Path A READY (Trend+BOS)."
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

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  const color =
    tone === "up" ? "text-emerald-300" : tone === "down" ? "text-rose-300" : "text-terminal-text";
  return (
    <div className="rounded border border-terminal-border bg-terminal-panel/60 px-3 py-2">
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
}: {
  rows: PaperOpportunity[];
  empty: string;
  autoOn: boolean;
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
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-terminal-border/70">
              <td className="px-2 py-1.5 text-terminal-text">{r.symbol}</td>
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
          ))}
        </tbody>
      </table>
    </div>
  );
}
