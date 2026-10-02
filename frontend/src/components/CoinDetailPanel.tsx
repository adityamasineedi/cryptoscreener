import { useEffect, useState } from "react";
import { fetchCoinDetail } from "../api/client";
import { useMarketStore } from "../store/marketStore";
import type { FreshValue, ScreenerRow } from "../types/market";
import { FreshCell, StatusBadge } from "./FreshCell";
import { Spinner } from "./Spinner";
import { TradePlanTab } from "./TradePlanTab";
import {
  formatRatio,
  normalizeTvlFresh,
  statusLaneLabel,
} from "../utils/freshDisplay";

const detailTabs = [
  "Overview",
  "Performance",
  "Technicals",
  "SETUP",
  "TRADE PLAN",
  "Valuation",
  "Derivatives",
  "Addresses",
  "Transactions",
  "Sentiment",
] as const;

type Tab = (typeof detailTabs)[number];

function fmtNum(v: number | string, digits = 2) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "Data unavailable";
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function asFresh(v: unknown): FreshValue | undefined {
  if (v && typeof v === "object" && "status" in (v as object)) {
    return v as FreshValue;
  }
  return undefined;
}

export function CoinDetailPanel({
  onClose,
  compactHeader = false,
}: {
  onClose?: () => void;
  compactHeader?: boolean;
} = {}) {
  const selected = useMarketStore((s) => s.selectedSymbol);
  const liveRow = useMarketStore((s) => (selected ? s.rows[selected] : null));
  const [tab, setTab] = useState<Tab>("Overview");
  const [tabLoading, setTabLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<{
    structure?: unknown;
    zones?: unknown;
    indicators?: unknown;
    tabs?: Record<string, unknown>;
    signals?: { entry_exit?: unknown; tech_rating?: unknown };
  } | null>(null);

  function switchTab(next: Tab) {
    if (next === tab) return;
    setTabLoading(true);
    setTab(next);
    window.setTimeout(() => setTabLoading(false), 160);
  }

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      setDetailLoading(false);
      return;
    }
    let alive = true;
    setDetailLoading(true);
    fetchCoinDetail(selected)
      .then((d) => {
        if (alive) {
          setDetail({
            structure: d.structure,
            zones: d.zones,
            indicators: d.indicators,
            tabs: d.tabs,
            signals: d.signals,
          });
        }
      })
      .catch(() => {
        if (alive) setDetail(null);
      })
      .finally(() => {
        if (alive) setDetailLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [selected]);

  if (!selected || !liveRow) {
    return (
      <aside className="coin-detail border-l border-terminal-border bg-terminal-panel/70">
        <div className="p-4">
          <div className="flex items-center justify-between gap-2">
            <h2 className="font-display text-sm font-semibold">Coin Detail</h2>
            {onClose ? (
              <button
                type="button"
                onClick={onClose}
                className="rounded border border-terminal-border px-2 py-0.5 text-[11px] text-terminal-muted hover:text-terminal-text"
              >
                Close
              </button>
            ) : null}
          </div>
          <p className="mt-3 text-sm text-terminal-muted">Select a row to inspect live metrics.</p>
        </div>
      </aside>
    );
  }

  const row = liveRow;
  const tabs = detail?.tabs || {};
  const entryExit = detail?.signals?.entry_exit as Record<string, unknown> | undefined;
  const techRating = detail?.signals?.tech_rating as Record<string, unknown> | undefined;

  return (
    <aside className="coin-detail border-l border-terminal-border bg-terminal-panel/70">
      <div className={`shrink-0 border-b border-terminal-border ${compactHeader ? "p-3" : "p-4"}`}>
        <div className="flex min-w-0 items-start justify-between gap-2">
          <div className="min-w-0 overflow-hidden">
            <div className="truncate font-display text-xl font-bold">{row.base_asset}</div>
            <div className="truncate font-mono text-xs text-terminal-muted">{row.symbol}</div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <StatusBadge status={row.price.status} />
            {onClose ? (
              <button
                type="button"
                onClick={onClose}
                className="rounded border border-terminal-border px-2 py-0.5 text-[11px] text-terminal-muted hover:text-terminal-text"
              >
                Close
              </button>
            ) : null}
          </div>
        </div>
        <div className="mt-3 min-w-0">
          <FreshCell
            fv={row.price}
            format={(v) => Number(v).toLocaleString(undefined, { maximumFractionDigits: 6 })}
            className="text-2xl font-semibold"
          />
          <div className="mt-0.5 text-[9px] text-terminal-muted">
            Ticker/mark · {statusLaneLabel(row.price.status, "price")}
            {row.price.source ? ` · ${row.price.source}` : ""}
            {" — chart uses OHLCV candle close (may differ)"}
          </div>
          <div className="mt-2">
            <div className="mb-0.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-terminal-muted">
              <span>24h ticker change</span>
              <StatusBadge status={row.change_24h_pct.status} compact />
            </div>
            <FreshCell
              fv={row.change_24h_pct}
              format={(v) => `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`}
              className={
                typeof row.change_24h_pct.value === "number"
                  ? row.change_24h_pct.value >= 0
                    ? "text-terminal-up"
                    : "text-terminal-down"
                  : ""
              }
            />
            <div className="text-[9px] text-terminal-muted">
              Exchange rolling 24h — not the HISTORICAL 1D performance row
            </div>
          </div>
        </div>
      </div>
      <div className="coin-tabs gap-1 border-b border-terminal-border px-2 py-2">
        {detailTabs.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => switchTab(t)}
            className={`shrink-0 whitespace-nowrap rounded px-2 py-1 text-[11px] ${
              tab === t ? "bg-white/10 text-terminal-text" : "text-terminal-muted"
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="coin-detail-content relative space-y-3 p-4 text-sm">
        {tabLoading || detailLoading ? (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-terminal-panel/70">
            <Spinner label={detailLoading ? "Loading detail…" : `Loading ${tab}…`} />
          </div>
        ) : null}
        {tab === "Overview" && (
          <OverviewTab row={row} entryExit={entryExit} techRating={techRating} />
        )}
        {tab === "Performance" && (
          <div className="space-y-2">
            <p className="text-[10px] text-terminal-muted">
              HISTORICAL — computed from 1D OHLCV closes only. Never substitutes 24h ticker change.
              Each metric shows source, timestamp, and status.
            </p>
            <MetricMap
              data={(tabs.performance as Record<string, unknown>) || {
                performance_1d: row.performance_1d,
                performance_7d: row.performance_7d,
                performance_30d: row.performance_30d,
                performance_90d: row.performance_90d,
                performance_180d: row.performance_180d,
                performance_1y: row.performance_1y,
                performance_ytd: row.performance_ytd,
              }}
              empty="Waiting for 1D OHLCV history (backfill in progress)..."
              percentKeys={/performance_|change_|volume_change/i}
            />
          </div>
        )}
        {tab === "Technicals" && (
          <>
            <Field label="Williams %R" fv={row.williams_r} />
            <Field label="Volatility" fv={row.volatility} />
            <Field label="RVOL" fv={row.relative_volume} />
            <Field label="Structure" fv={row.market_structure ?? row.structure} />
            <Field label="BOS" fv={row.bos} />
            <Field label="CHOCH" fv={row.choch} />
            <ConditionsBlock title="Entry / Exit" payload={entryExit} />
            <RatingBlock payload={techRating} />
            <JsonBlock
              title="Indicators"
              data={detail?.indicators}
              empty="Waiting for closed candles..."
            />
          </>
        )}
        {tab === "SETUP" && (
          <SetupTab
            payload={(tabs.setup as Record<string, unknown>) || undefined}
            row={row}
          />
        )}
        {tab === "TRADE PLAN" && (
          <TradePlanTab
            payload={(tabs.setup as Record<string, unknown>) || undefined}
            row={row}
          />
        )}
        {tab === "Valuation" && (
          <>
            <Field label="Market Cap" fv={row.market_cap} format={fmtNum} />
            <Field label="FDV" fv={row.fdv} format={fmtNum} />
            <Field label="Market Rank" fv={row.market_rank} />
            <div className="text-[10px] text-terminal-muted">
              Screener rank: {row.screener_rank ?? row.rank ?? "—"} (table position)
            </div>
            <Field label="Vol/MCap" fv={row.volume_mcap} format={(v) => formatRatio(v, true)} />
            <Field label="MCap/FDV" fv={row.mcap_fdv} format={(v) => formatRatio(v, false)} />
            <Field label="TVL" fv={normalizeTvlFresh(row.tvl)} format={fmtNum} />
            <Field
              label="Circ / Max"
              fv={asFresh((tabs.valuation as Record<string, unknown> | undefined)?.circ_max_ratio)}
              format={(v) => formatRatio(v, false)}
            />
            <Field
              label="Circ / Total"
              fv={asFresh((tabs.valuation as Record<string, unknown> | undefined)?.circ_total_ratio)}
              format={(v) => formatRatio(v, false)}
            />
            <Field label="NVT (true on-chain)" fv={row.nvt} />
            <Field label="Velocity (true on-chain)" fv={row.velocity} />
            {(tabs.valuation as { EXPERIMENTAL?: Record<string, unknown> } | undefined)
              ?.EXPERIMENTAL ? (
              <div className="mt-3 rounded border border-amber-500/30 p-2">
                <div className="mb-1 text-[10px] uppercase text-amber-300">Experimental</div>
                <p className="mb-2 text-[9px] text-terminal-muted">
                  Exchange-volume proxies — not true on-chain NVT/velocity.
                </p>
                <MetricMap
                  data={
                    (tabs.valuation as { EXPERIMENTAL: Record<string, unknown> }).EXPERIMENTAL
                  }
                  empty="No experimental proxies"
                  percentKeys={/velocity_proxy|volume_mcap/i}
                />
              </div>
            ) : null}
          </>
        )}
        {tab === "Derivatives" && (
          <>
            <Field label="Open Interest" fv={row.open_interest} format={fmtNum} />
            <Field
              label="OI Change"
              fv={row.oi_change_pct ?? row.oi_change_24h}
              format={(v) => `${Number(v).toFixed(2)}%`}
            />
            <Field
              label="Funding"
              fv={row.funding_rate}
              format={(v) => `${(Number(v) * 100).toFixed(4)}%`}
            />
            <Field label="Long Liq" fv={row.long_liquidations} format={fmtNum} />
            <Field label="Short Liq" fv={row.short_liquidations} format={fmtNum} />
          </>
        )}
        {tab === "Addresses" && (
          <MetricMap
            data={(tabs.addresses as Record<string, unknown>) || {}}
            empty="WAITING — on-chain provider not configured"
            note="Holder concentration shown only when an indexer is configured. Not a decentralization claim."
          />
        )}
        {tab === "Transactions" && (
          <MetricMap
            data={(tabs.transactions as Record<string, unknown>) || {}}
            empty="WAITING — on-chain provider not configured"
          />
        )}
        {tab === "Sentiment" && (
          <MetricMap
            data={(tabs.sentiment as Record<string, unknown>) || {}}
            empty="WAITING — sentiment provider not configured"
            note="Never fabricated. Configure Santiment/LunarCrush-style API to populate."
          />
        )}
      </div>
    </aside>
  );
}

function SetupTab({
  payload,
  row,
}: {
  payload?: Record<string, unknown>;
  row: ScreenerRow;
}) {
  if (!payload) {
    return (
      <p className="text-xs text-terminal-muted">
        SETUP: Waiting for OHLCV / setup engine…
      </p>
    );
  }
  const analysis = (payload.analysis as Record<string, unknown>) || payload;
  const trend = (payload.trend || analysis.trend || {}) as Record<string, unknown>;
  const structure = (payload.structure || {}) as Record<string, unknown>;
  const setupState = (payload.setup_state || {}) as Record<string, unknown>;
  const trade = (payload.trade_plan || {}) as Record<string, unknown>;
  const risk = (payload.risk || analysis.risk_management || {}) as Record<string, unknown>;
  const conditions = (payload.conditions || analysis.conditions || []) as Array<
    Record<string, unknown>
  >;
  const explanation = (payload.explanation || analysis.explanation || []) as string[];
  const deps = (payload.data_dependencies || analysis.data_dependencies || {}) as Record<
    string,
    string
  >;
  const entry = (trade.entry || analysis.entry || {}) as Record<string, unknown>;
  const stop = (trade.stop || analysis.stop || {}) as Record<string, unknown>;
  const targets = (trade.targets || analysis.targets || []) as Array<Record<string, unknown>>;
  const rr = (trade.risk_reward || analysis.risk_reward || {}) as Record<string, unknown>;
  const bos = (structure.bos || analysis.bos || {}) as Record<string, unknown>;
  const choch = (structure.choch || analysis.choch || {}) as Record<string, unknown>;
  const impulse = (setupState.impulse || analysis.impulse || {}) as Record<string, unknown>;
  const pullback = (setupState.pullback || analysis.pullback || {}) as Record<string, unknown>;
  const retest = (setupState.retest || analysis.retest || {}) as Record<string, unknown>;
  const marketSignal = String(
    payload.market_signal || analysis.market_signal || row.market_signal?.value || "WAITING"
  );
  const conf = payload.confirmation_strength ?? analysis.confirmation_strength;
  const denom = payload.confirmation_denominator ?? analysis.confirmation_denominator ?? 10;
  const msReasons = (payload.market_signal_reasons ||
    (analysis.market_signal_payload as { reasons?: string[] } | undefined)?.reasons ||
    []) as string[];
  const msConds = (payload.market_signal_conditions ||
    (analysis.market_signal_payload as { conditions?: Record<string, string> } | undefined)
      ?.conditions ||
    {}) as Record<string, string>;
  const msLabel =
    marketSignal === "STRONG_BUY"
      ? "STRONG BUY"
      : marketSignal === "STRONG_SELL"
        ? "STRONG SELL"
        : marketSignal;
  const msColor = marketSignal.includes("BUY")
    ? "text-terminal-up"
    : marketSignal.includes("SELL")
      ? "text-terminal-down"
      : "text-terminal-muted";

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-terminal-muted">
        Structural classification only — not a trade instruction or profitability claim.
        For a trader-facing summary (entry / SL / targets / risk), open the{" "}
        <span className="text-terminal-text">TRADE PLAN</span> tab.
      </p>
      <div className="rounded border border-terminal-border/60 p-3">
        <div className="text-[11px] uppercase tracking-wide text-terminal-muted">MARKET SIGNAL</div>
        <div className={`mt-1 font-display text-2xl font-bold ${msColor}`}>{msLabel}</div>
        <div className="mt-1 font-mono text-xs text-terminal-muted">
          Confirmation: {conf != null ? `${conf} / ${denom}` : "—"}
        </div>
        <p className="mt-1 text-[10px] text-terminal-muted">
          {String(payload.market_signal_reason || analysis.market_signal_reason || "")}
        </p>
        <ul className="mt-2 space-y-0.5 text-[11px]">
          {msReasons.slice(0, 12).map((line, i) => (
            <li key={i} className="font-mono text-terminal-muted">
              {line}
            </li>
          ))}
        </ul>
        {Object.keys(msConds).length > 0 ? (
          <div className="mt-2 grid grid-cols-2 gap-1 font-mono text-[10px]">
            {Object.entries(msConds).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2">
                <span className="text-terminal-muted">{k}</span>
                <span
                  className={
                    v === "PASS"
                      ? "text-terminal-up"
                      : v === "FAIL"
                        ? "text-terminal-down"
                        : "text-terminal-muted"
                  }
                >
                  {v}
                </span>
              </div>
            ))}
          </div>
        ) : null}
        <Field label="Market Signal" fv={row.market_signal} />
        <Field label="Confirmation" fv={row.confirmation_strength} />
      </div>
      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 text-[11px] uppercase text-terminal-muted">SETUP</div>
        <Field label="Setup status" fv={row.setup_signal} />
      </div>
      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 text-[11px] uppercase text-terminal-muted">TREND</div>
        <div className="grid grid-cols-2 gap-1 font-mono text-xs">
          {Object.entries(trend).map(([tf, v]) => (
            <div key={tf} className="flex justify-between gap-2">
              <span className="text-terminal-muted">{String(tf).toUpperCase()}</span>
              <span>{String(v ?? "WAITING")}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 text-[11px] uppercase text-terminal-muted">STRUCTURE</div>
        <div className="space-y-1 font-mono text-xs">
          <div>BOS: {String(bos.direction ?? bos.state ?? "NONE")}</div>
          <div>CHOCH: {String(choch.direction ?? choch.state ?? "NONE")}</div>
          <div>Swing High: {String((analysis as { swings?: Array<{ swing_type?: string; price?: number }> }).swings?.filter((s) => s.swing_type === "HIGH").slice(-1)[0]?.price ?? "—")}</div>
          <div>Swing Low: {String((analysis as { swings?: Array<{ swing_type?: string; price?: number }> }).swings?.filter((s) => s.swing_type === "LOW").slice(-1)[0]?.price ?? "—")}</div>
        </div>
      </div>
      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 text-[11px] uppercase text-terminal-muted">SETUP</div>
        <div className="space-y-1 font-mono text-xs">
          <div>Impulse: {String(impulse.quality ?? impulse.is_impulse ?? "WAITING")}</div>
          <div>Pullback: {String(pullback.pullback_state ?? "WAITING")}</div>
          <div>Retest: {String(retest.state ?? (retest.retest ? "CONFIRMED" : "WAITING"))}</div>
        </div>
      </div>
      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 text-[11px] uppercase text-terminal-muted">BUY / SELL / TAKE PROFIT</div>
        <div className="space-y-1 font-mono text-xs">
          {(() => {
            const dir = String(
              entry.direction || trade.direction || analysis.direction || ""
            ).toUpperCase();
            const side =
              dir === "LONG" ? "BUY" : dir === "SHORT" ? "SELL" : "ENTRY";
            const sideColor =
              side === "BUY"
                ? "text-terminal-up"
                : side === "SELL"
                  ? "text-terminal-down"
                  : "text-terminal-muted";
            return (
              <div className={sideColor}>
                {side}: {String(entry.entry_price ?? "WAITING")}{" "}
                <span className="text-terminal-muted">
                  ({String((entry.entry_type ?? dir) || "—")})
                </span>
              </div>
            );
          })()}
          <div className="text-terminal-down">
            STOP: {String(stop.final_stop ?? "—")}
          </div>
          {targets.map((t) => (
            <div key={String(t.name)} style={{ color: "#6cb6ff" }}>
              TAKE PROFIT {String(t.name)}: {String(t.target_price)} ({String(t.r_multiple)}R) —{" "}
              {String(t.structural_reason ?? t.target_type)}
            </div>
          ))}
          <div>R:R: {String(rr.best_R ?? "—")} ({String(rr.RISK_REWARD ?? "")})</div>
        </div>
      </div>
      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 text-[11px] uppercase text-terminal-muted">RISK</div>
        <div className="space-y-1 font-mono text-xs">
          <div>Account risk: {String(risk.max_risk_amount ?? "—")}</div>
          <div>Position size: {String(risk.final_quantity ?? "—")}</div>
          <div>Leverage: {String(risk.leverage ?? "—")}</div>
          <div>
            Liquidation distance:{" "}
            {risk.distance_to_liquidation != null
              ? `${risk.distance_to_liquidation}%`
              : "N/A"}
          </div>
          {Array.isArray(risk.warnings) && risk.warnings.length > 0 ? (
            <div className="text-amber-300">Warnings: {(risk.warnings as string[]).join(", ")}</div>
          ) : null}
        </div>
      </div>
      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 text-[11px] uppercase text-terminal-muted">EXPLANATION</div>
        <ul className="space-y-1 text-[11px]">
          {conditions.map((c) => (
            <li key={String(c.id)} className="flex justify-between gap-2 font-mono">
              <span>{String(c.label)}</span>
              <span
                className={
                  c.verdict === "PASS"
                    ? "text-terminal-up"
                    : c.verdict === "FAIL"
                      ? "text-terminal-down"
                      : "text-terminal-muted"
                }
              >
                {String(c.verdict)}
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-2 space-y-0.5 text-[10px] text-terminal-muted">
          {explanation.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>
      </div>
      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 text-[11px] uppercase text-terminal-muted">DATA</div>
        <div className="space-y-0.5 font-mono text-[11px]">
          {Object.entries(deps).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-2">
              <span className="text-terminal-muted">{k}</span>
              <span>{v}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function OverviewTab({
  row,
  entryExit,
  techRating,
}: {
  row: ScreenerRow;
  entryExit?: Record<string, unknown>;
  techRating?: Record<string, unknown>;
}) {
  return (
    <>
      <div className="mb-2 space-y-1.5 rounded border border-terminal-border/50 p-2 text-[10px] text-terminal-muted">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="shrink-0">Price:</span>
          <StatusBadge status={row.price.status} />
          <span className="truncate">{statusLaneLabel(row.price.status, "price")}</span>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="shrink-0">Volume:</span>
          <StatusBadge status={row.quote_volume_24h?.status || "WAITING"} />
          <span className="truncate">
            {statusLaneLabel(row.quote_volume_24h?.status || "WAITING", "volume")}
          </span>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="shrink-0">Performance:</span>
          <StatusBadge status={row.performance_1d?.status || "HISTORICAL"} />
          <span className="truncate">
            {statusLaneLabel(row.performance_1d?.status || "HISTORICAL", "performance")}
          </span>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="shrink-0">Market Cap:</span>
          <StatusBadge status={row.market_cap?.status || "WAITING"} />
          <span className="truncate">
            {statusLaneLabel(row.market_cap?.status || "WAITING", "fundamental")}
          </span>
        </div>
      </div>
      <Field label="Volume (24h quote)" fv={row.quote_volume_24h} format={fmtNum} />
      <Field label="Market Cap" fv={row.market_cap} format={fmtNum} />
      <Field label="FDV" fv={row.fdv} format={fmtNum} />
      <Field label="Vol/MCap" fv={row.volume_mcap} format={(v) => formatRatio(v, true)} />
      <Field label="TVL" fv={normalizeTvlFresh(row.tvl)} format={fmtNum} />
      <Field label="Category" fv={row.category} />
      <Field label="OI" fv={row.open_interest} format={fmtNum} />
      <Field label="Funding" fv={row.funding_rate} format={(v) => `${(Number(v) * 100).toFixed(4)}%`} />
      <Field label="RVOL" fv={row.relative_volume} />
      <Field label="Entry/Exit" fv={row.entry_exit_state ?? asFresh((entryExit as { state?: unknown })?.state)} />
      <Field label="Tech Rating" fv={row.tech_rating ?? asFresh((techRating as { label?: unknown })?.label)} />
      <ConditionsBlock title="Entry / Exit conditions" payload={entryExit} />
      <RatingBlock payload={techRating} />
    </>
  );
}

function ConditionsBlock({
  title,
  payload,
}: {
  title: string;
  payload?: Record<string, unknown>;
}) {
  if (!payload) {
    return <p className="text-xs text-terminal-muted">{title}: Waiting...</p>;
  }
  const state = asFresh(payload.state);
  const entry = (payload.entry_conditions as Array<Record<string, unknown>>) || [];
  const exit = (payload.exit_conditions as Array<Record<string, unknown>>) || [];
  return (
    <div className="min-w-0 space-y-2 rounded border border-terminal-border/60 p-2">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="truncate text-[11px] uppercase text-terminal-muted">{title}</div>
        {state ? <StatusBadge status={state.status} /> : null}
      </div>
      <div className="truncate font-mono text-xs">{state?.value ?? "WAITING"}</div>
      <CondList label="Entry" items={entry} />
      <CondList label="Exit" items={exit} />
    </div>
  );
}

function CondList({
  label,
  items,
}: {
  label: string;
  items: Array<Record<string, unknown>>;
}) {
  if (!items.length) return null;
  return (
    <div className="min-w-0">
      <div className="mb-1 text-[10px] uppercase text-terminal-muted">{label}</div>
      <ul className="space-y-1">
        {items.map((c) => {
          const passed = c.passed;
          const mark =
            passed === true ? "PASS" : passed === false ? "FAIL" : "N/A";
          const color =
            passed === true
              ? "text-terminal-up"
              : passed === false
                ? "text-terminal-down"
                : "text-terminal-muted";
          return (
            <li key={String(c.id)} className="flex min-w-0 gap-2 font-mono text-[10px]">
              <span className={`shrink-0 ${color}`}>{mark}</span>
              <span className="min-w-0 truncate">{String(c.description)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function RatingBlock({ payload }: { payload?: Record<string, unknown> }) {
  if (!payload) {
    return <p className="text-xs text-terminal-muted">Tech rating: Waiting...</p>;
  }
  const label = asFresh(payload.label);
  const components = (payload.components as Record<string, { score?: number | null; note?: string }>) || {};
  const missing = (payload.missing_components as string[]) || [];
  return (
    <div className="min-w-0 space-y-2 rounded border border-terminal-border/60 p-2">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="truncate text-[11px] uppercase text-terminal-muted">Tech Rating</div>
        {label ? <StatusBadge status={label.status} /> : null}
      </div>
      <div className="truncate font-mono text-xs">{label?.value ?? "WAITING"}</div>
      {payload.aggregate_score != null && (
        <div className="text-[10px] text-terminal-muted">
          Aggregate: {Number(payload.aggregate_score).toFixed(3)}
        </div>
      )}
      <ul className="space-y-1">
        {Object.entries(components).map(([k, v]) => (
          <li key={k} className="flex min-w-0 justify-between gap-2 font-mono text-[10px]">
            <span className="min-w-0 truncate">{k}</span>
            <span className="shrink-0 text-terminal-muted">
              {v.score == null ? "WAITING" : v.score.toFixed(2)}
              {v.note ? ` · ${v.note}` : ""}
            </span>
          </li>
        ))}
      </ul>
      {missing.length > 0 && (
        <div className="truncate text-[10px] text-terminal-muted">Missing: {missing.join(", ")}</div>
      )}
      <div className="text-[10px] text-terminal-muted">
        Labels: BULLISH_BIAS / NEUTRAL / BEARISH_BIAS / INSUFFICIENT_DATA only — never Strong Buy
      </div>
    </div>
  );
}

function MetricMap({
  data,
  empty,
  note,
  percentKeys,
}: {
  data: Record<string, unknown>;
  empty: string;
  note?: string;
  /** Keys matching this regex format numeric values as signed percents */
  percentKeys?: RegExp;
}) {
  const entries = Object.entries(data).filter(
    ([k]) => k !== "provider" && k !== "note" && k !== "methodology" && k !== "asset_metadata"
  );
  if (!entries.length) {
    return <p className="text-xs text-terminal-muted">{empty}</p>;
  }
  return (
    <div className="min-w-0 space-y-2">
      {note && <p className="text-[10px] text-terminal-muted">{note}</p>}
      {entries.map(([k, v]) => {
        const fv = asFresh(v);
        if (fv) {
          const asPct = percentKeys?.test(k);
          return (
            <Field
              key={k}
              label={k.replace(/_/g, " ")}
              fv={fv}
              format={
                asPct
                  ? (val) => {
                      const n = Number(val);
                      if (!Number.isFinite(n)) return "—";
                      return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
                    }
                  : /mcap|ratio|proxy|velocity/i.test(k)
                    ? (val) => formatRatio(val, /velocity|mcap|ratio/i.test(k))
                    : fmtNum
              }
            />
          );
        }
        if (v == null) return null;
        if (typeof v === "object") {
          return <JsonBlock key={k} title={k} data={v} empty={empty} />;
        }
        return (
          <div key={k} className="min-w-0">
            <div className="truncate text-[11px] uppercase text-terminal-muted">{k}</div>
            <div className="truncate font-mono text-xs">{String(v)}</div>
          </div>
        );
      })}
    </div>
  );
}

function Field({
  label,
  fv,
  format,
}: {
  label: string;
  fv?: FreshValue | ScreenerRow[keyof ScreenerRow] | undefined;
  format?: (v: number | string) => string;
}) {
  const fresh = fv as FreshValue | undefined;
  const updated = fresh?.timestamp
    ? new Date(fresh.timestamp).toLocaleTimeString()
    : null;
  return (
    <div className="min-w-0">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="truncate text-[11px] uppercase text-terminal-muted">{label}</div>
        {fresh && typeof fresh === "object" && "status" in fresh ? (
          <StatusBadge status={fresh.status} />
        ) : null}
      </div>
      <FreshCell fv={fresh} format={format} />
      <div className="mt-0.5 flex min-w-0 flex-wrap gap-x-2 text-[9px] text-terminal-muted">
        {fresh?.source ? <span className="truncate">Source: {fresh.source}</span> : null}
        {updated ? <span className="shrink-0">Updated: {updated}</span> : null}
      </div>
      {fresh?.methodology ? (
        <div className="mt-0.5 truncate text-[9px] text-terminal-muted">Method: {fresh.methodology}</div>
      ) : null}
    </div>
  );
}

function JsonBlock({
  title,
  data,
  empty,
}: {
  title: string;
  data: unknown;
  empty: string;
}) {
  if (
    data == null ||
    (Array.isArray(data) && data.length === 0) ||
    (typeof data === "object" && Object.keys(data as object).length === 0)
  ) {
    return <p className="text-xs text-terminal-muted">{empty}</p>;
  }
  return (
    <div className="min-w-0">
      <div className="mb-1 truncate text-[11px] uppercase text-terminal-muted">{title}</div>
      <pre className="max-h-40 overflow-auto rounded bg-black/30 p-2 font-mono text-[10px] text-terminal-muted">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}
