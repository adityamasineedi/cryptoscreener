/** Owner D188 part 2: My Trades, the owner's manual trade journal. Paper journal only: no orders, no keys. */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  closeTrade,
  exportUrl,
  fetchMyTrades,
  fetchPrice,
  logTrade,
  voidTrade,
  type GroupStats,
  type ManualTrade,
  type MyTradesView,
  type NewTrade,
  type TradeStats,
} from "../screenerTool/client";

const THIN = new Set(["AAVE", "APT", "ARB", "DOT", "ETC", "OP", "FIL"]);
const TAKER = 0.0005;

function base(symbol: string): string {
  const s = symbol.trim().toUpperCase();
  for (const q of ["USDT", "USDC", "BUSD", "FDUSD"]) if (s.endsWith(q) && s.length > q.length) return s.slice(0, -q.length);
  return s;
}

/** Client preview only; the server recomputes every figure (D144). */
export function previewLevels(f: {
  symbol: string;
  side: "LONG" | "SHORT";
  entry: number;
  stop: number;
  targets: number[];
  sizeMode: "risk_usd" | "quantity";
  size: number;
}): { ok: boolean; error?: string; qty?: number; risk?: number; stopPct?: number; costR?: number; targetsR?: number[] } {
  const sgn = f.side === "LONG" ? 1 : -1;
  if (!(f.entry > 0) || !(f.stop > 0)) return { ok: false, error: "entry and stop are needed" };
  if ((f.entry - f.stop) * sgn <= 0) return { ok: false, error: `stop must be ${f.side === "LONG" ? "below" : "above"} entry` };
  if (!(f.size > 0)) return { ok: false, error: "size or risk $ is needed" };
  const perUnit = Math.abs(f.entry - f.stop);
  const qty = f.sizeMode === "quantity" ? f.size : f.size / perUnit;
  const risk = f.sizeMode === "quantity" ? f.size * perUnit : f.size;
  const slip = THIN.has(base(f.symbol)) ? 0.0005 : 0.0002;
  return {
    ok: true,
    qty,
    risk,
    stopPct: (perUnit / f.entry) * 100,
    costR: (2 * (TAKER + slip) * f.entry) / perUnit,
    targetsR: f.targets.map((t) => ((t - f.entry) * sgn) / perUnit),
  };
}

