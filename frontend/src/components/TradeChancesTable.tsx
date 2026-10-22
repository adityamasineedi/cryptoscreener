import { useEffect, useMemo, useState } from "react";
import type { PaperOpportunity } from "../api/client";
import { opportunityAction } from "../paper/paperLabels";
import { fetchScreenerContext, type ContextRow, type ScreenerContext } from "../screenerTool/client";
import {
  applyChanceFilter,
  arrowGlyph,
  contextBySymbol,
  costLabel,
  DEFAULT_FILTER,
  sortChances,
  type ChanceFilter,
  type ChanceSort,
} from "../screenerTool/chances";

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function tierClass(tier: string): string {
  if (tier === "READY") return "text-emerald-300";
  if (tier === "NEAR") return "text-amber-300";
  if (tier === "FORMING") return "text-sky-300";
  if (tier === "BLOCKED") return "text-rose-300";
  return "text-terminal-muted";
}

const CONTEXT_REFRESH_MS = 60_000;

function contextKey(rows: PaperOpportunity[]): string {
  return rows.map((r) => `${r.symbol}|${r.direction}|${r.entry_price}|${r.stop_price}|${r.tier}`).join(";");
}

function HtfCell({ c, tf }: { c: ContextRow | undefined; tf: string }) {
  const st = c?.htf?.[tf];
  if (!st || st.status !== "OK") {
    return (
      <span className="text-terminal-muted" title={st ? `${tf}: ${st.status} (${st.bars} closed bars)` : "no data"}>
        {tf} —
      </span>
    );
  }
  const tone = st.trend === "UP" ? "text-emerald-300" : st.trend === "DOWN" ? "text-rose-300" : "text-amber-200";
  return (
    <span
      className={tone}
      data-testid={`htf-${tf}`}
      title={`${tf} closed bars only (last closed bar ${st.last_closed_bar}): close vs EMA50 ${st.close_vs_ema50}, EMA20 vs EMA50 ${st.ema20_vs_ema50}`}
    >
      {tf} {arrowGlyph(st.close_vs_ema50)}
      {arrowGlyph(st.ema20_vs_ema50)}
    </span>
  );
}

export function BtcContextLine({ ctx, error }: { ctx: ScreenerContext | null; error: string | null }) {
  if (error) {
    return (
      <p className="mb-2 text-[11px] text-amber-200/90" data-testid="btc-context">
        Screener context unavailable: {error}
      </p>
    );
  }
  if (!ctx) return null;
  const reg = ctx.btc.regime;
  const fund = ctx.btc.funding;
  return (
    <p className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-terminal-muted" data-testid="btc-context">
      <span title="Live BTC daily switch (btc_daily_regime.daily_trend via RegimeClock), closed daily bars">
        BTC regime: <span className="text-terminal-text">{reg.trend ?? "no data"}</span>
        {reg.as_of_bar ? ` (daily bar ${reg.as_of_bar.slice(0, 10)})` : ""}
        {reg.short_history ? " · short history" : ""}
      </span>
      <span data-testid="btc-funding" title={fund.reason ?? "D175: 72 h mean of 8 h settlements vs 0.01%/8h"}>
        {fund.status === "OK" ? fund.text : "funding: no data"}
      </span>
    </p>
  );
}

