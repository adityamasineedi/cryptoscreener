import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchLongStrategyBacktest,
  fetchResearchOhlcvRange,
  type OhlcvRangeRow,
  type StrategyMatrixResponse,
  type StrategyMatrixRow,
  type StrategyTradeRow,
} from "../api/client";

type PeriodMode = "lookback" | "dates";

const YEAR_PRESETS = [
  { id: "2023", start: "2023-01-01", end: "2023-12-31", label: "2023" },
  { id: "2024", start: "2024-01-01", end: "2024-12-31", label: "2024" },
  { id: "2025", start: "2025-01-01", end: "2025-12-31", label: "2025" },
  { id: "2026ytd", start: "2026-01-01", end: "", label: "2026 YTD" },
] as const;

type RunProgress = {
  done: number;
  total: number;
  current: string;
  startedAt: number;
  /** Rolling average ms per completed cell */
  avgMsPerCell: number | null;
};

const SYMBOL_OPTIONS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;
const TF_OPTIONS = ["15m", "1h", "4h"] as const;
const LOOKBACKS = [
  { id: "12d", label: "~12 days", limit: 1200, hint: "15m ≈ 12.5d · 1h ≈ 50d" },
  { id: "30d", label: "~30 days", limit: 2880, hint: "15m ≈ 30d · 1h ≈ 120d" },
  { id: "60d", label: "~60 days", limit: 5760, hint: "15m ≈ 60d · 1h ≈ 240d" },
  { id: "90d", label: "~90 days", limit: 8640, hint: "15m ≈ 90d" },
  { id: "max", label: "Max (~127d 15m)", limit: 12200, hint: "Uses full DB tail" },
] as const;

