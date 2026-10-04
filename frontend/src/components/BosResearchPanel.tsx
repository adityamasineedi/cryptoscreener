import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchBosCombinationDetail,
  fetchBosCombinations,
  fetchBosCombinationsCompare,
  fetchResearchOhlcvRange,
  type BosCompareResponse,
  type OhlcvRangeRow,
} from "../api/client";
import {
  normalizeResearchSymbol,
  RESEARCH_SYMBOL_PRESETS,
} from "../research/symbols";

type CompareRow = {
  combination_id: string;
  name?: string;
  description?: string;
  sample_size: number;
  tp1_hit_rate?: number | null;
  tp2_hit_rate?: number | null;
  tp3_hit_rate?: number | null;
  sl_rate?: number | null;
  average_R?: number | null;
  expectancy_R?: number | null;
  profit_factor?: number | null;
  max_drawdown_R?: number | null;
  average_MAE_R?: number | null;
  average_MFE_R?: number | null;
  status?: string;
};

type SortKey = keyof CompareRow;
type LoadState = "LOADING" | "SUCCESS_WITH_DATA" | "SUCCESS_EMPTY" | "ERROR";

const TIMEFRAMES = ["5m", "15m", "1h", "4h"] as const;
const DIRECTIONS = ["ALL", "LONG", "SHORT"] as const;
const SYMBOL_PRESETS = RESEARCH_SYMBOL_PRESETS;

function pct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function Sparkline({
  values,
  sampleSize,
  label,
}: {
  values: number[];
  sampleSize: number;
  label: string;
}) {
  if (!values.length) {
    return (
      <div className="text-xs text-terminal-muted">
        {label}: no data (n={sampleSize})
      </div>
    );
  }
  const w = 280;
  const h = 72;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => {
      const x = (i / Math.max(1, values.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 8) - 4;
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[11px] uppercase tracking-wide text-terminal-muted">
        <span>{label}</span>
        <span className="font-mono">n={sampleSize}</span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full max-w-md rounded border border-terminal-border/60 bg-black/20">
        <polyline fill="none" stroke="currentColor" strokeWidth="1.5" className="text-terminal-accent" points={pts} />
      </svg>
    </div>
  );
}

function Histogram({
  values,
  sampleSize,
  label,
}: {
  values: number[];
  sampleSize: number;
  label: string;
}) {
  if (!values.length) {
    return (
      <div className="text-xs text-terminal-muted">
        {label}: no data (n={sampleSize})
      </div>
    );
  }
  const bins = 12;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const counts = Array.from({ length: bins }, () => 0);
  for (const v of values) {
    let idx = Math.floor(((v - min) / span) * bins);
    if (idx >= bins) idx = bins - 1;
    counts[idx] += 1;
  }
  const peak = Math.max(...counts, 1);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[11px] uppercase tracking-wide text-terminal-muted">
        <span>{label}</span>
        <span className="font-mono">n={sampleSize}</span>
      </div>
      <div className="flex h-20 items-end gap-0.5 rounded border border-terminal-border/60 bg-black/20 px-1 py-1">
        {counts.map((c, i) => (
          <div
            key={i}
            className="flex-1 bg-terminal-accent/70"
            style={{ height: `${(c / peak) * 100}%` }}
            title={String(c)}
          />
        ))}
      </div>
    </div>
  );
}