export function TradeChancesTable({
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
  const [ctx, setCtx] = useState<ScreenerContext | null>(null);
  const [ctxError, setCtxError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ChanceFilter>(DEFAULT_FILTER);
  const [sort, setSort] = useState<ChanceSort>("default");
  const key = contextKey(rows);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), CONTEXT_REFRESH_MS);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!rows.length) return;
    let alive = true;
    fetchScreenerContext(
      rows.map((r) => ({
        symbol: r.symbol,
        direction: r.direction,
        tier: r.tier,
        status: r.status,
        entry_price: r.entry_price,
        stop_price: r.stop_price,
        setup_trend: r.setup_trend,
        bos: r.bos,
        pullback: r.pullback,
        retest: r.retest,
      })),
    )
      .then((next) => {
        if (!alive) return;
        setCtx(next);
        setCtxError(null);
      })
      .catch((e) => {
        if (alive) setCtxError(e instanceof Error ? e.message : "context failed");
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);

  const byCoin = useMemo(() => contextBySymbol(ctx?.rows), [ctx]);
  const shown = useMemo(() => sortChances(applyChanceFilter(rows, byCoin, filter), byCoin, sort), [rows, byCoin, filter, sort]);

  return (
    <div>
      <BtcContextLine ctx={ctx} error={ctxError} />
      <div className="mb-2 flex flex-wrap items-center gap-3 text-[11px] text-terminal-muted" data-testid="chance-controls">
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={filter.costOkOnly}
            onChange={(e) => setFilter((f) => ({ ...f, costOkOnly: e.target.checked }))}
          />
          Cost OK only (≤ 0.25 R)
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={filter.htfAgreesOnly}
            onChange={(e) => setFilter((f) => ({ ...f, htfAgreesOnly: e.target.checked }))}
          />
          HTF agrees only
        </label>
        <label className="flex items-center gap-1">
          Coin
          <input
            aria-label="Filter coin"
            value={filter.coin}
            onChange={(e) => setFilter((f) => ({ ...f, coin: e.target.value }))}
            className="w-24 rounded border border-terminal-border bg-black/20 px-1 py-0.5 text-terminal-text"
          />
        </label>
        <label className="flex items-center gap-1">
          Sort
          <select
            aria-label="Sort chances"
            value={sort}
            onChange={(e) => setSort(e.target.value as ChanceSort)}
            className="rounded border border-terminal-border bg-black/20 px-1 py-0.5 text-terminal-text"
          >
            <option value="default">Tier (default)</option>
            <option value="cost">Cost as R (low first)</option>
            <option value="stop">Stop distance (wide first)</option>
            <option value="coin">Coin</option>
          </select>
        </label>
        <span title={ctx?.rules.cost}>
          Cost = 2 × (0.05% taker + 0.02% slippage) / stop distance; 5 bp slippage on AAVE/APT/ARB/DOT/ETC/OP/FIL (D144).
        </span>
      </div>
      {!shown.length ? (
        <div className="rounded border border-dashed border-terminal-border px-3 py-6 text-center text-[12px] text-terminal-muted">
          {rows.length ? "No rows match the filters." : empty}
        </div>
      ) : (
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
                <th className="px-2 py-1.5">Stop dist</th>
                <th className="px-2 py-1.5">Cost</th>
                <th className="px-2 py-1.5" title="Closed bars only: close vs EMA50, EMA20 vs EMA50">HTF 4h / 1d</th>
                <th className="px-2 py-1.5">Research</th>
                <th className="px-2 py-1.5">Pass</th>
                <th className="px-2 py-1.5">Chance</th>
                <th className="px-2 py-1.5">Action</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const c = byCoin.get(r.symbol.toUpperCase());
                const tooHigh = c?.cost.cost_too_high === true;
                return (
                  <tr
                    key={`${r.symbol}-${r.tier}-${r.status}`}
                    className={`border-t border-terminal-border/70 ${tooHigh ? "opacity-50 grayscale" : ""}`}
                    data-testid="chance-row"
                    data-cost-too-high={tooHigh ? "1" : "0"}
                  >
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
                    <td className="px-2 py-1.5" data-testid="chance-stop-dist">
                      {c?.cost.stop_distance_pct != null ? `${c.cost.stop_distance_pct.toFixed(2)}%` : "—"}
                    </td>
                    <td
                      className={`px-2 py-1.5 whitespace-nowrap ${tooHigh ? "text-terminal-muted" : "text-terminal-text"}`}
                      data-testid="chance-cost"
                      title={
                        c
                          ? `round trip ${c.cost.round_trip_pct.toFixed(2)}% (slippage ${c.cost.slippage_bps} bp${
                              c.cost.thin_coin ? ", D144 thin coin" : ""
                            })`
                          : "context not loaded"
                      }
                    >
                      {costLabel(c)}
                      {c?.cost.thin_coin ? " · 5bp" : ""}
                      {tooHigh ? <span className="ml-1 rounded bg-white/10 px-1 text-[9px]">COST TOO HIGH</span> : null}
                    </td>
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <HtfCell c={c} tf="4h" /> <HtfCell c={c} tf="1d" />
                      {c?.htf_agrees === true ? <span className="ml-1 text-emerald-300" title="HTF agrees">✓</span> : null}
                    </td>
                    <td className="max-w-[12rem] px-2 py-1.5" data-testid="chance-badge">
                      {c ? (
                        <span
                          className="rounded border border-terminal-border px-1 text-[9px] text-terminal-muted"
                          title={`${c.badge.setup_label}: ${c.badge.detail} ${c.badge.not_a_signal}`}
                        >
                          {c.badge.text}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-2 py-1.5">{r.pass_count}/8</td>
                    <td className="max-w-[14rem] truncate px-2 py-1.5 text-terminal-muted" title={r.chance}>
                      {r.chance}
                    </td>
                    <td className="px-2 py-1.5" data-testid="opportunity-action">
                      {opportunityAction(r, { autoOn, watcherOwns, legacyAutoOn })}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