function n(v: number | null | undefined, d = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

function signed(v: number | null | undefined, d = 2, suffix = ""): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(d)}${suffix}`;
}

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function parseTargets(text: string): number[] {
  return text
    .split(/[,;\s]+/)
    .map((x) => Number(x))
    .filter((x) => Number.isFinite(x) && x > 0);
}

function nowLocalInput(): string {
  const d = new Date();
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function StatsRow({ label, s }: { label: string; s: TradeStats }) {
  return (
    <tr className="border-t border-terminal-border/70" data-testid="stats-row">
      <td className="px-2 py-1 text-terminal-text">{label}</td>
      <td className="px-2 py-1">{s.trades}</td>
      <td className="px-2 py-1">{pct(s.win_rate)}</td>
      <td className="px-2 py-1">{signed(s.avg_r, 2, " R")}</td>
      <td className="px-2 py-1">
        {signed(s.expectancy_r, 2, " R")} / {s.expectancy_usd == null ? "—" : `$${n(s.expectancy_usd)}`}
      </td>
      <td className="px-2 py-1">{s.profit_factor == null ? (s.trades ? "no loss" : "—") : n(s.profit_factor)}</td>
      <td className="px-2 py-1">
        {n(s.max_drawdown_r)} R / ${n(s.max_drawdown_usd)}
      </td>
      <td className="px-2 py-1">{s.longest_losing_streak}</td>
      <td className="px-2 py-1">${n(s.net_pnl_usd)}</td>
    </tr>
  );
}

function StatsTable({ rows }: { rows: Array<{ label: string; s: TradeStats }> }) {
  return (
    <div className="overflow-x-auto rounded border border-terminal-border">
      <table className="min-w-full text-left font-mono text-[11px]">
        <thead className="bg-black/20 text-[10px] uppercase tracking-wide text-terminal-muted">
          <tr>
            <th className="px-2 py-1">Group</th>
            <th className="px-2 py-1">Trades</th>
            <th className="px-2 py-1">Win rate</th>
            <th className="px-2 py-1">Avg R</th>
            <th className="px-2 py-1">Expectancy</th>
            <th className="px-2 py-1">Profit factor</th>
            <th className="px-2 py-1">Max DD</th>
            <th className="px-2 py-1">Longest losing streak</th>
            <th className="px-2 py-1">Net $</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <StatsRow key={r.label} label={r.label} s={r.s} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExitForm({ trade, onDone }: { trade: ManualTrade; onDone: (msg: string | null, err?: string) => void }) {
  const [price, setPrice] = useState("");
  const [when, setWhen] = useState(nowLocalInput());
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="flex flex-wrap items-center gap-1"
      data-testid={`exit-form-${trade.trade_id}`}
      onSubmit={async (e) => {
        e.preventDefault();
        const p = Number(price);
        if (!(p > 0)) return onDone(null, "exit price is needed");
        setBusy(true);
        try {
          await closeTrade(trade.trade_id, {
            exit_price: p,
            exit_at: when ? new Date(when).toISOString() : undefined,
            exit_reason: reason || undefined,
          });
          onDone(`Exit logged for ${trade.symbol}.`);
        } catch (err) {
          onDone(null, err instanceof Error ? err.message : "exit failed");
        } finally {
          setBusy(false);
        }
      }}
    >
      <input
        aria-label={`Exit price ${trade.symbol}`}
        value={price}
        onChange={(e) => setPrice(e.target.value)}
        placeholder="exit price"
        className="w-24 rounded border border-terminal-border bg-black/20 px-1"
      />
      <input
        aria-label={`Exit time ${trade.symbol}`}
        type="datetime-local"
        value={when}
        onChange={(e) => setWhen(e.target.value)}
        className="rounded border border-terminal-border bg-black/20 px-1"
      />
      <input
        aria-label={`Exit reason ${trade.symbol}`}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="reason (target, stop, manual)"
        className="w-36 rounded border border-terminal-border bg-black/20 px-1"
      />
      <button type="submit" disabled={busy} className="rounded border border-sky-500/50 px-2 text-sky-200">
        Log exit
      </button>
    </form>
  );
}

export function MyTradesPage() {
  const [view, setView] = useState<MyTradesView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [group, setGroup] = useState<"by_setup" | "by_coin" | "by_month">("by_setup");
  const [form, setForm] = useState({
    symbol: "",
    side: "LONG" as "LONG" | "SHORT",
    entry: "",
    stop: "",
    targets: "",
    sizeMode: "risk_usd" as "risk_usd" | "quantity",
    size: "",
    setup_tag: "",
    timeframe: "",
    notes: "",
    screenshot_path: "",
  });

  const load = useCallback(async () => {
    try {
      setView(await fetchMyTrades());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load My Trades");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const prefill = useCallback(
    async (force = false) => {
      const sym = form.symbol.trim().toUpperCase();
      if (sym.length < 3 || (form.entry && !force)) return;
      try {
        const p = await fetchPrice(sym);
        if (p.price != null) setForm((f) => (force || !f.entry ? { ...f, entry: String(p.price) } : f));
      } catch {
        /* no live price: the owner types it */
      }
    },
    [form.symbol, form.entry],
  );

  const preview = useMemo(
    () =>
      previewLevels({
        symbol: form.symbol,
        side: form.side,
        entry: Number(form.entry),
        stop: Number(form.stop),
        targets: parseTargets(form.targets),
        sizeMode: form.sizeMode,
        size: Number(form.size),
      }),
    [form],
  );

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!preview.ok) {
      setError(preview.error ?? "check the levels");
      return;
    }
    const body: NewTrade = {
      symbol: form.symbol.trim().toUpperCase(),
      side: form.side,
      entry_price: Number(form.entry),
      stop_price: Number(form.stop),
      targets: parseTargets(form.targets),
      setup_tag: form.setup_tag || undefined,
      timeframe: form.timeframe || undefined,
      notes: form.notes || undefined,
      screenshot_path: form.screenshot_path || undefined,
      ...(form.sizeMode === "quantity" ? { quantity: Number(form.size) } : { risk_usd: Number(form.size) }),
    };
    try {
      const { trade } = await logTrade(body);
      setNotice(`Logged ${trade.symbol} ${trade.side}: risk $${n(trade.risk_usd)}, qty ${n(trade.quantity, 6)}.`);
      setError(null);
      setForm((f) => ({ ...f, entry: "", stop: "", targets: "", size: "", notes: "", screenshot_path: "" }));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "log failed");
    }
  }

  const trades = view?.trades ?? [];
  const open = trades.filter((t) => t.status === "OPEN");
  const closed = trades.filter((t) => t.status === "CLOSED");
  const voided = trades.filter((t) => t.voided);
  const honesty = view?.stats.honesty;
  const groups: GroupStats[] = view ? view.stats[group] : [];
  const input = "rounded border border-terminal-border bg-black/20 px-1 py-0.5 text-terminal-text";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4 text-[12px] text-terminal-text">
      <header>
        <h1 className="text-[13px] uppercase tracking-wide text-terminal-muted">My Trades · manual journal</h1>
        <p className="mt-1 max-w-3xl text-[11px] text-terminal-muted" data-testid="my-trades-disclaimer">
          {view?.disclaimer ?? "Paper journal and display only: no orders, no exchange keys. Not a buy or sell signal."}
        </p>
        <p className="mt-1 max-w-3xl text-[11px] text-terminal-muted" data-testid="my-trades-formula">
          Costs (D144): net $ = qty × (exit − entry) × side − qty × (entry + exit) × (0.05% taker + 0.02% slippage);
          slippage 0.05% on AAVE/APT/ARB/DOT/ETC/OP/FIL. R = net $ / (qty × |entry − stop|).
        </p>
      </header>
      {error ? (
        <p className="text-[11px] text-rose-300" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? <p className="text-[11px] text-emerald-300">{notice}</p> : null}

      <section className="rounded border border-terminal-border p-3" data-testid="log-trade">
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">Log a trade</h2>
        <form className="grid grid-cols-2 gap-2 sm:grid-cols-4" onSubmit={submit}>
          <label className="flex flex-col gap-0.5">
            Coin
            <input
              aria-label="Coin"
              className={input}
              value={form.symbol}
              onChange={(e) => setForm({ ...form, symbol: e.target.value })}
              onBlur={() => prefill(false)}
              placeholder="ETHUSDT"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            Side
            <select
              aria-label="Side"
              className={input}
              value={form.side}
              onChange={(e) => setForm({ ...form, side: e.target.value as "LONG" | "SHORT" })}
            >
              <option value="LONG">LONG</option>
              <option value="SHORT">SHORT</option>
            </select>
          </label>
          <div className="flex flex-col gap-0.5">
            <span>
              Entry{" "}
              <button type="button" className="text-[10px] text-sky-300" onClick={() => prefill(true)}>
                use current price
              </button>
            </span>
            <input aria-label="Entry" className={input} value={form.entry} onChange={(e) => setForm({ ...form, entry: e.target.value })} />
          </div>
          <label className="flex flex-col gap-0.5">
            Stop
            <input aria-label="Stop" className={input} value={form.stop} onChange={(e) => setForm({ ...form, stop: e.target.value })} />
          </label>
          <label className="flex flex-col gap-0.5">
            Targets (comma separated)
            <input
              aria-label="Targets"
              className={input}
              value={form.targets}
              onChange={(e) => setForm({ ...form, targets: e.target.value })}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            Size as
            <select
              aria-label="Size as"
              className={input}
              value={form.sizeMode}
              onChange={(e) => setForm({ ...form, sizeMode: e.target.value as "risk_usd" | "quantity" })}
            >
              <option value="risk_usd">Risk $</option>
              <option value="quantity">Quantity (coins)</option>
            </select>
          </label>
          <label className="flex flex-col gap-0.5">
            {form.sizeMode === "risk_usd" ? "Risk $" : "Quantity"}
            <input aria-label="Size" className={input} value={form.size} onChange={(e) => setForm({ ...form, size: e.target.value })} />
          </label>
          <label className="flex flex-col gap-0.5">
            Setup tag
            <input
              aria-label="Setup tag"
              className={input}
              value={form.setup_tag}
              onChange={(e) => setForm({ ...form, setup_tag: e.target.value })}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            Timeframe
            <input
              aria-label="Timeframe"
              className={input}
              value={form.timeframe}
              onChange={(e) => setForm({ ...form, timeframe: e.target.value })}
              placeholder="1h"
            />
          </label>
          <label className="col-span-2 flex flex-col gap-0.5">
            Notes
            <input aria-label="Notes" className={input} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </label>
          <label className="flex flex-col gap-0.5">
            Screenshot path (optional)
            <input
              aria-label="Screenshot path"
              className={input}
              value={form.screenshot_path}
              onChange={(e) => setForm({ ...form, screenshot_path: e.target.value })}
            />
          </label>
          <div className="col-span-2 flex flex-wrap items-center gap-3 sm:col-span-4" data-testid="log-preview">
            {preview.ok ? (
              <span className="text-terminal-muted">
                Risk ${n(preview.risk)} · qty {n(preview.qty, 6)} · stop {n(preview.stopPct)}% · cost {n(preview.costR)} R
                {preview.costR != null && preview.costR > 0.25 ? " (COST TOO HIGH)" : ""}
                {preview.targetsR?.length ? ` · targets ${preview.targetsR.map((r) => `${r.toFixed(2)} R`).join(", ")}` : ""}
              </span>
            ) : (
              <span className="text-terminal-muted">{preview.error}</span>
            )}
            <button type="submit" className="rounded border border-emerald-500/50 px-3 py-0.5 text-emerald-200">
              Log trade
            </button>
          </div>
        </form>
      </section>

      <section data-testid="honesty-panel" className="rounded border border-amber-500/30 bg-amber-500/5 p-3">
        <h2 className="mb-1 text-[11px] uppercase tracking-wide text-amber-200/90">Honesty panel</h2>
        {honesty ? (
          honesty.judgeable ? (
            <p>
              {honesty.text}{" "}
              <span className="text-terminal-muted">
                ({honesty.trades} closed trades, 90% bootstrap CI, {honesty.resamples.toLocaleString()} resamples, seed {honesty.seed})
              </span>
            </p>
          ) : (
            <p>
              <strong>{honesty.text}</strong>{" "}
              <span className="text-terminal-muted">
                ({honesty.trades} of {honesty.min_trades} closed trades needed before a confidence interval is shown)
              </span>
            </p>
          )
        ) : (
          <p className="text-terminal-muted">Reading…</p>
        )}
      </section>

      <section data-testid="stats-overall">
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">Stats (closed, net of D144 costs)</h2>
        {view ? <StatsTable rows={[{ label: "Overall", s: view.stats.overall }]} /> : null}
        <div className="mb-2 mt-3 flex gap-1">
          {(
            [
              ["by_setup", "Per setup tag"],
              ["by_coin", "Per coin"],
              ["by_month", "Per month"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              aria-pressed={group === k}
              onClick={() => setGroup(k)}
              className={`rounded px-2 py-0.5 text-[10px] uppercase ${group === k ? "bg-white/10" : "text-terminal-muted"}`}
            >
              {label}
            </button>
          ))}
        </div>
        {groups.length ? <StatsTable rows={groups.map((g) => ({ label: g.key, s: g }))} /> : null}
      </section>

      <section data-testid="open-trades">
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">Open ({open.length})</h2>
        <div className="overflow-x-auto rounded border border-terminal-border">
          <table className="min-w-full text-left font-mono text-[11px]">
            <thead className="bg-black/20 text-[10px] uppercase tracking-wide text-terminal-muted">
              <tr>
                <th className="px-2 py-1">Coin</th>
                <th className="px-2 py-1">Side</th>
                <th className="px-2 py-1">Entry</th>
                <th className="px-2 py-1">Stop</th>
                <th className="px-2 py-1">Targets</th>
                <th className="px-2 py-1">Qty</th>
                <th className="px-2 py-1">Risk $</th>
                <th className="px-2 py-1">Tag</th>
                <th className="px-2 py-1">Exit</th>
                <th className="px-2 py-1" />
              </tr>
            </thead>
            <tbody>
              {open.map((t) => (
                <tr key={t.trade_id} className="border-t border-terminal-border/70" data-testid="open-trade-row">
                  <td className="px-2 py-1">{t.symbol}</td>
                  <td className="px-2 py-1">{t.side}</td>
                  <td className="px-2 py-1">{n(t.entry_price, 4)}</td>
                  <td className="px-2 py-1">{n(t.stop_price, 4)}</td>
                  <td className="px-2 py-1">
                    {t.targets.map((x, i) => `${n(x, 4)} (${(t.targets_r[i] ?? 0).toFixed(1)}R)`).join(", ") || "—"}
                  </td>
                  <td className="px-2 py-1">{n(t.quantity, 6)}</td>
                  <td className="px-2 py-1">${n(t.risk_usd)}</td>
                  <td className="px-2 py-1">{t.setup_tag ?? "—"}</td>
                  <td className="px-2 py-1">
                    <ExitForm
                      trade={t}
                      onDone={async (msg, err) => {
                        if (err) setError(err);
                        else {
                          setNotice(msg);
                          setError(null);
                          await load();
                        }
                      }}
                    />
                  </td>
                  <td className="px-2 py-1">
                    <button
                      type="button"
                      className="text-[10px] text-rose-300"
                      onClick={async () => {
                        const reason = window.prompt(`Void ${t.symbol}? It stays in the history, flagged void. Reason:`);
                        if (reason == null) return;
                        try {
                          await voidTrade(t.trade_id, reason);
                          await load();
                        } catch (e) {
                          setError(e instanceof Error ? e.message : "void failed");
                        }
                      }}
                    >
                      Void
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section data-testid="closed-trades">
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">Closed ({closed.length})</h2>
        <div className="overflow-x-auto rounded border border-terminal-border">
          <table className="min-w-full text-left font-mono text-[11px]">
            <thead className="bg-black/20 text-[10px] uppercase tracking-wide text-terminal-muted">
              <tr>
                <th className="px-2 py-1">Coin</th>
                <th className="px-2 py-1">Side</th>
                <th className="px-2 py-1">Entry</th>
                <th className="px-2 py-1">Exit</th>
                <th className="px-2 py-1">Reason</th>
                <th className="px-2 py-1">Gross $</th>
                <th className="px-2 py-1">Fees + slippage $</th>
                <th className="px-2 py-1">Net $</th>
                <th className="px-2 py-1">R (net)</th>
                <th className="px-2 py-1">Tag</th>
                <th className="px-2 py-1">Version</th>
              </tr>
            </thead>
            <tbody>
              {closed.map((t) => (
                <tr key={t.trade_id} className="border-t border-terminal-border/70" data-testid="closed-trade-row">
                  <td className="px-2 py-1">{t.symbol}</td>
                  <td className="px-2 py-1">{t.side}</td>
                  <td className="px-2 py-1">{n(t.entry_price, 4)}</td>
                  <td className="px-2 py-1">{n(t.exit_price, 4)}</td>
                  <td className="px-2 py-1">{t.exit_reason ?? "—"}</td>
                  <td className="px-2 py-1">{signed(t.gross_pnl_usd)}</td>
                  <td className="px-2 py-1">{n(t.costs_usd)}</td>
                  <td className="px-2 py-1">{signed(t.net_pnl_usd)}</td>
                  <td className="px-2 py-1" data-testid="closed-r">
                    {signed(t.r_multiple, 2, " R")}
                  </td>
                  <td className="px-2 py-1">{t.setup_tag ?? "—"}</td>
                  <td className="px-2 py-1">v{t.version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {voided.length ? (
          <p className="mt-1 text-[11px] text-terminal-muted">{voided.length} void trade(s) kept in the history, not in the stats.</p>
        ) : null}
      </section>

      <section data-testid="export" className="flex flex-wrap gap-3 text-[11px]">
        <a className="text-sky-300 underline" href={exportUrl(false)} download>
          Export CSV (current versions)
        </a>
        <a className="text-sky-300 underline" href={exportUrl(true)} download>
          Export CSV (full edit history)
        </a>
        {view ? (
          <span className="text-terminal-muted">
            Stored in table {view.storage.table}, append-only, key ({view.storage.key.join(", ")}).
          </span>
        ) : null}
      </section>
    </div>
  );
}
