/**
 * Read-only Market Structure & Regime panel for the backtest screen.
 * Analytics only — does not affect strategy decisions.
 */
import { useMemo, useState } from "react";
import type { MarketStructureAnalytics, MarketStructureTableRow } from "../api/client";

const PAGE_SIZE = 50;

function regimeClass(label: string | null | undefined): string {
  const v = (label || "").toUpperCase();
  if (v.includes("BULL")) return "text-emerald-400";
  if (v.includes("BEAR")) return "text-red-400";
  if (v.includes("RANGE") || v.includes("COMPRESSION")) return "text-yellow-300";
  if (v.includes("CHOP")) return "text-orange-400";
  if (v.includes("TRANSITION")) return "text-sky-400";
  return "text-terminal-muted";
}

function downloadBlob(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function rowsToCsv(rows: MarketStructureTableRow[]): string {
  if (!rows.length) return "";
  const keys = Object.keys(rows[0]) as (keyof MarketStructureTableRow)[];
  const esc = (v: unknown) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [keys.join(",")];
  for (const r of rows) {
    lines.push(keys.map((k) => esc(r[k])).join(","));
  }
  return lines.join("\n");
}

type SortKey = keyof MarketStructureTableRow;

export function MarketStructurePanel({
  analytics,
  symbol,
  timeframe,
}: {
  analytics: MarketStructureAnalytics | null | undefined;
  symbol: string;
  timeframe: string;
}) {
  const [open, setOpen] = useState(true);
  const [search, setSearch] = useState("");
  const [regimeFilter, setRegimeFilter] = useState("");
  const [mtfFilter, setMtfFilter] = useState("");
  const [bosFilter, setBosFilter] = useState("");
  const [tfFilter, setTfFilter] = useState<"all" | "4h" | "1h" | "15m">("all");
  const [tradeFilter, setTradeFilter] = useState("");
  const [wlFilter, setWlFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("decision_time");
  const [sortAsc, setSortAsc] = useState(true);
  const [page, setPage] = useState(0);

  const tableRows = analytics?.table_rows || [];
  const opportunity = analytics?.regime_opportunity_summary || [];
  const tradeRegime = analytics?.trade_regime_summary || [];
  const mtfSummary = analytics?.mtf_alignment_summary || [];

  const filtered = useMemo(() => {
    let rows = [...tableRows];
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      rows = rows.filter((r) => String(r.decision_time || "").toLowerCase().includes(q));
    }
    if (regimeFilter) {
      rows = rows.filter(
        (r) =>
          r.regime_1h === regimeFilter ||
          r.regime_4h === regimeFilter ||
          r.regime_15m === regimeFilter ||
          r.market_regime === regimeFilter,
      );
    }
    if (mtfFilter) {
      rows = rows.filter((r) => r.mtf_alignment === mtfFilter);
    }
    if (bosFilter) {
      rows = rows.filter(
        (r) => r.bos_1h === bosFilter || r.bos_4h === bosFilter || r.bos_15m === bosFilter,
      );
    }
    if (tradeFilter.trim()) {
      const q = tradeFilter.trim();
      rows = rows.filter((r) => String(r.trade_id ?? "") === q);
    }
    if (wlFilter) {
      rows = rows.filter((r) => String(r.win_loss || "") === wlFilter);
    }
    // Timeframe filter emphasizes that TF's columns visually via sort preference
    if (tfFilter !== "all") {
      // Keep all rows; filter is informational for column highlight only
      void tfFilter;
    }
    rows.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      const as = av == null ? "" : String(av);
      const bs = bv == null ? "" : String(bv);
      const cmp = as < bs ? -1 : as > bs ? 1 : 0;
      return sortAsc ? cmp : -cmp;
    });
    return rows;
  }, [
    tableRows,
    search,
    regimeFilter,
    mtfFilter,
    bosFilter,
    tradeFilter,
    wlFilter,
    tfFilter,
    sortKey,
    sortAsc,
  ]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageRows = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  if (!analytics) {
    return null;
  }

  const disabled = analytics.analytics_enabled === false;
  const status15 = analytics["15m_status"] || "UNKNOWN";

  return (
    <section className="mt-4 rounded border border-terminal-border/80 p-3">
      <button
        type="button"
        className="flex w-full items-center justify-between text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <div>
          <div className="text-[10px] uppercase tracking-[0.16em] text-sky-400/90">
            Analytics only — does not affect strategy decisions
          </div>
          <h2 className="text-sm font-semibold text-terminal-text">
            Market Structure — {symbol} · {timeframe}
          </h2>
        </div>
        <span className="font-mono text-[11px] text-terminal-muted">
          {open ? "collapse" : "expand"} · {tableRows.length} bars · 15m {status15}
        </span>
      </button>

      {!open ? null : (
        <div className="mt-3 space-y-4">
          {disabled ? (
            <p className="text-sm text-terminal-muted">
              Market structure analytics disabled
              {analytics.error ? ` — ${analytics.error}` : ""}.
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2 text-[11px]">
            <input
              className="rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono"
              placeholder="Search timestamp"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
            />
            <input
              className="rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono"
              placeholder="Trade ID"
              value={tradeFilter}
              onChange={(e) => {
                setTradeFilter(e.target.value);
                setPage(0);
              }}
            />
            <select
              className="rounded border border-terminal-border bg-black/30 px-2 py-1"
              value={regimeFilter}
              onChange={(e) => {
                setRegimeFilter(e.target.value);
                setPage(0);
              }}
            >
              <option value="">All regimes</option>
              {[
                "BULL_TREND",
                "BEAR_TREND",
                "RANGE",
                "CHOPPY",
                "HIGH_VOLATILITY_TREND",
                "HIGH_VOLATILITY_RANGE",
                "LOW_VOLATILITY_COMPRESSION",
                "TRANSITION",
                "UNKNOWN",
              ].map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <select
              className="rounded border border-terminal-border bg-black/30 px-2 py-1"
              value={mtfFilter}
              onChange={(e) => {
                setMtfFilter(e.target.value);
                setPage(0);
              }}
            >
              <option value="">All MTF</option>
              {[
                "FULL_BULL_ALIGNMENT",
                "FULL_BEAR_ALIGNMENT",
                "BULLISH_HIGHER_TIMEFRAME_BUT_15M_WEAK",
                "BEARISH_HIGHER_TIMEFRAME_BUT_15M_WEAK",
                "1H_15M_BULLISH_AGAINST_4H",
                "1H_15M_BEARISH_AGAINST_4H",
                "TIMEFRAME_CONFLICT",
                "RANGE_ALIGNED",
                "TRANSITION_ALIGNED",
                "INSUFFICIENT_DATA",
              ].map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <select
              className="rounded border border-terminal-border bg-black/30 px-2 py-1"
              value={bosFilter}
              onChange={(e) => {
                setBosFilter(e.target.value);
                setPage(0);
              }}
            >
              <option value="">All BOS/CHoCH</option>
              {[
                "BULLISH_BOS",
                "BEARISH_BOS",
                "NO_CONFIRMED_BOS",
                "BULLISH_CHOCH",
                "BEARISH_CHOCH",
              ].map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <select
              className="rounded border border-terminal-border bg-black/30 px-2 py-1"
              value={wlFilter}
              onChange={(e) => {
                setWlFilter(e.target.value);
                setPage(0);
              }}
            >
              <option value="">Win/Loss</option>
              <option value="WIN">WIN</option>
              <option value="LOSS">LOSS</option>
              <option value="FLAT">FLAT</option>
            </select>
            <select
              className="rounded border border-terminal-border bg-black/30 px-2 py-1"
              value={tfFilter}
              onChange={(e) => setTfFilter(e.target.value as typeof tfFilter)}
            >
              <option value="all">TF: all</option>
              <option value="4h">TF: 4h focus</option>
              <option value="1h">TF: 1h focus</option>
              <option value="15m">TF: 15m focus</option>
            </select>
            <button
              type="button"
              className="rounded border border-terminal-border px-2 py-1 text-terminal-accent hover:bg-white/5"
              onClick={() =>
                downloadBlob(
                  `market_structure_${symbol}_${timeframe}.csv`,
                  rowsToCsv(filtered),
                  "text/csv",
                )
              }
            >
              Export CSV
            </button>
            <button
              type="button"
              className="rounded border border-terminal-border px-2 py-1 text-terminal-accent hover:bg-white/5"
              onClick={() =>
                downloadBlob(
                  `market_structure_${symbol}_${timeframe}.json`,
                  JSON.stringify(
                    {
                      metadata: analytics.metadata,
                      table_rows: filtered,
                      trade_regime_summary: tradeRegime,
                      regime_opportunity_summary: opportunity,
                      mtf_alignment_summary: mtfSummary,
                      quality_report: analytics.quality_report,
                    },
                    null,
                    2,
                  ),
                  "application/json",
                )
              }
            >
              Export JSON
            </button>
          </div>

          <div className="font-mono text-[10px] text-terminal-muted">
            analytics_v={analytics.analytics_version || "—"} · runtime{" "}
            {analytics.analytics_runtime_seconds ?? "—"}s · strategy{" "}
            {analytics.strategy_runtime_seconds ?? "—"}s · fp{" "}
            {analytics.feature_config_fingerprint || "—"} · future_violations=
            {analytics.quality_report?.bars_with_future_feature_violation ?? "—"}
          </div>

          {/* Regime Summary */}
          <div>
            <h3 className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Regime Summary (opportunity)
            </h3>
            <div className="max-h-40 overflow-auto rounded border border-terminal-border/60">
              <table className="w-full text-left text-[11px]">
                <thead className="sticky top-0 bg-black/80 text-[10px] text-terminal-muted">
                  <tr>
                    <th className="px-2 py-1">Regime</th>
                    <th className="px-2 py-1">Bars</th>
                    <th className="px-2 py-1">Accepted</th>
                    <th className="px-2 py-1">Rejected</th>
                    <th className="px-2 py-1">Signal rate</th>
                    <th className="px-2 py-1">Trade rate</th>
                  </tr>
                </thead>
                <tbody>
                  {!opportunity.length ? (
                    <tr>
                      <td colSpan={6} className="px-2 py-3 text-terminal-muted">
                        No regime opportunity rows
                      </td>
                    </tr>
                  ) : (
                    opportunity.map((o) => (
                      <tr key={o.regime} className="border-t border-terminal-border/40">
                        <td className={`px-2 py-1 font-mono ${regimeClass(o.regime)}`}>
                          {o.regime}
                        </td>
                        <td className="px-2 py-1 font-mono">{o.bars_in_regime}</td>
                        <td className="px-2 py-1 font-mono">{o.accepted_signals}</td>
                        <td className="px-2 py-1 font-mono">{o.rejected_signals}</td>
                        <td className="px-2 py-1 font-mono">
                          {o.signal_rate != null ? (o.signal_rate * 100).toFixed(2) + "%" : "—"}
                        </td>
                        <td className="px-2 py-1 font-mono">
                          {o.trade_rate != null ? (o.trade_rate * 100).toFixed(2) + "%" : "—"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Trade context / main table */}
          <div>
            <h3 className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Trade Context / Structure by bar
            </h3>
            <div className="max-h-[420px] overflow-auto rounded border border-terminal-border/60">
              <table className="w-full min-w-[1600px] border-collapse text-left text-[10px]">
                <thead className="sticky top-0 bg-black/80 text-[9px] uppercase tracking-wide text-terminal-muted">
                  <tr>
                    {(
                      [
                        ["decision_time", "Decision time"],
                        ["trade_id", "Trade ID"],
                        ["trade_status", "Status"],
                        ["entry", "Entry"],
                        ["direction", "Dir"],
                        ["regime_4h", "4h regime"],
                        ["trend_4h", "4h trend"],
                        ["structure_4h", "4h struct"],
                        ["bos_4h", "4h BOS"],
                        ["regime_1h", "1h regime"],
                        ["trend_1h", "1h trend"],
                        ["structure_1h", "1h struct"],
                        ["bos_1h", "1h BOS"],
                        ["regime_15m", "15m regime"],
                        ["trend_15m", "15m trend"],
                        ["structure_15m", "15m struct"],
                        ["bos_15m", "15m BOS"],
                        ["mtf_alignment", "MTF"],
                        ["mtf_score", "MTF score"],
                        ["volatility_state", "Vol"],
                        ["choppiness_state", "Chop"],
                        ["signal_stage", "Stage"],
                        ["final_strategy_decision", "Decision"],
                        ["rejection_reason", "Reject"],
                      ] as [SortKey, string][]
                    ).map(([key, label]) => (
                      <th key={key} className="px-1.5 py-1.5">
                        <button
                          type="button"
                          className="hover:text-terminal-text"
                          onClick={() => toggleSort(key)}
                        >
                          {label}
                          {sortKey === key ? (sortAsc ? " ↑" : " ↓") : ""}
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {!pageRows.length ? (
                    <tr>
                      <td colSpan={24} className="px-3 py-6 text-center text-terminal-muted">
                        No structure rows
                      </td>
                    </tr>
                  ) : (
                    pageRows.map((r, i) => (
                      <tr
                        key={`${r.decision_time}-${r.trade_id}-${i}`}
                        className="border-t border-terminal-border/40 hover:bg-white/[0.03]"
                      >
                        <td className="px-1.5 py-1 font-mono whitespace-nowrap">
                          {r.decision_time || "—"}
                        </td>
                        <td className="px-1.5 py-1 font-mono">{r.trade_id ?? "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.trade_status || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.entry || "—"}</td>
                        <td className={`px-1.5 py-1 font-mono ${regimeClass(r.direction)}`}>
                          {r.direction || "—"}
                        </td>
                        <td className={`px-1.5 py-1 font-mono ${regimeClass(r.regime_4h)}`}>
                          {r.regime_4h || "—"}
                        </td>
                        <td className="px-1.5 py-1 font-mono">{r.trend_4h || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.structure_4h || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.bos_4h || "—"}</td>
                        <td className={`px-1.5 py-1 font-mono ${regimeClass(r.regime_1h)}`}>
                          {r.regime_1h || "—"}
                        </td>
                        <td className="px-1.5 py-1 font-mono">{r.trend_1h || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.structure_1h || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.bos_1h || "—"}</td>
                        <td className={`px-1.5 py-1 font-mono ${regimeClass(r.regime_15m)}`}>
                          {r.regime_15m || "—"}
                        </td>
                        <td className="px-1.5 py-1 font-mono">{r.trend_15m || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.structure_15m || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.bos_15m || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.mtf_alignment || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.mtf_score ?? "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.volatility_state || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.choppiness_state || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">{r.signal_stage || "—"}</td>
                        <td className="px-1.5 py-1 font-mono">
                          {r.final_strategy_decision || "—"}
                        </td>
                        <td className="px-1.5 py-1 font-mono text-terminal-muted">
                          {r.rejection_reason || "—"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <div className="mt-2 flex items-center justify-between text-[11px] text-terminal-muted">
              <span>
                Showing {pageRows.length} of {filtered.length} (page {page + 1}/{pageCount})
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="rounded border border-terminal-border px-2 py-0.5 disabled:opacity-40"
                  disabled={page <= 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  Prev
                </button>
                <button
                  type="button"
                  className="rounded border border-terminal-border px-2 py-0.5 disabled:opacity-40"
                  disabled={page >= pageCount - 1}
                  onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                >
                  Next
                </button>
              </div>
            </div>
          </div>

          {/* Compact trade-regime rollup */}
          {tradeRegime.length ? (
            <div>
              <h3 className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
                Trade-regime rollup (accepted trades)
              </h3>
              <div className="max-h-36 overflow-auto rounded border border-terminal-border/60">
                <table className="w-full text-left text-[11px]">
                  <thead className="sticky top-0 bg-black/80 text-[10px] text-terminal-muted">
                    <tr>
                      <th className="px-2 py-1">Group</th>
                      <th className="px-2 py-1">Regime</th>
                      <th className="px-2 py-1">n</th>
                      <th className="px-2 py-1">WR</th>
                      <th className="px-2 py-1">Avg R</th>
                      <th className="px-2 py-1">Sum R</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tradeRegime.slice(0, 40).map((t, i) => (
                      <tr
                        key={`${t.group_key}-${t.regime}-${i}`}
                        className="border-t border-terminal-border/40"
                      >
                        <td className="px-2 py-1 font-mono text-terminal-muted">
                          {t.group_key || t.timeframe}
                        </td>
                        <td className={`px-2 py-1 font-mono ${regimeClass(t.regime)}`}>
                          {t.regime}
                        </td>
                        <td className="px-2 py-1 font-mono">{t.total_trades}</td>
                        <td className="px-2 py-1 font-mono">
                          {t.win_rate != null ? (t.win_rate * 100).toFixed(1) + "%" : "—"}
                        </td>
                        <td className="px-2 py-1 font-mono">
                          {t.average_R != null ? t.average_R.toFixed(3) : "—"}
                        </td>
                        <td className="px-2 py-1 font-mono">
                          {t.sum_R != null ? t.sum_R.toFixed(3) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