function pct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function money(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${v.toFixed(2)}`;
}

function px(v: number | null | undefined, digits = 4): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  if (Math.abs(v) >= 1000) return v.toFixed(2);
  if (Math.abs(v) >= 1) return v.toFixed(Math.min(digits, 4));
  return v.toFixed(6);
}

function fmtTime(v: string | null | undefined): string {
  if (!v) return "—";
  // Keep UTC ISO compact: 2026-10-01T04:00:00+00:00 → 2026-10-01 04:00 UTC
  return v.replace("T", " ").replace(/\+00:00$/, " UTC").replace(/Z$/, " UTC");
}

function Sparkline({ values }: { values: number[] }) {
  if (!values.length) {
    return <span className="text-terminal-muted">—</span>;
  }
  const w = 120;
  const h = 28;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => {
      const x = (i / Math.max(1, values.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 4) - 2;
      return `${x},${y}`;
    })
    .join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-7 w-[120px]">
      <polyline
        fill="none"
        stroke={up ? "#3ecf8e" : "#f07178"}
        strokeWidth="1.5"
        points={pts}
      />
    </svg>
  );
}

function toggleInList<T extends string>(list: T[], value: T): T[] {
  return list.includes(value)
    ? list.filter((x) => x !== value)
    : [...list, value];
}

function formatDuration(ms: number): string {
  const sec = Math.max(0, Math.round(ms / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function BacktestPanel() {
  const [symbols, setSymbols] = useState<string[]>(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
  const [timeframes, setTimeframes] = useState<string[]>(["15m", "1h"]);
  const [direction, setDirection] = useState<"LONG" | "SHORT">("LONG");
  const [lookbackId, setLookbackId] = useState<(typeof LOOKBACKS)[number]["id"]>("60d");
  const [customLimit, setCustomLimit] = useState("");
  const [periodMode, setPeriodMode] = useState<PeriodMode>("lookback");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [principalUsd, setPrincipalUsd] = useState(1000);
  const [riskPct, setRiskPct] = useState(2);
  const [riskUsd, setRiskUsd] = useState(20);
  const [takerFeePct, setTakerFeePct] = useState(0.04);
  const [makerFeePct, setMakerFeePct] = useState(0.02);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StrategyMatrixResponse | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<OhlcvRangeRow[]>([]);

  const limit = useMemo(() => {
    // Date mode loads by calendar bounds from Postgres (full series slice).
    // Keep a high safety cap for warmup/unbounded paths.
    if (periodMode === "dates" && (start || end)) return 20000;
    const custom = Number(customLimit);
    if (customLimit && Number.isFinite(custom) && custom >= 50) {
      return Math.min(Math.floor(custom), 20000);
    }
    return LOOKBACKS.find((l) => l.id === lookbackId)?.limit ?? 5760;
  }, [customLimit, lookbackId, periodMode, start, end]);

  const effectiveStart = periodMode === "dates" ? start || undefined : undefined;
  const effectiveEnd = periodMode === "dates" ? end || undefined : undefined;

  useEffect(() => {
    let alive = true;
    fetchResearchOhlcvRange({ symbols, timeframes })
      .then((r) => {
        if (alive) setCoverage(r.rows || []);
      })
      .catch(() => {
        if (alive) setCoverage([]);
      });
    return () => {
      alive = false;
    };
  }, [symbols, timeframes]);

  const coverageWarning = useMemo(() => {
    if (periodMode !== "dates" || !start) return null;
    if (!coverage.length) return null;
    const wantStart = start;
    const wantEnd = end || "9999-12-31";
    const gaps: string[] = [];
    for (const row of coverage) {
      if (!row.bars || !row.start || !row.end) {
        gaps.push(`${row.symbol} ${row.timeframe}: no OHLCV in DB`);
        continue;
      }
      const dbStart = row.start.slice(0, 10);
      const dbEnd = row.end.slice(0, 10);
      if (wantEnd < dbStart || wantStart > dbEnd) {
        gaps.push(
          `${row.symbol} ${row.timeframe}: DB has ${dbStart} → ${dbEnd} (no overlap with ${wantStart} → ${end || "…"})`
        );
      } else if (wantStart < dbStart || (end && end > dbEnd)) {
        gaps.push(
          `${row.symbol} ${row.timeframe}: partial — DB ${dbStart} → ${dbEnd}`
        );
      }
    }
    return gaps.length ? gaps : null;
  }, [periodMode, start, end, coverage]);

  // Tick clock while running so ETA/elapsed stay live.
  useEffect(() => {
    if (!loading) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [loading]);

  const progressView = useMemo(() => {
    if (!progress || progress.total <= 0) return null;
    const pct = Math.min(100, Math.round((progress.done / progress.total) * 100));
    const elapsedMs = Math.max(0, nowMs - progress.startedAt);
    const remaining = Math.max(0, progress.total - progress.done);
    let etaMs: number | null = null;
    if (progress.done > 0 && progress.avgMsPerCell != null) {
      etaMs = progress.avgMsPerCell * remaining;
    } else if (progress.done > 0) {
      etaMs = (elapsedMs / progress.done) * remaining;
    }
    return {
      pct,
      done: progress.done,
      total: progress.total,
      current: progress.current,
      elapsedLabel: formatDuration(elapsedMs),
      etaLabel: etaMs == null ? "…" : formatDuration(etaMs),
    };
  }, [progress, nowMs]);

  const run = useCallback(async () => {
    if (!symbols.length || !timeframes.length) {
      setError("Pick at least one symbol and timeframe");
      return;
    }
    if (periodMode === "dates" && !start && !end) {
      setError("Custom dates mode needs a start and/or end day (UTC)");
      return;
    }
    const cells = symbols.flatMap((symbol) =>
      timeframes.map((timeframe) => ({ symbol, timeframe }))
    );
    const startedAt = Date.now();
    setLoading(true);
    setError(null);
    setSelectedKey(null);
    setResult(null);
    setProgress({
      done: 0,
      total: cells.length,
      current: `${cells[0].symbol} ${cells[0].timeframe}`,
      startedAt,
      avgMsPerCell: null,
    });

    const collected: StrategyMatrixRow[] = [];
    let meta: StrategyMatrixResponse | null = null;
    let cellDurations = 0;

    try {
      for (let i = 0; i < cells.length; i++) {
        const { symbol, timeframe } = cells[i];
        setProgress((p) =>
          p
            ? {
                ...p,
                current: `${symbol} ${timeframe}`,
                done: i,
              }
            : p
        );
        const cellStarted = Date.now();
        const payload = await fetchLongStrategyBacktest({
          symbols: [symbol],
          timeframes: [timeframe],
          direction,
          combination_id: "COMBO_02",
          limit,
          risk_usd: riskUsd,
          taker_fee_pct: takerFeePct,
          maker_fee_pct: makerFeePct,
          include_trades: true,
          start_date: effectiveStart,
          end_date: effectiveEnd,
        });
        cellDurations += Date.now() - cellStarted;

        if (payload.status === "NOT_FOUND" || payload.status === "ERROR") {
          throw new Error(payload.reason || payload.status);
        }
        meta = payload;
        for (const row of payload.rows || []) collected.push(row);

        const done = i + 1;
        setProgress({
          done,
          total: cells.length,
          current:
            done < cells.length
              ? `${cells[done].symbol} ${cells[done].timeframe}`
              : "Finishing",
          startedAt,
          avgMsPerCell: cellDurations / done,
        });
        // Show partial matrix as cells finish.
        setResult({
          ...payload,
          rows: [...collected],
          symbols,
          timeframes,
          elapsed_seconds: (Date.now() - startedAt) / 1000,
        });
      }

      const firstWithTrades = collected.find(
        (r) => (r.trades?.length || 0) > 0 || r.sample_size > 0
      );
      if (firstWithTrades) {
        setSelectedKey(`${firstWithTrades.symbol}:${firstWithTrades.timeframe}`);
      }
      if (meta) {
        setResult({
          ...meta,
          rows: collected,
          symbols,
          timeframes,
          elapsed_seconds: (Date.now() - startedAt) / 1000,
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Backtest failed");
      if (!collected.length) setResult(null);
    } finally {
      setLoading(false);
      setProgress(null);
    }
  }, [
    symbols,
    timeframes,
    direction,
    limit,
    riskUsd,
    takerFeePct,
    makerFeePct,
    periodMode,
    start,
    end,
    effectiveStart,
    effectiveEnd,
  ]);

  const rows = result?.rows ?? [];
  const selected = rows.find(
    (r) => `${r.symbol}:${r.timeframe}` === selectedKey
  );
  const pooled = useMemo(() => {
    let n = 0;
    let sumR = 0;
    let pnl = 0;
    let pnlNet = 0;
    let fees = 0;
    for (const r of rows) {
      const s = Number(r.sample_size || 0);
      if (!s) continue;
      if (r.average_R != null) {
        n += s;
        sumR += Number(r.average_R) * s;
      }
      pnl += Number(r.pnl_usd || 0);
      pnlNet += Number(r.pnl_usd_net || 0);
      fees += Number(r.fees_usd || 0);
    }
    return {
      n,
      avgR: n ? sumR / n : null,
      pnl,
      pnlNet,
      fees,
    };
  }, [rows]);

  const selectedCapital = useMemo(() => {
    const net = selected?.pnl_usd_net ?? null;
    const fees = selected?.fees_usd ?? 0;
    const ending = net == null ? null : principalUsd + net;
    const retPct =
      net == null || !principalUsd ? null : (net / principalUsd) * 100;
    return { net, fees, ending, retPct };
  }, [selected, principalUsd]);

  function applyRiskFromPrincipal(nextPrincipal: number, nextRiskPct: number) {
    const p = Math.max(0, nextPrincipal);
    const rp = Math.max(0, nextRiskPct);
    setPrincipalUsd(p);
    setRiskPct(rp);
    setRiskUsd(Math.max(1, Math.round((p * rp) / 100)));
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto p-4">
      <div className="mb-4">
        <h1 className="font-display text-lg font-semibold text-terminal-text">
          Strategy Backtest
        </h1>
        <p className="mt-1 max-w-3xl text-xs text-terminal-muted">
          Run the HL Long Path A playbook (Trend + BOS / COMBO_02) on real Postgres
          OHLCV. Same engine as the research scripts — not a profitability claim.
        </p>
      </div>

      <section className="mb-4 rounded border border-terminal-border/80 p-3">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded border border-terminal-accent/40 bg-terminal-accent/10 px-2 py-1 font-mono text-terminal-accent">
            COMBO_02 · TREND_BOS
          </span>
          <span className="text-terminal-muted">
            {direction === "LONG"
              ? "Structure filter: HH + HL (longs only)"
              : "Structure filter: LH + LL (shorts)"}
          </span>
        </div>

        <div className="grid gap-3 term-md:grid-cols-2 term-lg:grid-cols-4">
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Symbols
            </div>
            <div className="flex flex-wrap gap-1">
              {SYMBOL_OPTIONS.map((s) => {
                const on = symbols.includes(s);
                return (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setSymbols((prev) => toggleInList(prev, s))}
                    className={`rounded border px-2 py-1 font-mono text-xs ${
                      on
                        ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                        : "border-terminal-border text-terminal-muted"
                    }`}
                  >
                    {s.replace("USDT", "")}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Timeframes
            </div>
            <div className="flex flex-wrap gap-1">
              {TF_OPTIONS.map((tf) => {
                const on = timeframes.includes(tf);
                return (
                  <button
                    key={tf}
                    type="button"
                    onClick={() => setTimeframes((prev) => toggleInList(prev, tf))}
                    className={`rounded border px-2 py-1 font-mono text-xs ${
                      on
                        ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                        : "border-terminal-border text-terminal-muted"
                    }`}
                  >
                    {tf}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Direction
            </div>
            <div className="flex flex-wrap gap-1">
              {(["LONG", "SHORT"] as const).map((d) => (
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

          <label className="text-xs">
            <span className="mb-1 block text-terminal-muted">Principal $ (starting equity)</span>
            <input
              type="number"
              min={1}
              step={100}
              value={principalUsd}
              onChange={(e) =>
                applyRiskFromPrincipal(Number(e.target.value) || 0, riskPct)
              }
              className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
            />
          </label>
        </div>

        <div className="mt-3 grid gap-3 term-md:grid-cols-3">
          <label className="text-xs">
            <span className="mb-1 block text-terminal-muted">Risk % of principal (1R)</span>
            <input
              type="number"
              min={0.1}
              step={0.5}
              value={riskPct}
              onChange={(e) =>
                applyRiskFromPrincipal(principalUsd, Number(e.target.value) || 0)
              }
              className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
            />
          </label>
          <label className="text-xs">
            <span className="mb-1 block text-terminal-muted">Risk $ per trade (1R)</span>
            <input
              type="number"
              min={1}
              step={1}
              value={riskUsd}
              onChange={(e) => setRiskUsd(Number(e.target.value) || 20)}
              className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
            />
          </label>
          <div className="flex items-end text-[11px] text-terminal-muted">
            Default: $1,000 principal · 2% risk = $20/R. Ending equity = principal + net profit.
          </div>
        </div>

        <div className="mt-3 grid gap-3 term-md:grid-cols-3">
          <label className="text-xs">
            <span className="mb-1 block text-terminal-muted">
              Taker fee % (exit + market entry)
            </span>
            <input
              type="number"
              min={0}
              step={0.01}
              value={takerFeePct}
              onChange={(e) => setTakerFeePct(Number(e.target.value) || 0)}
              className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
            />
          </label>
          <label className="text-xs">
            <span className="mb-1 block text-terminal-muted">
              Maker fee % (LIMIT_RETEST entry)
            </span>
            <input
              type="number"
              min={0}
              step={0.01}
              value={makerFeePct}
              onChange={(e) => setMakerFeePct(Number(e.target.value) || 0)}
              className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
            />
          </label>
          <div className="flex items-end text-[11px] text-terminal-muted">
            Defaults: Binance USDT-M VIP0 (taker 0.04% / maker 0.02%). Qty sized so stop risk = Risk $.
          </div>
        </div>

        <div className="mt-3">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
            Period
          </div>
          <div className="mb-2 flex flex-wrap gap-1">
            <button
              type="button"
              onClick={() => setPeriodMode("lookback")}
              className={`rounded border px-2 py-1 text-xs ${
                periodMode === "lookback"
                  ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                  : "border-terminal-border text-terminal-muted"
              }`}
            >
              Recent lookback
            </button>
            <button
              type="button"
              onClick={() => setPeriodMode("dates")}
              className={`rounded border px-2 py-1 text-xs ${
                periodMode === "dates"
                  ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                  : "border-terminal-border text-terminal-muted"
              }`}
            >
              Custom dates (any year)
            </button>
          </div>

          {periodMode === "lookback" ? (
            <>
              <div className="mb-1 text-[11px] text-terminal-muted">
                Bars from DB tail (ignores calendar year)
              </div>
              <div className="flex flex-wrap gap-1">
                {LOOKBACKS.map((l) => (
                  <button
                    key={l.id}
                    type="button"
                    title={l.hint}
                    onClick={() => {
                      setLookbackId(l.id);
                      setCustomLimit("");
                    }}
                    className={`rounded border px-2 py-1 text-xs ${
                      !customLimit && lookbackId === l.id
                        ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                        : "border-terminal-border text-terminal-muted"
                    }`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
              <label className="mt-2 block text-xs">
                <span className="mb-1 block text-terminal-muted">Custom bars (optional)</span>
                <input
                  value={customLimit}
                  onChange={(e) => setCustomLimit(e.target.value.replace(/[^\d]/g, ""))}
                  placeholder={String(limit)}
                  className="w-36 rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
                />
              </label>
            </>
          ) : (
            <>
              <div className="mb-1 text-[11px] text-terminal-muted">
                UTC calendar window — pick a year preset or any start/end (needs OHLCV in DB)
              </div>
              <div className="mb-2 flex flex-wrap gap-1">
                {YEAR_PRESETS.map((y) => (
                  <button
                    key={y.id}
                    type="button"
                    onClick={() => {
                      setStart(y.start);
                      setEnd(y.end);
                    }}
                    className={`rounded border px-2 py-1 font-mono text-xs ${
                      start === y.start && end === y.end
                        ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                        : "border-terminal-border text-terminal-muted"
                    }`}
                  >
                    {y.label}
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap items-end gap-3">
                <label className="text-xs">
                  <span className="mb-1 block text-terminal-muted">Start day (UTC)</span>
                  <input
                    type="date"
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                    className="rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
                  />
                </label>
                <label className="text-xs">
                  <span className="mb-1 block text-terminal-muted">End day (UTC, inclusive)</span>
                  <input
                    type="date"
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                    className="rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
                  />
                </label>
              </div>
            </>
          )}

          <div className="mt-2 flex flex-wrap items-end gap-3">
            <button
              type="button"
              disabled={loading}
              onClick={() => void run()}
              className="rounded border border-terminal-accent bg-terminal-accent/15 px-4 py-1.5 text-sm text-terminal-accent disabled:opacity-50"
            >
              {loading && progressView
                ? `${progressView.pct}%`
                : loading
                  ? "Running…"
                  : "Run backtest"}
            </button>
          </div>
          <div className="mt-1 font-mono text-[11px] text-terminal-muted">
            {periodMode === "dates"
              ? `Date window ${effectiveStart || "…"} → ${effectiveEnd || "…"} · ${symbols.length}×${timeframes.length} cells`
              : `Using limit=${limit} bars · ${symbols.length}×${timeframes.length} cells`}
          </div>
          {coverage.length ? (
            <div className="mt-1 font-mono text-[11px] text-terminal-muted">
              DB OHLCV:{" "}
              {coverage
                .slice(0, 4)
                .map(
                  (c) =>
                    `${c.symbol.replace("USDT", "")}/${c.timeframe} ${
                      c.start ? c.start.slice(0, 10) : "?"
                    }→${c.end ? c.end.slice(0, 10) : "?"}`
                )
                .join(" · ")}
              {coverage.length > 4 ? " …" : ""}
            </div>
          ) : null}
          {coverageWarning ? (
            <div className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-100">
              <div className="mb-1 font-semibold">Selected dates are outside (or only partly inside) DB history</div>
              <ul className="list-disc pl-4">
                {coverageWarning.map((g) => (
                  <li key={g}>{g}</li>
                ))}
              </ul>
              <div className="mt-1 text-amber-100/80">
                Backfill first, e.g.{" "}
                <code className="font-mono">
                  python scripts/expand_ohlcv_history.py --until 2023-01-01 --symbols BTCUSDT,ETHUSDT,SOLUSDT --tfs 15m,1h
                </code>
              </div>
            </div>
          ) : null}
          {loading && progressView ? (
            <div className="mt-3 rounded border border-terminal-border/70 bg-black/25 px-3 py-2">
              <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2 font-mono text-[11px]">
                <span className="text-terminal-accent">
                  {progressView.pct}% · {progressView.done}/{progressView.total} cells
                </span>
                <span className="text-terminal-muted">
                  elapsed {progressView.elapsedLabel} · ETA {progressView.etaLabel}
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded bg-terminal-border/50">
                <div
                  className="h-full bg-terminal-accent transition-[width] duration-300 ease-out"
                  style={{ width: `${progressView.pct}%` }}
                />
              </div>
              <div className="mt-1.5 font-mono text-[11px] text-terminal-muted">
                Running {progressView.current}
                {progressView.done === 0
                  ? " — first cell can take 1–3 min (structure scan); % updates when it finishes"
                  : ""}
              </div>
            </div>
          ) : null}
        </div>
      </section>

      {error ? (
        <div className="mb-3 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      ) : null}

      {result ? (
        <>
          <div className="mb-3 grid gap-2 rounded border border-terminal-accent/30 bg-terminal-accent/5 px-3 py-3 term-md:grid-cols-4">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
                Principal
              </div>
              <div className="font-mono text-lg text-terminal-text">
                ${principalUsd.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
                {selected
                  ? `Net profit (${selected.symbol} ${selected.timeframe})`
                  : "Net profit (pooled)"}
              </div>
              <div
                className={`font-mono text-lg ${
                  (selected ? selectedCapital.net : pooled.pnlNet) != null &&
                  ((selected ? selectedCapital.net : pooled.pnlNet) as number) >= 0
                    ? "text-emerald-400"
                    : "text-red-400"
                }`}
              >
                {money(selected ? selectedCapital.net : pooled.pnlNet)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
                Ending equity (total)
              </div>
              <div className="font-mono text-lg text-terminal-accent">
                {selected
                  ? selectedCapital.ending == null
                    ? "—"
                    : `$${selectedCapital.ending.toFixed(2)}`
                  : `$${(principalUsd + pooled.pnlNet).toFixed(2)}`}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
                Return on principal
              </div>
              <div className="font-mono text-lg text-terminal-text">
                {selected
                  ? selectedCapital.retPct == null
                    ? "—"
                    : `${selectedCapital.retPct >= 0 ? "+" : ""}${selectedCapital.retPct.toFixed(2)}%`
                  : principalUsd
                    ? `${pooled.pnlNet >= 0 ? "+" : ""}${((pooled.pnlNet / principalUsd) * 100).toFixed(2)}%`
                    : "—"}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-terminal-muted">
                Fees {money(-(selected ? selectedCapital.fees : pooled.fees)).replace("+", "-")} · 1R
                = ${riskUsd}
              </div>
            </div>
          </div>
          <div className="mb-3 grid gap-1 rounded border border-terminal-border/80 bg-black/20 px-3 py-2 font-mono text-[11px] text-terminal-muted term-md:grid-cols-2">
            <div>
              Playbook:{" "}
              <span className="text-terminal-text">{result.playbook || "—"}</span>
            </div>
            <div>
              Elapsed:{" "}
              <span className="text-terminal-text">
                {result.elapsed_seconds != null ? `${result.elapsed_seconds}s` : "—"}
              </span>
            </div>
            <div>
              Pooled n={pooled.n} · avg R={num(pooled.avgR)} · gross PnL={money(pooled.pnl)}{" "}
              <span className="text-terminal-muted">(@ ${riskUsd}/R before fees)</span>
            </div>
            <div>
              Fees: taker {(takerFeePct).toFixed(2)}% / maker {(makerFeePct).toFixed(2)}% · click a
              row for trade blotter
            </div>
            <div className="text-terminal-muted term-md:col-span-2">{result.disclaimer}</div>
          </div>
        </>
      ) : null}

      <div className="min-w-0 overflow-x-auto rounded border border-terminal-border/80">
        <table className="w-full min-w-[880px] border-collapse text-left text-xs">
          <thead className="bg-black/30 text-[11px] uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-2 py-2">Symbol</th>
              <th className="px-2 py-2">TF</th>
              <th className="px-2 py-2">n</th>
              <th className="px-2 py-2">Avg R</th>
              <th className="px-2 py-2">TP1</th>
              <th className="px-2 py-2">SL</th>
              <th className="px-2 py-2">PF</th>
              <th className="px-2 py-2">Max DD R</th>
              <th className="px-2 py-2">Gross PnL</th>
              <th className="px-2 py-2">Net PnL</th>
              <th className="px-2 py-2">Fees</th>
              <th className="px-2 py-2">Equity R</th>
              <th className="px-2 py-2">Period</th>
            </tr>
          </thead>
          <tbody>
            {loading && !rows.length ? (
              <tr>
                <td colSpan={13} className="px-3 py-8 text-center text-terminal-muted">
                  {progressView
                    ? `${progressView.pct}% complete — ${progressView.done}/${progressView.total} · ETA ${progressView.etaLabel}`
                    : "Starting backtest…"}
                </td>
              </tr>
            ) : null}
            {!loading && !rows.length ? (
              <tr>
                <td colSpan={13} className="px-3 py-8 text-center text-terminal-muted">
                  {result
                    ? "NO TRADES — empty sample for selected filters"
                    : "Choose lookback and click Run backtest"}
                </td>
              </tr>
            ) : null}
            {rows.map((row) => {
                const key = `${row.symbol}:${row.timeframe}`;
                const active = selectedKey === key;
                const avg = row.average_R;
                const avgCls =
                  avg == null
                    ? "text-terminal-muted"
                    : avg >= 0
                      ? "text-emerald-400"
                      : "text-red-400";
                return (
                  <tr
                    key={key}
                    onClick={() => setSelectedKey(key)}
                    className={`cursor-pointer border-t border-terminal-border/50 ${
                      active ? "bg-terminal-accent/10" : "hover:bg-white/[0.03]"
                    }`}
                  >
                    <td className="px-2 py-2 font-mono text-terminal-text">
                      {row.symbol}
                    </td>
                    <td className="px-2 py-2 font-mono">{row.timeframe}</td>
                    <td className="px-2 py-2 font-mono">{row.sample_size}</td>
                    <td className={`px-2 py-2 font-mono ${avgCls}`}>
                      {row.sample_size ? num(avg) : "—"}
                    </td>
                    <td className="px-2 py-2 font-mono">{pct(row.tp1_hit_rate)}</td>
                    <td className="px-2 py-2 font-mono">{pct(row.sl_rate)}</td>
                    <td className="px-2 py-2 font-mono">{num(row.profit_factor)}</td>
                    <td className="px-2 py-2 font-mono">{num(row.max_drawdown_R)}</td>
                    <td
                      className={`px-2 py-2 font-mono ${
                        (row.pnl_usd ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                      }`}
                    >
                      {row.sample_size ? money(row.pnl_usd) : "—"}
                    </td>
                    <td
                      className={`px-2 py-2 font-mono ${
                        (row.pnl_usd_net ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                      }`}
                    >
                      {row.sample_size ? money(row.pnl_usd_net) : "—"}
                    </td>
                    <td className="px-2 py-2 font-mono text-terminal-muted">
                      {row.sample_size ? money(-(row.fees_usd ?? 0)).replace("+", "-") : "—"}
                    </td>
                    <td className="px-2 py-2">
                      <Sparkline values={row.equity_curve_r || []} />
                    </td>
                    <td className="px-2 py-2 font-mono text-terminal-muted">
                      {String(row.period_start || "—").slice(0, 10)} →{" "}
                      {String(row.period_end || "—").slice(0, 10)}
                      <div className="text-[10px]">bars={row.bars_loaded ?? "—"}</div>
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {selected ? (
        <SelectedDetail row={selected} principalUsd={principalUsd} />
      ) : null}
    </div>
  );
}

function SelectedDetail({
  row,
  principalUsd,
}: {
  row: StrategyMatrixRow;
  principalUsd: number;
}) {
  const trades = row.trades || [];
  let running = principalUsd;
  const withEquity = trades.map((t) => {
    running += Number(t.net_pnl_usd || 0);
    return { t, equity: running };
  });
  const ending = principalUsd + Number(row.pnl_usd_net || 0);
  return (
    <section className="mt-4 rounded border border-terminal-border/80 p-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-terminal-text">
          Trade blotter — {row.symbol} · {row.timeframe} · {row.direction}
        </h2>
        <span className="font-mono text-[11px] text-terminal-muted">
          Principal ${principalUsd.toFixed(2)} → total ${ending.toFixed(2)} · net{" "}
          {money(row.pnl_usd_net)} · fees{" "}
          {money(-(row.fees_usd ?? 0)).replace("+", "-")}
        </span>
      </div>

      <div className="mb-3 grid gap-3 term-md:grid-cols-2">
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
            Equity curve (R, gross engine)
          </div>
          <div className="rounded border border-terminal-border/60 bg-black/20 p-2">
            <SparklineLarge values={row.equity_curve_r || []} />
          </div>
        </div>
        <div className="font-mono text-[11px] text-terminal-muted">
          <div>
            Avg R gross {num(row.average_R)} · Avg R net {num(row.average_R_net)}
          </div>
          <div className="mt-1">
            System entry = engine entry_price (LIMIT_RETEST broken level or market close). Exit =
            first touch of TP1/TP2/TP3/SL on later bars. Times are candle open times (UTC).
          </div>
          <div className="mt-2 text-terminal-text">
            Principal ${principalUsd.toFixed(2)} + net profit {money(row.pnl_usd_net)} ={" "}
            <span className="text-terminal-accent">total ${ending.toFixed(2)}</span>
          </div>
        </div>
      </div>

      <div className="max-h-[420px] min-w-0 overflow-auto rounded border border-terminal-border/60">
        <table className="w-full min-w-[1180px] border-collapse text-left text-[11px]">
          <thead className="sticky top-0 bg-black/80 text-[10px] uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-2 py-2">#</th>
              <th className="px-2 py-2">Entry time</th>
              <th className="px-2 py-2">Exit time</th>
              <th className="px-2 py-2">Side</th>
              <th className="px-2 py-2">Entry</th>
              <th className="px-2 py-2">Exit</th>
              <th className="px-2 py-2">Stop</th>
              <th className="px-2 py-2">TP1</th>
              <th className="px-2 py-2">Outcome</th>
              <th className="px-2 py-2">Hold</th>
              <th className="px-2 py-2">Qty</th>
              <th className="px-2 py-2">Type</th>
              <th className="px-2 py-2">Fees</th>
              <th className="px-2 py-2">Gross</th>
              <th className="px-2 py-2">Net</th>
              <th className="px-2 py-2">R net</th>
              <th className="px-2 py-2">Equity $</th>
            </tr>
          </thead>
          <tbody>
            {!trades.length ? (
              <tr>
                <td colSpan={17} className="px-3 py-6 text-center text-terminal-muted">
                  No closed trades for this cell
                </td>
              </tr>
            ) : null}
            {withEquity.map(({ t, equity }) => (
              <TradeRow
                key={`${t.signal_time}-${t.entry_price}-${t.trade_no}`}
                t={t}
                equityUsd={equity}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TradeRow({ t, equityUsd }: { t: StrategyTradeRow; equityUsd: number }) {
  const net = t.net_pnl_usd;
  const netCls =
    net == null ? "text-terminal-muted" : net >= 0 ? "text-emerald-400" : "text-red-400";
  return (
    <tr className="border-t border-terminal-border/40 hover:bg-white/[0.03]">
      <td className="px-2 py-1.5 font-mono text-terminal-muted">{t.trade_no ?? "—"}</td>
      <td className="px-2 py-1.5 font-mono whitespace-nowrap">{fmtTime(t.signal_time)}</td>
      <td className="px-2 py-1.5 font-mono whitespace-nowrap">{fmtTime(t.exit_time)}</td>
      <td className="px-2 py-1.5 font-mono">{t.direction}</td>
      <td className="px-2 py-1.5 font-mono">{px(t.entry_price)}</td>
      <td className="px-2 py-1.5 font-mono">{px(t.exit_price)}</td>
      <td className="px-2 py-1.5 font-mono text-terminal-muted">{px(t.stop_price)}</td>
      <td className="px-2 py-1.5 font-mono text-terminal-muted">{px(t.tp1)}</td>
      <td className="px-2 py-1.5 font-mono">{t.outcome || "—"}</td>
      <td className="px-2 py-1.5 font-mono">{t.holding_bars ?? "—"}</td>
      <td className="px-2 py-1.5 font-mono">{num(t.qty, 4)}</td>
      <td className="px-2 py-1.5 font-mono text-terminal-muted">{t.entry_type || "—"}</td>
      <td className="px-2 py-1.5 font-mono text-terminal-muted">
        {money(-(t.fee_total_usd ?? 0)).replace("+", "-")}
      </td>
      <td
        className={`px-2 py-1.5 font-mono ${
          (t.gross_pnl_usd ?? 0) >= 0 ? "text-emerald-400/80" : "text-red-400/80"
        }`}
      >
        {money(t.gross_pnl_usd)}
      </td>
      <td className={`px-2 py-1.5 font-mono ${netCls}`}>{money(net)}</td>
      <td className={`px-2 py-1.5 font-mono ${netCls}`}>{num(t.r_net, 3)}</td>
      <td className="px-2 py-1.5 font-mono text-terminal-accent">${equityUsd.toFixed(2)}</td>
    </tr>
  );
}

function SparklineLarge({ values }: { values: number[] }) {
  if (!values.length) {
    return <div className="py-6 text-center text-xs text-terminal-muted">No equity data</div>;
  }
  const w = 420;
  const h = 100;
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const zeroY = h - ((0 - min) / span) * (h - 8) - 4;
  const pts = values
    .map((v, i) => {
      const x = (i / Math.max(1, values.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 8) - 4;
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-24 w-full max-w-lg text-terminal-accent">
      <line
        x1={0}
        x2={w}
        y1={zeroY}
        y2={zeroY}
        stroke="currentColor"
        strokeOpacity={0.25}
        strokeDasharray="4 3"
      />
      <polyline fill="none" stroke="currentColor" strokeWidth="1.5" points={pts} />
    </svg>
  );
}