export function BosResearchPanel() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>("4h");
  const [direction, setDirection] = useState<(typeof DIRECTIONS)[number]>("ALL");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [rows, setRows] = useState<CompareRow[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("LOADING");
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<BosCompareResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("sample_size");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [coverage, setCoverage] = useState<OhlcvRangeRow[]>([]);
  const [coverageReady, setCoverageReady] = useState(false);
  const didInitDates = useRef(false);

  const activeCoverage = useMemo(
    () =>
      coverage.find(
        (c) => c.symbol === symbol.toUpperCase() && c.timeframe === timeframe
      ) || null,
    [coverage, symbol, timeframe]
  );

  const coverageOk = Boolean(activeCoverage && (activeCoverage.bars || 0) > 0);

  // Load DB OHLCV availability for presets + the typed symbol, then clamp dates once.
  useEffect(() => {
    let alive = true;
    const active = normalizeResearchSymbol(symbol);
    const symbols = [
      ...new Set([...SYMBOL_PRESETS, ...(active ? [active] : [])]),
    ];
    setCoverageReady(false);
    fetchResearchOhlcvRange({
      symbols,
      timeframes: [...TIMEFRAMES],
    })
      .then((r) => {
        if (!alive) return;
        const rowsCov = r.rows || [];
        setCoverage(rowsCov);
        // Only auto-pick symbol/window on first successful load.
        if (!didInitDates.current) {
          didInitDates.current = true;
          const hit =
            rowsCov.find((c) => c.symbol === "BTCUSDT" && c.timeframe === "4h") ||
            rowsCov.find((c) => c.symbol === "BTCUSDT" && c.timeframe === "1h") ||
            rowsCov.find((c) => c.symbol === "BTCUSDT" && c.timeframe === "15m") ||
            rowsCov.find((c) => (c.bars || 0) > 0) ||
            null;
          if (hit && (hit.bars || 0) > 0) {
            setSymbol(hit.symbol);
            setTimeframe(hit.timeframe as (typeof TIMEFRAMES)[number]);
            // Prefer a recent window of available DB history (fast enough to compare).
            // Full-range 15m years can take many minutes per run.
            const endDay = hit.end?.slice(0, 10) || "";
            let startDay = hit.start?.slice(0, 10) || "";
            // Short recent window keeps compare responsive (~seconds–1 min).
            const lookbackDays =
              hit.timeframe === "5m"
                ? 5
                : hit.timeframe === "15m"
                  ? 14
                  : hit.timeframe === "1h"
                    ? 30
                    : 45;
            if (endDay) {
              const endDt = new Date(`${endDay}T00:00:00Z`);
              const recent = new Date(endDt);
              recent.setUTCDate(recent.getUTCDate() - lookbackDays);
              const recentStr = recent.toISOString().slice(0, 10);
              if (!startDay || startDay < recentStr) startDay = recentStr;
              setStart(startDay);
              setEnd(endDay);
            }
          }
        }
        setCoverageReady(true);
      })
      .catch(() => {
        if (!alive) return;
        setCoverage([]);
        setCoverageReady(true);
      });
    return () => {
      alive = false;
    };
  }, [symbol]);

  const loadCompare = useCallback(async () => {
    setLoadState("LOADING");
    setError(null);
    setRows([]);
    setMeta(null);
    setSelected(null);
    setDetail(null);
    try {
      const [list, cmp] = await Promise.all([
        fetchBosCombinations(),
        fetchBosCombinationsCompare({
          symbol,
          timeframe,
          direction: direction === "ALL" ? undefined : direction,
          start_date: start || undefined,
          end_date: end || undefined,
          // Cap for responsiveness; date filters already bound the DB slice.
          limit: start || end ? 1500 : 500,
        }),
      ]);
      const nextRows = (cmp.rows || []) as CompareRow[];
      const closed = Number(cmp.closed_trades_sample ?? 0);
      const empty =
        cmp.status === "SUCCESS_EMPTY" ||
        closed === 0 ||
        nextRows.every((r) => Number(r.sample_size || 0) === 0);
      setMeta({
        ...cmp,
        dataset: cmp.dataset || list.dataset || "BOS Combination Research",
        combinations_tested:
          cmp.combinations_tested ?? list.combinations?.length ?? nextRows.length,
      });
      setRows(nextRows);
      setLoadState(empty ? "SUCCESS_EMPTY" : "SUCCESS_WITH_DATA");
      // Prefer combinations that actually produced setups on available data.
      setSortKey("sample_size");
      setSortDir("desc");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Research request failed");
      setLoadState("ERROR");
    }
  }, [symbol, timeframe, direction, start, end]);

  // After coverage loads, auto-run once on the short DB window.
  useEffect(() => {
    if (!coverageReady) return;
    void loadCompare();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional one-shot after coverage
  }, [coverageReady]);

  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") {
        return sortDir === "asc" ? av - bv : bv - av;
      }
      return sortDir === "asc"
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
    return copy;
  }, [rows, sortKey, sortDir]);

  async function openDetail(id: string) {
    setSelected(id);
    setDetailLoading(true);
    try {
      const d = await fetchBosCombinationDetail(id, {
        symbol,
        timeframe,
        direction: direction === "ALL" ? undefined : direction,
        start_date: start || undefined,
        end_date: end || undefined,
        limit: 500,
      });
      setDetail(d);
    } catch (e) {
      setDetail({ error: e instanceof Error ? e.message : "detail failed" });
    } finally {
      setDetailLoading(false);
    }
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "combination_id" || key === "name" ? "asc" : "desc");
    }
  }

  const outcomes = (detail?.outcomes || {}) as Record<string, number | undefined>;
  const sampleSize = Number(detail?.sample_size ?? outcomes.sample_size ?? 0);
  const rDist = (detail?.r_distribution || []) as number[];
  const equity = (detail?.equity_curve_r || []) as number[];
  const dd = (detail?.drawdown_curve_r || []) as number[];
  const freq = (detail?.setup_frequency || []) as Array<{ date: string; count: number }>;
  const definition = (detail?.definition || detail?.combination || {}) as Record<string, unknown>;
  const gates = (detail?.gates || definition.gates || {}) as Record<string, boolean>;

  const period = meta?.historical_period || meta?.date_filter || {};
  const periodStart =
    (period.start_date as string | undefined) ||
    (period.period_start as string | undefined) ||
    start ||
    "—";
  const periodEnd =
    (period.end_date_inclusive as string | undefined) ||
    (period.period_end as string | undefined) ||
    end ||
    "—";
  const mt = meta?.multiple_testing_detail;
  const loading = loadState === "LOADING";

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto p-4">
      <div className="mb-4">
        <h1 className="font-display text-lg font-semibold text-terminal-text">
          BOS Combination Research
        </h1>
        <p className="mt-1 max-w-3xl text-xs text-terminal-muted">
          Research comparison of historical BOS condition combinations on real OHLCV.
          Not a ranking. Not a profitability claim. Live signal engine is unchanged.
          This page is not Candle-1/Candle-2 V2.
        </p>
      </div>

      <section className="mb-3 rounded border border-terminal-border/80 bg-black/20 px-3 py-2">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
          Available DB OHLCV (research symbols)
        </div>
        {coverage.length === 0 ? (
          <div className="text-xs text-terminal-muted">
            No coverage loaded yet
            {!coverageReady ? "…" : " — fetch history first."}
          </div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {coverage
              .filter((c) => (c.bars || 0) > 0)
              .map((c) => {
                const active =
                  c.symbol === symbol.toUpperCase() && c.timeframe === timeframe;
                return (
                  <button
                    key={`${c.symbol}-${c.timeframe}`}
                    type="button"
                    title={`${c.bars} bars`}
                    onClick={() => {
                      setSymbol(c.symbol);
                      setTimeframe(c.timeframe as (typeof TIMEFRAMES)[number]);
                      if (c.start) setStart(c.start.slice(0, 10));
                      if (c.end) setEnd(c.end.slice(0, 10));
                    }}
                    className={`rounded border px-2 py-1 font-mono text-[11px] ${
                      active
                        ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                        : "border-terminal-border text-terminal-muted hover:text-terminal-text"
                    }`}
                  >
                    {c.symbol.replace("USDT", "")}/{c.timeframe}{" "}
                    {c.start?.slice(0, 10)}→{c.end?.slice(0, 10)}
                  </button>
                );
              })}
            {coverage.every((c) => !(c.bars || 0)) ? (
              <span className="text-xs text-amber-200">
                No OHLCV in DB for BTC/ETH/SOL —{" "}
                <Link to="/ohlcv-history" className="underline text-terminal-accent">
                  open OHLCV History
                </Link>
              </span>
            ) : null}
          </div>
        )}
        {activeCoverage && coverageOk ? (
          <div className="mt-1.5 font-mono text-[11px] text-terminal-muted">
            Selected series: {activeCoverage.bars.toLocaleString()} bars ·{" "}
            {activeCoverage.start?.slice(0, 10)} → {activeCoverage.end?.slice(0, 10)}
          </div>
        ) : coverageReady && !coverageOk ? (
          <div className="mt-1.5 text-[11px] text-amber-200">
            No DB history for {symbol} {timeframe}.{" "}
            <Link
              to={`/ohlcv-history?symbols=${encodeURIComponent(symbol)}&tfs=${encodeURIComponent(
                timeframe
              )}&until=${encodeURIComponent(start || "2023-01-01")}`}
              className="underline text-terminal-accent"
            >
              Fetch into DB
            </Link>
          </div>
        ) : null}
      </section>

      <section className="mb-4 grid gap-3 rounded border border-terminal-border/80 p-3 term:grid-cols-2 term-md:grid-cols-4">
        <div className="text-xs">
          <span className="mb-1 block text-terminal-muted">Symbol</span>
          <div className="mb-1.5 flex flex-wrap gap-1">
            {SYMBOL_PRESETS.map((s) => {
              const cov = coverage.find((c) => c.symbol === s && c.timeframe === timeframe);
              const has = Boolean(cov && (cov.bars || 0) > 0);
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => {
                    setSymbol(s);
                    if (cov?.start) setStart(cov.start.slice(0, 10));
                    if (cov?.end) setEnd(cov.end.slice(0, 10));
                  }}
                  className={`rounded border px-2 py-1 font-mono text-xs ${
                    symbol === s
                      ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                      : has
                        ? "border-terminal-border text-terminal-muted"
                        : "border-terminal-border/50 text-terminal-muted/50"
                  }`}
                  title={has ? `${cov!.bars} bars in DB` : "No DB bars for this TF"}
                >
                  {s.replace("USDT", "")}
                  {has ? "" : " · —"}
                </button>
              );
            })}
          </div>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
          />
        </div>
        <label className="text-xs">
          <span className="mb-1 block text-terminal-muted">Period start (UTC day)</span>
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
          />
        </label>
        <label className="text-xs">
          <span className="mb-1 block text-terminal-muted">Period end (UTC day, inclusive)</span>
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
          />
        </label>
        <div className="flex flex-col justify-end gap-1.5">
          <button
            type="button"
            disabled={loading}
            onClick={() => void loadCompare()}
            className="rounded border border-terminal-accent bg-terminal-accent/15 px-3 py-1.5 text-sm text-terminal-accent disabled:opacity-50"
          >
            {loading ? "Scanning… (can take ~30–90s)" : "Find strategies on this data"}
          </button>
          {activeCoverage?.start && activeCoverage?.end ? (
            <button
              type="button"
              disabled={loading}
              onClick={() => {
                setStart(activeCoverage.start!.slice(0, 10));
                setEnd(activeCoverage.end!.slice(0, 10));
              }}
              className="rounded border border-terminal-border px-3 py-1 text-[11px] text-terminal-muted hover:text-terminal-text disabled:opacity-50"
            >
              Use full DB range
            </button>
          ) : null}
        </div>
      </section>

      <section className="mb-4 flex flex-wrap gap-4">
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">Timeframe</div>
          <div className="flex flex-wrap gap-1">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                type="button"
                onClick={() => setTimeframe(tf)}
                className={`rounded border px-2 py-1 font-mono text-xs ${
                  timeframe === tf
                    ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                    : "border-terminal-border text-terminal-muted"
                }`}
              >
                {tf.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">Direction</div>
          <div className="flex flex-wrap gap-1">
            {DIRECTIONS.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDirection(d)}
                className={`rounded border px-2 py-1 font-mono text-xs ${
                  direction === d
                    ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                    : "border-terminal-border text-terminal-muted"
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
      </section>

      {meta ? (
        <div className="mb-3 grid gap-1 rounded border border-terminal-border/80 bg-black/20 px-3 py-2 font-mono text-[11px] text-terminal-muted term-md:grid-cols-2">
          <div>
            Dataset:{" "}
            <span className="text-terminal-text">{meta.dataset || "BOS Combination Research"}</span>
          </div>
          <div>
            Not dataset:{" "}
            <span className="text-terminal-text">
              {meta.not_dataset === "candle12_v2"
                ? "Candle-1/Candle-2 V2"
                : String(meta.not_dataset || "candle12_v2")}
            </span>
          </div>
          <div>
            Research period:{" "}
            <span className="text-terminal-text">
              {String(periodStart).slice(0, 10)} → {String(periodEnd).slice(0, 10)}
            </span>
          </div>
          <div>
            Timezone: <span className="text-terminal-text">{meta.timezone || "UTC"}</span>
            {" · "}
            Sample:{" "}
            <span className="text-terminal-text">
              {Number(meta.closed_trades_sample ?? 0)} closed trades
            </span>
          </div>
          <div>
            Candle source:{" "}
            <span className="text-terminal-text">{meta.candle_source || "—"}</span>
          </div>
          <div>
            combinations_tested={meta.combinations_tested ?? "—"}
            {" · "}
            parameter_variants_tested={meta.parameter_variants_tested ?? meta.parameters_tested ?? "—"}
          </div>
        </div>
      ) : null}

      {meta?.multiple_testing_flag ? (
        <div className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          MULTIPLE_TESTING_RISK — {mt?.configurations_explored ?? "?"} configurations explored
          (combinations_tested={mt?.combinations_tested ?? meta.combinations_tested}
          {" · "}
          parameter_variants_tested=
          {mt?.parameter_variants_tested ?? meta.parameter_variants_tested ?? meta.parameters_tested}
          ). Interpret cautiously. Not a ranking.
        </div>
      ) : null}

      {loadState === "SUCCESS_EMPTY" ? (
        <div className="mb-3 rounded border border-terminal-border/80 bg-black/30 px-3 py-2 text-xs text-terminal-muted">
          NO QUALIFYING DATA FOR SELECTED FILTERS — sample_size=0 for every combination.
          Metrics are shown as — (not 0.00). This is BOS Combination Research on the selected
          window, not Candle-1/Candle-2 V2 universe totals.
        </div>
      ) : null}

      <section className="mb-6">
        <div className="mb-2 flex items-baseline justify-between gap-2">
          <h2 className="text-[11px] uppercase tracking-wide text-terminal-muted">
            Combination Comparison
          </h2>
          <span className="font-mono text-[11px] text-terminal-muted">
            {loadState === "LOADING"
              ? "Loading…"
              : loadState === "ERROR"
                ? "Error"
                : loadState === "SUCCESS_EMPTY"
                  ? "Empty · 0 closed trades"
                  : `${sorted.length} combinations`}
            {meta?.elapsed_seconds != null && loadState !== "LOADING"
              ? ` · ${Number(meta.elapsed_seconds).toFixed(2)}s`
              : ""}
          </span>
        </div>
        {error ? <div className="mb-2 text-xs text-rose-300">{error}</div> : null}
        <div className="overflow-x-auto rounded border border-terminal-border/80">
          <table className="min-w-full text-left text-xs">
            <thead className="bg-terminal-panel/80 text-[11px] uppercase tracking-wide text-terminal-muted">
              <tr>
                {(
                  [
                    ["combination_id", "Combination"],
                    ["sample_size", "Setups"],
                    ["tp1_hit_rate", "TP1 %"],
                    ["tp2_hit_rate", "TP2 %"],
                    ["sl_rate", "SL %"],
                    ["average_R", "Avg R"],
                    ["expectancy_R", "Expectancy R"],
                    ["profit_factor", "Profit Factor"],
                    ["max_drawdown_R", "Max DD"],
                    ["average_MAE_R", "MAE"],
                    ["average_MFE_R", "MFE"],
                  ] as Array<[SortKey, string]>
                ).map(([key, label]) => (
                  <th key={key} className="px-2 py-2 font-medium">
                    <button type="button" onClick={() => toggleSort(key)} className="hover:text-terminal-text">
                      {label}
                      {sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={11} className="px-3 py-6 text-center text-terminal-muted">
                    LOADING — waiting for research response…
                  </td>
                </tr>
              ) : null}
              {!loading &&
                sorted.map((row) => {
                  const n = Number(row.sample_size || 0);
                  return (
                    <tr
                      key={row.combination_id}
                      className={`cursor-pointer border-t border-terminal-border/50 hover:bg-white/5 ${
                        selected === row.combination_id ? "bg-terminal-accent/10" : ""
                      }`}
                      onClick={() => openDetail(row.combination_id)}
                    >
                      <td className="px-2 py-2 font-mono text-terminal-text">
                        <div>{row.name || row.combination_id}</div>
                        <div className="text-[10px] text-terminal-muted">{row.combination_id}</div>
                      </td>
                      <td className="px-2 py-2 font-mono">{n}</td>
                      <td className="px-2 py-2 font-mono">{n ? pct(row.tp1_hit_rate) : "—"}</td>
                      <td className="px-2 py-2 font-mono">{n ? pct(row.tp2_hit_rate) : "—"}</td>
                      <td className="px-2 py-2 font-mono">{n ? pct(row.sl_rate) : "—"}</td>
                      <td className="px-2 py-2 font-mono">{n ? num(row.average_R) : "—"}</td>
                      <td className="px-2 py-2 font-mono">{n ? num(row.expectancy_R) : "—"}</td>
                      <td className="px-2 py-2 font-mono">{n ? num(row.profit_factor) : "—"}</td>
                      <td className="px-2 py-2 font-mono">{n ? num(row.max_drawdown_R) : "—"}</td>
                      <td className="px-2 py-2 font-mono">{n ? num(row.average_MAE_R) : "—"}</td>
                      <td className="px-2 py-2 font-mono">{n ? num(row.average_MFE_R) : "—"}</td>
                    </tr>
                  );
                })}
              {!loading && loadState === "SUCCESS_EMPTY" && sorted.length === 0 ? (
                <tr>
                  <td colSpan={11} className="px-3 py-6 text-center text-terminal-muted">
                    NO QUALIFYING DATA FOR SELECTED FILTERS
                  </td>
                </tr>
              ) : null}
              {!loading && loadState === "ERROR" ? (
                <tr>
                  <td colSpan={11} className="px-3 py-6 text-center text-rose-300">
                    ERROR — research request failed
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-terminal-muted">
          Column sorting is a UI convenience only — it does not imply superiority.
          Date filters use UTC calendar days with start ≤ ts &lt; end+1day.
        </p>
      </section>

      {selected ? (
        <section className="mb-8 space-y-4 rounded border border-terminal-border/80 p-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="font-display text-base font-semibold">{selected} detail</h2>
            <span className="font-mono text-xs text-terminal-muted">sample_size={sampleSize}</span>
          </div>
          {detailLoading ? (
            <div className="text-xs text-terminal-muted">Loading detail…</div>
          ) : (
            <>
              <div>
                <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">Definition</div>
                <p className="text-sm text-terminal-text">
                  {String(definition.description || definition.name || "—")}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {["BOS", "Trend", "Impulse", "Pullback", "RVOL", "S/D", "R:R"].map((g) => {
                    const on = Boolean(gates[g]);
                    return (
                      <span
                        key={g}
                        className={`rounded border px-2 py-0.5 font-mono text-[11px] ${
                          on
                            ? "border-terminal-accent/50 text-terminal-accent"
                            : "border-terminal-border text-terminal-muted"
                        }`}
                      >
                        {g}: {on ? "ON" : "off"}
                      </span>
                    );
                  })}
                </div>
              </div>

              <div className="grid gap-3 term-md:grid-cols-2">
                <div className="rounded border border-terminal-border/60 p-3">
                  <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
                    Outcomes (n={sampleSize})
                  </div>
                  <div className="grid grid-cols-2 gap-1 font-mono text-xs">
                    <div>TP1 hits: {sampleSize ? outcomes.tp1_hits ?? "—" : "—"}</div>
                    <div>TP2 hits: {sampleSize ? outcomes.tp2_hits ?? "—" : "—"}</div>
                    <div>TP3 hits: {sampleSize ? outcomes.tp3_hits ?? "—" : "—"}</div>
                    <div>SL hits: {sampleSize ? outcomes.sl_hits ?? "—" : "—"}</div>
                    <div>Ambiguous: {sampleSize ? outcomes.ambiguous_count ?? "—" : "—"}</div>
                  </div>
                </div>
                <div className="rounded border border-terminal-border/60 p-3">
                  <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
                    MAE / MFE
                  </div>
                  <div className="font-mono text-xs">
                    avg MAE_R:{" "}
                    {sampleSize
                      ? num((detail?.mae_mfe as { average_MAE_R?: number } | undefined)?.average_MAE_R)
                      : "—"}
                    <br />
                    avg MFE_R:{" "}
                    {sampleSize
                      ? num((detail?.mae_mfe as { average_MFE_R?: number } | undefined)?.average_MFE_R)
                      : "—"}
                  </div>
                  <div className="mt-2 text-[11px] text-terminal-muted">
                    Regime: REGIME_NOT_AVAILABLE
                  </div>
                </div>
              </div>

              <div className="grid gap-4 term-md:grid-cols-2">
                <Sparkline values={equity} sampleSize={sampleSize} label="Cumulative R" />
                <Sparkline values={dd} sampleSize={sampleSize} label="Drawdown curve (R)" />
                <Histogram values={rDist} sampleSize={sampleSize} label="R distribution" />
                <div>
                  <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
                    Setup frequency · n={sampleSize}
                  </div>
                  <div className="max-h-24 overflow-auto font-mono text-[11px] text-terminal-muted">
                    {freq.length
                      ? freq.map((f) => (
                          <div key={f.date}>
                            {f.date}: {f.count}
                          </div>
                        ))
                      : "No frequency points"}
                  </div>
                </div>
              </div>

              <div className="grid gap-3 term-md:grid-cols-2">
                <div className="rounded border border-terminal-border/60 p-3">
                  <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
                    Long / short breakdown
                  </div>
                  <pre className="overflow-auto text-[11px] text-terminal-muted">
                    {JSON.stringify(detail?.by_direction || {}, null, 2)}
                  </pre>
                </div>
                <div className="rounded border border-terminal-border/60 p-3">
                  <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
                    Out-of-sample (evaluation only)
                  </div>
                  <pre className="max-h-40 overflow-auto text-[11px] text-terminal-muted">
                    {JSON.stringify(
                      (detail?.out_of_sample as { periods?: unknown } | undefined)?.periods || {},
                      null,
                      2
                    )}
                  </pre>
                </div>
              </div>

              <div className="rounded border border-terminal-border/60 p-3">
                <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
                  Walk-forward windows
                </div>
                <pre className="max-h-40 overflow-auto text-[11px] text-terminal-muted">
                  {JSON.stringify(
                    (detail?.walk_forward as { windows?: unknown } | undefined)?.windows || [],
                    null,
                    2
                  )}
                </pre>
              </div>
            </>
          )}
        </section>
      ) : null}
    </div>
  );
}
