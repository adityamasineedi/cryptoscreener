import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchResearchOhlcvRange,
  type OhlcvRangeRow,
  type StrategyMatrixResponse,
  type StrategyMatrixRow,
  type StrategyTradeRow,
} from "../api/client";
import {
  allFrozenV1Symbols,
  assessProductionComparable,
  barsToApproxDays,
  FEE_DISPLAY,
  formatBarsDuration,
  lookbackLabelForTf,
  symbolRoleDisplay,
  timeframeRoleLabel,
  V1_PRODUCTION_RISK,
  v1RiskForSymbol,
} from "../backtest/v1Config";
import { tradeRowKey } from "../chart/backtestTradeOverlay";
import {
  mergeSymbols,
  parseSymbolList,
  RESEARCH_SYMBOL_PRESETS,
} from "../research/symbols";
import { useBacktestJobStore } from "../store/backtestJobStore";
import { BacktestTradeChart } from "./BacktestTradeChart";
import { CandidateResearchPanel } from "./CandidateResearchPanel";
import { DynamicCandidatePipelinePanel } from "./DynamicCandidatePipelinePanel";
import { ShortResearchPanel } from "./ShortResearchPanel";

type PeriodMode = "lookback" | "dates";

const YEAR_PRESETS = [
  { id: "2023", start: "2023-01-01", end: "2023-12-31", label: "2023" },
  { id: "2024", start: "2024-01-01", end: "2024-12-31", label: "2024" },
  { id: "2025", start: "2025-01-01", end: "2025-12-31", label: "2025" },
  { id: "2026ytd", start: "2026-01-01", end: "", label: "2026 YTD" },
] as const;

const SYMBOL_OPTIONS = RESEARCH_SYMBOL_PRESETS;
const TF_OPTIONS = ["15m", "1h", "4h"] as const;

/** COMBO_02 v1 production book labels (see docs/v1_production.md). */
const V1_BOOK_TIER: Record<string, "core" | "secondary" | "research"> = {
  "BTCUSDT|1h": "core",
  "ETHUSDT|1h": "secondary",
  "SOLUSDT|1h": "secondary",
  "BTCUSDT|4h": "secondary",
  "ETHUSDT|4h": "secondary",
  "BTCUSDT|15m": "research",
  "ETHUSDT|15m": "research",
  "SOLUSDT|15m": "research",
  "SOLUSDT|4h": "research",
};

function v1TierBadge(tier: "core" | "secondary" | "research"): {
  label: string;
  className: string;
} {
  if (tier === "core") {
    return {
      label: "v1 CORE",
      className: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
    };
  }
  if (tier === "secondary") {
    return {
      label: "v1 SECONDARY",
      className: "border-amber-500/40 bg-amber-500/10 text-amber-200",
    };
  }
  return {
    label: "research-only",
    className: "border-terminal-border bg-white/5 text-terminal-muted",
  };
}

const LOOKBACK_LIMITS = [
  { id: "12d", limit: 1200 },
  { id: "30d", limit: 2880 },
  { id: "60d", limit: 5760 },
  { id: "90d", limit: 8640 },
  { id: "max", limit: 12200 },
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
  const [customSymbols, setCustomSymbols] = useState("");
  const [timeframes, setTimeframes] = useState<string[]>(["1h"]);
  const [direction, setDirection] = useState<"LONG" | "SHORT">("LONG");
  const [lookbackId, setLookbackId] = useState<(typeof LOOKBACK_LIMITS)[number]["id"]>("60d");
  const [customLimit, setCustomLimit] = useState("");
  const [periodMode, setPeriodMode] = useState<PeriodMode>("lookback");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [principalUsd, setPrincipalUsd] = useState(1000);
  const [riskPct, setRiskPct] = useState(1.5);
  const [riskUsd, setRiskUsd] = useState(15);
  const [researchRiskOverride, setResearchRiskOverride] = useState(false);
  const [shortNotice, setShortNotice] = useState<string | null>(null);
  const [leverage, setLeverage] = useState(2);
  const [takerFeePct, setTakerFeePct] = useState(0.04);
  const [makerFeePct, setMakerFeePct] = useState(0.02);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<OhlcvRangeRow[]>([]);

  const job = useBacktestJobStore((s) => s.job);
  const storeError = useBacktestJobStore((s) => s.error);
  const active = useBacktestJobStore((s) => s.active);
  const startJob = useBacktestJobStore((s) => s.start);
  const cancelJob = useBacktestJobStore((s) => s.cancel);
  const setStoreError = useBacktestJobStore((s) => s.setError);

  const loading = active || job?.status === "running";
  const frozenV1Selection = allFrozenV1Symbols(symbols);
  const lockV1Risk = frozenV1Selection && !researchRiskOverride;
  const primaryTf = timeframes.includes("1h")
    ? "1h"
    : timeframes[0] || "1h";

  const limit = useMemo(() => {
    // Date mode loads by calendar bounds from Postgres (full series slice).
    // Keep a high safety cap for warmup/unbounded paths.
    if (periodMode === "dates" && (start || end)) return 20000;
    const custom = Number(customLimit);
    if (customLimit && Number.isFinite(custom) && custom >= 50) {
      return Math.min(Math.floor(custom), 20000);
    }
    return LOOKBACK_LIMITS.find((l) => l.id === lookbackId)?.limit ?? 5760;
  }, [customLimit, lookbackId, periodMode, start, end]);

  const approxDays = useMemo(
    () => barsToApproxDays(limit, primaryTf),
    [limit, primaryTf]
  );

  const comparability = useMemo(
    () =>
      assessProductionComparable({
        symbols,
        timeframes,
        direction,
        researchRiskOverride,
        leverage,
        takerFeePct,
        makerFeePct,
      }),
    [
      symbols,
      timeframes,
      direction,
      researchRiskOverride,
      leverage,
      takerFeePct,
      makerFeePct,
    ]
  );

  // Auto-apply frozen v1 production risk when editing is locked.
  useEffect(() => {
    if (!lockV1Risk) return;
    if (symbols.length === 1) {
      const r = v1RiskForSymbol(symbols[0], principalUsd);
      if (r) {
        setRiskPct(r.riskPercent);
        setRiskUsd(r.riskUsd);
      }
      return;
    }
    // Multi-symbol: show BTC core as the summary default; API sizes each cell.
    const btc = v1RiskForSymbol("BTCUSDT", principalUsd);
    if (btc && symbols.includes("BTCUSDT")) {
      setRiskPct(btc.riskPercent);
      setRiskUsd(btc.riskUsd);
    } else {
      const first = v1RiskForSymbol(symbols[0], principalUsd);
      if (first) {
        setRiskPct(first.riskPercent);
        setRiskUsd(first.riskUsd);
      }
    }
  }, [lockV1Risk, symbols, principalUsd]);

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

  const result: StrategyMatrixResponse | null = useMemo(() => {
    if (!job || job.status === "idle") return null;
    if (!job.rows?.length && job.status === "running") {
      return {
        status: "OK",
        playbook: job.playbook || undefined,
        combination_id: job.combination_id,
        combination_name: job.combination_name || undefined,
        direction: job.direction,
        limit: job.limit,
        risk_usd: job.risk_usd,
        symbols: job.symbols,
        timeframes: job.timeframes,
        rows: [],
        elapsed_seconds: job.elapsed_seconds ?? undefined,
        disclaimer: job.disclaimer || undefined,
      };
    }
    if (!job.rows?.length && job.status !== "done" && job.status !== "cancelled") {
      return null;
    }
    if (!job.rows?.length && (job.status === "done" || job.status === "cancelled")) {
      return {
        status: job.status === "cancelled" ? "CANCELLED" : "OK",
        playbook: job.playbook || undefined,
        combination_id: job.combination_id,
        combination_name: job.combination_name || undefined,
        direction: job.direction,
        limit: job.limit,
        risk_usd: job.risk_usd,
        symbols: job.symbols,
        timeframes: job.timeframes,
        rows: [],
        elapsed_seconds: job.elapsed_seconds ?? undefined,
        disclaimer: job.disclaimer || undefined,
      };
    }
    return {
      status: job.status === "error" ? "ERROR" : "OK",
      playbook: job.playbook || undefined,
      combination_id: job.combination_id,
      combination_name: job.combination_name || undefined,
      direction: job.direction,
      limit: job.limit,
      risk_usd: job.risk_usd,
      symbols: job.symbols,
      timeframes: job.timeframes,
      rows: job.rows || [],
      elapsed_seconds: job.elapsed_seconds ?? undefined,
      disclaimer: job.disclaimer || undefined,
      reason: job.error || undefined,
    };
  }, [job]);

  const error = storeError || (job?.status === "error" ? job.error || null : null);

  // Auto-select first non-empty row when results arrive.
  useEffect(() => {
    const rows = result?.rows ?? [];
    if (!rows.length) return;
    if (selectedKey && rows.some((r) => `${r.symbol}:${r.timeframe}` === selectedKey)) {
      return;
    }
    const firstWithTrades = rows.find(
      (r) => (r.trades?.length || 0) > 0 || r.sample_size > 0
    );
    const pick = firstWithTrades || rows[0];
    if (pick) setSelectedKey(`${pick.symbol}:${pick.timeframe}`);
  }, [result?.rows, selectedKey]);

  const progressView = useMemo(() => {
    // Show as soon as a run is active (optimistic store job) — not only after
    // the first successful poll, so % is never blank while "Running…".
    if (!loading || !job?.total_cells) return null;
    if (job.status !== "running" && !active) return null;
    const done = job.done_cells ?? 0;
    const total = job.total_cells;
    const rawPct =
      job.progress_percent != null && Number.isFinite(job.progress_percent)
        ? Number(job.progress_percent)
        : job.pct != null && Number.isFinite(job.pct)
          ? Number(job.pct)
          : (done / total) * 100;
    const pctNum = Math.min(100, rawPct);
    const pct =
      pctNum > 0 && pctNum < 10
        ? Number(pctNum.toFixed(1))
        : Math.round(pctNum);
    const startedMs = job.started_at ? Date.parse(job.started_at) : NaN;
    const elapsedMs = Number.isFinite(startedMs)
      ? Math.max(0, nowMs - startedMs)
      : (job.elapsed_seconds ?? 0) * 1000;
    const remaining = Math.max(0, total - done);
    let etaMs: number | null = null;
    const bars = job.bars_processed ?? 0;
    const totalBars = job.total_bars ?? 0;
    if (done > 0 && elapsedMs > 0) {
      etaMs = (elapsedMs / done) * remaining;
    } else if (bars > 0 && totalBars > bars && elapsedMs > 0) {
      etaMs = (elapsedMs / bars) * (totalBars - bars);
    }
    return {
      pct,
      done,
      total,
      current: job.current || "",
      phase: job.phase || "",
      barsProcessed: bars,
      totalBars,
      trades: job.trades_generated ?? job.trades ?? 0,
      lastHeartbeat: job.last_heartbeat || null,
      rowsLoaded: job.rows_loaded ?? 0,
      elapsedLabel: formatDuration(elapsedMs),
      etaLabel: etaMs == null ? "…" : formatDuration(etaMs),
    };
  }, [job, nowMs, loading, active]);

  const run = useCallback(async () => {
    if (!symbols.length || !timeframes.length) {
      setStoreError("Pick at least one symbol and timeframe");
      return;
    }
    if (direction === "SHORT") {
      setShortNotice(
        "SHORT research is paused. SHORT paper trading, production, and Telegram are disabled. No SHORT backtest will enter an operational path."
      );
      setStoreError("short_research_paused");
      return;
    }
    if (periodMode === "dates" && !start && !end) {
      setStoreError("Custom dates mode needs a start and/or end day (UTC)");
      return;
    }
    setSelectedKey(null);
    setShortNotice(null);
    await startJob({
      symbols,
      timeframes,
      direction: "LONG",
      combination_id: "COMBO_02",
      strategy_id: "COMBO_02_V1",
      combo_version: "v1",
      setup_timeframe: primaryTf,
      risk_mode: researchRiskOverride
        ? "RESEARCH_OVERRIDE"
        : "V1_PRODUCTION_PROFILE",
      research_risk_override: researchRiskOverride,
      limit,
      risk_usd: riskUsd,
      principal_usd: principalUsd,
      leverage,
      taker_fee_pct: takerFeePct,
      maker_fee_pct: makerFeePct,
      include_trades: true,
      start_date: effectiveStart,
      end_date: effectiveEnd,
    });
  }, [
    symbols,
    timeframes,
    direction,
    primaryTf,
    researchRiskOverride,
    limit,
    riskUsd,
    principalUsd,
    leverage,
    takerFeePct,
    makerFeePct,
    periodMode,
    start,
    end,
    effectiveStart,
    effectiveEnd,
    startJob,
    setStoreError,
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
          COMBO_02 v1 research backtest on Postgres OHLCV. LONG-only production
          profile. Same engine as research scripts — not a profitability claim.
          No paper or live trade is created from this screen.
        </p>
      </div>

      <section className="mb-4 rounded border border-terminal-border/80 p-3">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded border border-terminal-accent/40 bg-terminal-accent/10 px-2 py-1 font-mono text-terminal-accent">
            Strategy: COMBO_02 v1
          </span>
          <span className="rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-300">
            Direction: LONG
          </span>
          <span className="rounded border border-terminal-border bg-white/5 px-2 py-0.5 text-[10px] text-terminal-text">
            Setup TF: 1h
          </span>
          <span className="rounded border border-terminal-border bg-white/5 px-2 py-0.5 text-[10px] text-terminal-text">
            HTF: 1h + 4h bullish alignment
          </span>
          <span className="rounded border border-terminal-border bg-white/5 px-2 py-0.5 text-[10px] text-terminal-muted">
            Source: V1_RESEARCH_BACKTEST
          </span>
          <span className="rounded border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-[10px] text-rose-200">
            SHORT status: PAUSED
          </span>
        </div>

        {comparability.productionComparable ? (
          <div className="mb-3 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-[11px] text-emerald-100">
            <div className="font-semibold">FROZEN V1 CONFIGURATION</div>
            <div>Production-comparable research run</div>
          </div>
        ) : (
          <div className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-100">
            <div className="font-semibold">RESEARCH-ONLY CONFIGURATION</div>
            <div>
              This configuration differs from the frozen COMBO_02 v1 profile.
              Results must not be compared directly with production v1 results.
            </div>
            {comparability.reasons.length ? (
              <div className="mt-1 font-mono text-[10px] text-amber-100/80">
                Reasons: {comparability.reasons.join(", ")}
              </div>
            ) : null}
          </div>
        )}

        <div className="grid gap-3 term-md:grid-cols-2 term-lg:grid-cols-4">
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Symbols
            </div>
            <div className="flex flex-wrap gap-1">
              {SYMBOL_OPTIONS.map((s) => {
                const on = symbols.includes(s);
                const symTier =
                  s === "BTCUSDT"
                    ? "core"
                    : s === "ETHUSDT" || s === "SOLUSDT"
                      ? "secondary"
                      : ("research" as const);
                const badge = v1TierBadge(symTier);
                return (
                  <button
                    key={s}
                    type="button"
                    title={symbolRoleDisplay(s, "1h")}
                    onClick={() => setSymbols((prev) => toggleInList(prev, s))}
                    className={`rounded border px-2 py-1 font-mono text-xs ${
                      on
                        ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                        : "border-terminal-border text-terminal-muted"
                    }`}
                  >
                    {s.replace("USDT", "")}
                    {(s === "BTCUSDT" || s === "ETHUSDT" || s === "SOLUSDT") && (
                      <span className={`ml-1 text-[9px] ${badge.className} rounded px-1`}>
                        {badge.label}
                      </span>
                    )}
                  </button>
                );
              })}
              {symbols
                .filter((s) => !(SYMBOL_OPTIONS as readonly string[]).includes(s))
                .map((s) => (
                  <button
                    key={s}
                    type="button"
                    title={`Remove ${s}`}
                    onClick={() => setSymbols((prev) => prev.filter((x) => x !== s))}
                    className="rounded border border-terminal-accent bg-terminal-accent/15 px-2 py-1 font-mono text-xs text-terminal-accent"
                  >
                    {s.replace("USDT", "")} ×
                  </button>
                ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              <input
                value={customSymbols}
                onChange={(e) => setCustomSymbols(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  e.preventDefault();
                  const added = parseSymbolList(customSymbols);
                  if (!added.length) return;
                  setSymbols((prev) => mergeSymbols(prev, added));
                  setCustomSymbols("");
                }}
                placeholder="Add: XRPUSDT, INJUSDT…"
                className="min-w-[12rem] flex-1 rounded border border-terminal-border bg-transparent px-2 py-1 font-mono text-xs focus:border-terminal-accent focus:outline-none"
              />
              <button
                type="button"
                onClick={() => {
                  const added = parseSymbolList(customSymbols);
                  if (!added.length) return;
                  setSymbols((prev) => mergeSymbols(prev, added));
                  setCustomSymbols("");
                }}
                className="rounded border border-terminal-border px-2 py-1 text-xs text-terminal-muted hover:text-terminal-text"
              >
                Add
              </button>
            </div>
            <div className="mt-1 text-[10px] text-terminal-muted">
              Fetch OHLCV first for new coins (History tab), then run backtest.
            </div>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Timeframes
            </div>
            <div className="flex flex-wrap gap-1">
              {TF_OPTIONS.map((tf) => {
                const on = timeframes.includes(tf);
                const role = timeframeRoleLabel(tf);
                return (
                  <button
                    key={tf}
                    type="button"
                    title={`${tf} — ${role}`}
                    onClick={() => setTimeframes((prev) => toggleInList(prev, tf))}
                    className={`rounded border px-2 py-1 font-mono text-xs ${
                      on
                        ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                        : "border-terminal-border text-terminal-muted"
                    }`}
                  >
                    {tf}
                    <span className="ml-1 rounded border border-terminal-border bg-white/5 px-1 text-[9px] text-terminal-muted">
                      {role}
                    </span>
                  </button>
                );
              })}
            </div>
            {timeframes.includes("4h") ? (
              <div className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-100">
                4h setup is not the frozen COMBO_02 v1 1h production configuration.
                Results are research-only and not production-comparable.
              </div>
            ) : null}
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Direction
            </div>
            <div className="flex flex-wrap gap-1">
              <button
                type="button"
                onClick={() => {
                  setDirection("LONG");
                  setShortNotice(null);
                }}
                className={`rounded border px-2 py-1 font-mono text-xs ${
                  direction === "LONG"
                    ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                    : "border-terminal-border text-terminal-muted"
                }`}
              >
                LONG
              </button>
              <button
                type="button"
                aria-disabled="true"
                title="SHORT research is paused"
                onClick={() =>
                  setShortNotice(
                    "SHORT research is paused. SHORT paper trading, production, and Telegram are disabled. No SHORT backtest will enter an operational path."
                  )
                }
                className="rounded border border-rose-500/30 bg-rose-500/5 px-2 py-1 text-left font-mono text-xs text-rose-200/80 opacity-80"
              >
                <div>SHORT</div>
                <div className="text-[9px] leading-tight text-rose-200/70">Paused</div>
                <div className="text-[9px] leading-tight text-rose-200/60">
                  Research disabled
                </div>
              </button>
            </div>
            <div className="mt-1 font-mono text-[10px] text-terminal-muted">
              Direction: LONG · SHORT status: PAUSED
            </div>
            {shortNotice ? (
              <div className="mt-2 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1.5 text-[10px] text-rose-100">
                {shortNotice}
              </div>
            ) : null}
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

        <div className="mt-3 rounded border border-terminal-border/60 bg-black/20 px-3 py-2 text-[11px] text-terminal-muted">
          <div className="mb-1 font-semibold uppercase tracking-wide text-terminal-text">
            Effective production risk
          </div>
          <div className="font-mono text-[11px] text-terminal-text">
            BTCUSDT core — 1.5% / ${Math.round((principalUsd * 1.5) / 100)}
          </div>
          <div className="font-mono text-[11px] text-terminal-text">
            ETHUSDT secondary — 0.5% / ${Math.round((principalUsd * 0.5) / 100)}
          </div>
          <div className="font-mono text-[11px] text-terminal-text">
            SOLUSDT secondary — 0.5% / ${Math.round((principalUsd * 0.5) / 100)}
          </div>
        </div>

        <div className="mt-3 grid gap-3 term-md:grid-cols-4">
          <label className="text-xs">
            <span className="mb-1 block text-terminal-muted">Risk % of principal (1R)</span>
            <input
              type="number"
              min={0.1}
              step={0.1}
              value={riskPct}
              disabled={lockV1Risk}
              onChange={(e) =>
                applyRiskFromPrincipal(principalUsd, Number(e.target.value) || 0)
              }
              className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none disabled:opacity-50"
            />
          </label>
          <label className="text-xs">
            <span className="mb-1 block text-terminal-muted">Risk $ per trade (1R)</span>
            <input
              type="number"
              min={1}
              step={1}
              value={riskUsd}
              disabled={lockV1Risk}
              onChange={(e) => setRiskUsd(Number(e.target.value) || 20)}
              className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none disabled:opacity-50"
            />
          </label>
          <label className="text-xs">
            <span className="mb-1 block text-terminal-muted">Leverage (x)</span>
            <input
              type="number"
              min={1}
              max={125}
              step={1}
              value={leverage}
              onChange={(e) => setLeverage(Math.max(1, Number(e.target.value) || 2))}
              className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
            />
          </label>
          <div className="flex flex-col justify-end gap-1 text-[11px] text-terminal-muted">
            {frozenV1Selection ? (
              <label className="flex items-start gap-2 text-terminal-text">
                <input
                  type="checkbox"
                  checked={researchRiskOverride}
                  onChange={(e) => setResearchRiskOverride(e.target.checked)}
                  className="mt-0.5"
                />
                <span>
                  Research risk override
                  <span className="block text-[10px] text-terminal-muted">
                    Enables manual risk; marks results research-only
                  </span>
                </span>
              </label>
            ) : (
              <span>Non-v1 symbols use the configured research risk.</span>
            )}
          </div>
        </div>
        {researchRiskOverride ? (
          <div className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-100">
            This risk differs from the frozen COMBO_02 v1 production profile.
            Results are research-only and are not production-comparable.
          </div>
        ) : null}

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
          <div className="rounded border border-terminal-border/50 bg-black/15 px-2 py-1.5 font-mono text-[10px] text-terminal-muted">
            <div>Entry type: {FEE_DISPLAY.entryType}</div>
            <div>Entry fee type: Market entry → taker fee</div>
            <div>Exit type: {FEE_DISPLAY.exitType}</div>
            <div>Exit fee type: Market exit → taker fee</div>
            <div>Fee basis: {FEE_DISPLAY.feeBasis}</div>
            <div>Fee convention: {FEE_DISPLAY.feeConvention}</div>
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
              DB-tail mode
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
              Calendar-range mode
            </button>
          </div>

          {periodMode === "lookback" ? (
            <>
              <div className="mb-1 rounded border border-terminal-border/50 bg-black/15 px-2 py-1.5 text-[11px] text-terminal-muted">
                <div className="font-semibold text-terminal-text">DB-tail mode</div>
                <div>Uses the latest N available candles</div>
                <div>Does not represent a calendar-year filter</div>
              </div>
              <div className="flex flex-wrap gap-1">
                {LOOKBACK_LIMITS.map((l) => {
                  const days = barsToApproxDays(l.limit, primaryTf);
                  const label =
                    l.id === "max"
                      ? `Max (${l.limit.toLocaleString()} bars)`
                      : lookbackLabelForTf(l.limit, primaryTf);
                  return (
                    <button
                      key={l.id}
                      type="button"
                      title={formatBarsDuration(l.limit, primaryTf)}
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
                      {label}
                      {days != null && l.id !== "max" ? (
                        <span className="ml-1 text-[9px] opacity-70">
                          ({l.limit.toLocaleString()})
                        </span>
                      ) : null}
                    </button>
                  );
                })}
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
              <div className="mt-1 font-mono text-[11px] text-terminal-text">
                Bars requested: {limit.toLocaleString()}
              </div>
              <div className="font-mono text-[11px] text-terminal-muted">
                Approximate duration:{" "}
                {approxDays != null
                  ? `${approxDays} days at ${primaryTf}`
                  : "—"}
              </div>
            </>
          ) : (
            <>
              <div className="mb-1 rounded border border-terminal-border/50 bg-black/15 px-2 py-1.5 text-[11px] text-terminal-muted">
                <div className="font-semibold text-terminal-text">Calendar-range mode</div>
                <div>Uses only candles inside the requested UTC range</div>
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

          <div className="mt-3 rounded border border-terminal-border/70 bg-black/25 px-3 py-2 font-mono text-[11px] text-terminal-muted">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-terminal-text">
              Pre-run configuration summary
            </div>
            <div>Selected symbol: {symbols.join(", ") || "—"}</div>
            <div>
              Symbol role:{" "}
              {symbols.map((s) => symbolRoleDisplay(s, primaryTf)).join("; ") || "—"}
            </div>
            <div>Strategy: COMBO_02 v1</div>
            <div>Direction: LONG</div>
            <div>Setup timeframe: {primaryTf} ({timeframeRoleLabel(primaryTf)})</div>
            <div>HTF requirement: 1h + 4h bullish alignment</div>
            <div>
              Effective risk:{" "}
              {lockV1Risk
                ? symbols
                    .filter((s) => s in V1_PRODUCTION_RISK)
                    .map((s) => {
                      const r = v1RiskForSymbol(s, principalUsd);
                      return r ? `${s} ${r.riskPercent}% / $${r.riskUsd}` : s;
                    })
                    .join(" · ") || `${riskPct}% / $${riskUsd}`
                : `${riskPct}% / $${riskUsd}`}
            </div>
            <div>
              Risk source:{" "}
              {researchRiskOverride
                ? "Research override"
                : frozenV1Selection
                  ? "V1 production profile"
                  : "Research fallback"}
            </div>
            <div>Entry model: MARKET or LIMIT_RETEST</div>
            <div>
              Fee model: taker {takerFeePct}% / maker {makerFeePct}% · basis executed
              notional
            </div>
            <div>Leverage: {leverage}×</div>
            <div>
              Bars/date range:{" "}
              {periodMode === "dates"
                ? `Calendar ${effectiveStart || "…"} → ${effectiveEnd || "…"}`
                : `DB-tail ${limit.toLocaleString()} bars ≈ ${approxDays ?? "—"} days at ${primaryTf}`}
            </div>
            <div>
              Production comparable:{" "}
              <span
                className={
                  comparability.productionComparable
                    ? "text-emerald-300"
                    : "text-amber-200"
                }
              >
                {comparability.productionComparable ? "YES" : "NO"}
              </span>
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-end gap-3">
            <button
              type="button"
              disabled={loading || direction === "SHORT"}
              onClick={() => void run()}
              className="rounded border border-terminal-accent bg-terminal-accent/15 px-4 py-1.5 text-sm text-terminal-accent disabled:opacity-50"
            >
              {loading && progressView
                ? `${progressView.pct}%`
                : loading
                  ? "Running…"
                  : "Run backtest"}
            </button>
            {loading ? (
              <button
                type="button"
                onClick={() => void cancelJob()}
                className="rounded border border-rose-500/40 px-3 py-1.5 text-sm text-rose-200 hover:bg-rose-500/10"
              >
                Cancel
              </button>
            ) : null}
          </div>
          <div className="mt-1 font-mono text-[11px] text-terminal-muted">
            {periodMode === "dates"
              ? `Calendar-range mode ${effectiveStart || "…"} → ${effectiveEnd || "…"} · ${symbols.length}×${timeframes.length} cells · runs in background`
              : `DB-tail mode · Bars requested: ${limit.toLocaleString()} · ≈ ${approxDays ?? "—"} days at ${primaryTf} · ${symbols.length}×${timeframes.length} cells`}
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
                <Link
                  to={`/ohlcv-history?symbols=${encodeURIComponent(
                    symbols.join(",")
                  )}&tfs=${encodeURIComponent(timeframes.join(","))}&until=${encodeURIComponent(
                    start || "2023-01-01"
                  )}`}
                  className="font-semibold text-terminal-accent underline underline-offset-2 hover:text-terminal-text"
                >
                  Open OHLCV History tab
                </Link>
                {" "}to fetch missing range into DB
                {start ? ` (until ${start})` : ""}.
              </div>
            </div>
          ) : null}
          {loading && progressView ? (
            <div className="mt-3 rounded border border-terminal-border/70 bg-black/25 px-3 py-2">
              <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2 font-mono text-[11px]">
                <span className="text-terminal-accent">
                  {progressView.pct}% · {progressView.done}/{progressView.total} cells
                  <span className="text-terminal-muted"> · background</span>
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
                {progressView.phase ? ` · ${progressView.phase}` : ""}
                {progressView.totalBars > 0
                  ? ` · bars ${progressView.barsProcessed}/${progressView.totalBars}`
                  : progressView.rowsLoaded > 0
                    ? ` · rows loaded ${progressView.rowsLoaded}`
                    : progressView.done === 0
                      ? " — first cell scans structure bar-by-bar (15m date windows are heaviest)"
                      : ""}
                {` · trades ${progressView.trades}`}
                {" — safe to leave this tab"}
              </div>
              {progressView.lastHeartbeat ? (
                <div className="mt-0.5 font-mono text-[10px] text-terminal-muted/80">
                  last heartbeat {progressView.lastHeartbeat}
                </div>
              ) : null}
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
              Strategy identity:{" "}
              <span className="text-terminal-text">
                {job?.strategy_id || "COMBO_02_V1"} · {job?.combo_version || "v1"}
              </span>
            </div>
            <div>
              Direction:{" "}
              <span className="text-terminal-text">{job?.direction || "LONG"}</span>
              {" · "}
              SHORT status:{" "}
              <span className="text-rose-200">{job?.short_status || "PAUSED"}</span>
            </div>
            <div>
              Symbol role:{" "}
              <span className="text-terminal-text">
                {selected
                  ? symbolRoleDisplay(selected.symbol, selected.timeframe)
                  : symbols.map((s) => symbolRoleDisplay(s, primaryTf)).join("; ")}
              </span>
            </div>
            <div>
              Effective risk:{" "}
              <span className="text-terminal-text">
                {selected?.effective_risk_percent != null
                  ? `${(selected.effective_risk_percent * 100).toFixed(2)}% / $${num(selected.effective_risk_amount ?? selected.risk_usd, 0)}`
                  : job?.effective_risk_percent != null
                    ? `${(job.effective_risk_percent * 100).toFixed(2)}% / $${num(job.effective_risk_amount, 0)}`
                    : `${riskPct}% / $${riskUsd}`}
              </span>
            </div>
            <div>
              Risk source:{" "}
              <span className="text-terminal-text">
                {selected?.risk_source || job?.risk_source || "V1_PRODUCTION_PROFILE"}
              </span>
            </div>
            <div>
              Setup timeframe:{" "}
              <span className="text-terminal-text">
                {selected?.timeframe || primaryTf} (
                {timeframeRoleLabel(selected?.timeframe || primaryTf)})
              </span>
            </div>
            <div>
              HTF requirement:{" "}
              <span className="text-terminal-text">1h + 4h bullish alignment</span>
            </div>
            <div>
              Requested range:{" "}
              <span className="text-terminal-text">
                {job?.period_mode === "CALENDAR_RANGE" || periodMode === "dates"
                  ? `Calendar ${job?.start_date || effectiveStart || "…"} → ${job?.end_date || effectiveEnd || "…"}`
                  : `DB-tail ${job?.limit ?? limit} bars`}
              </span>
            </div>
            <div>
              Actual range:{" "}
              <span className="text-terminal-text">
                {selected
                  ? `${String(selected.period_start || "—").slice(0, 10)} → ${String(selected.period_end || "—").slice(0, 10)}`
                  : "—"}
              </span>
            </div>
            <div>
              Bars used:{" "}
              <span className="text-terminal-text">
                {selected?.bars_loaded ?? "—"}
                {selected?.timeframe
                  ? ` · ≈ ${barsToApproxDays(Number(selected.bars_loaded || 0), selected.timeframe) ?? "—"} days`
                  : ""}
              </span>
            </div>
            <div>
              Dataset fingerprint:{" "}
              <span className="text-terminal-text">
                {selected?.dataset_fingerprint || job?.dataset_fingerprint || "—"}
              </span>
            </div>
            <div>
              Configuration fingerprint:{" "}
              <span className="text-terminal-text">
                {selected?.configuration_fingerprint ||
                  job?.configuration_fingerprint ||
                  "—"}
              </span>
            </div>
            <div>
              Production comparable:{" "}
              <span
                className={
                  (selected?.production_comparable ?? job?.production_comparable)
                    ? "text-emerald-300"
                    : "text-amber-200"
                }
              >
                {(selected?.production_comparable ?? job?.production_comparable)
                  ? "YES"
                  : "NO"}
              </span>
            </div>
            <div>
              Research-only status:{" "}
              <span className="text-terminal-text">
                {(selected?.research_only ?? job?.research_only ?? true)
                  ? "YES"
                  : "NO"}
              </span>
            </div>
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
              Pooled n={pooled.n} · avg R={num(pooled.avgR)} · gross PnL={money(pooled.pnl)}
            </div>
            <div>
              Fees: taker {takerFeePct.toFixed(2)}% / maker {makerFeePct.toFixed(2)}% ·
              leverage {(job?.leverage ?? leverage)}x · entry {FEE_DISPLAY.entryType}
            </div>
            <div className="text-amber-100/90 term-md:col-span-2">
              Historical research only. Not a profitability claim. No paper or live
              trade created.
            </div>
            <div className="term-md:col-span-2 text-terminal-text">
              paper_trade_created ={" "}
              {String(job?.paper_trade_created ?? false)} · live_trade_created ={" "}
              {String(job?.live_trade_created ?? false)} · telegram_sent ={" "}
              {String(job?.telegram_sent ?? false)}
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
                      {(() => {
                        const tier =
                          V1_BOOK_TIER[`${row.symbol}|${row.timeframe}`];
                        if (!tier) return null;
                        const b = v1TierBadge(tier);
                        return (
                          <span
                            className={`ml-1 rounded border px-1 text-[9px] ${b.className}`}
                          >
                            {b.label}
                          </span>
                        );
                      })()}
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
                      <div className="text-[10px]">
                        Actual: {String(row.period_start || "—").slice(0, 10)} →{" "}
                        {String(row.period_end || "—").slice(0, 10)}
                      </div>
                      <div className="text-[10px]">
                        Bars loaded/used: {row.bars_loaded ?? "—"}
                        {row.timeframe
                          ? ` · ≈ ${barsToApproxDays(Number(row.bars_loaded || 0), row.timeframe) ?? "—"}d`
                          : ""}
                      </div>
                      <div className="text-[10px]">
                        {row.risk_source || "—"} ·{" "}
                        {row.production_comparable ? "prod-comparable" : "research-only"}
                      </div>
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {selected ? (
        <SelectedDetail
          key={`${selected.symbol}:${selected.timeframe}:${selected.direction}`}
          row={selected}
          principalUsd={principalUsd}
        />
      ) : null}

      <CandidateResearchPanel />
      <ShortResearchPanel />
      <DynamicCandidatePipelinePanel />
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
  const [selectedTradeKey, setSelectedTradeKey] = useState<string | null>(
    () => (trades[0] ? tradeRowKey(trades[0]) : null),
  );
  const selectedTrade =
    trades.find((t) => tradeRowKey(t) === selectedTradeKey) || null;

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
          <div className="mt-2 text-terminal-accent/90">
            Click a blotter row to overlay entry / SL / TP1 / exit on the price chart below.
          </div>
        </div>
      </div>

      {selectedTrade ? (
        <div className="mb-3">
          <BacktestTradeChart trade={selectedTrade} />
        </div>
      ) : null}

      <div className="max-h-[420px] min-w-0 overflow-auto rounded border border-terminal-border/60">
        <table className="w-full min-w-[1260px] border-collapse text-left text-[11px]">
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
              <th className="px-2 py-2">Margin</th>
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
            {withEquity.map(({ t, equity }) => {
              const key = tradeRowKey(t);
              return (
                <TradeRow
                  key={`${t.signal_time}-${t.entry_price}-${t.trade_no}`}
                  t={t}
                  equityUsd={equity}
                  selected={key === selectedTradeKey}
                  onSelect={() => setSelectedTradeKey(key)}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TradeRow({
  t,
  equityUsd,
  selected,
  onSelect,
}: {
  t: StrategyTradeRow;
  equityUsd: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const net = t.net_pnl_usd;
  const netCls =
    net == null ? "text-terminal-muted" : net >= 0 ? "text-emerald-400" : "text-red-400";
  return (
    <tr
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`border-t border-terminal-border/40 cursor-pointer ${
        selected
          ? "bg-terminal-accent/10 ring-1 ring-inset ring-terminal-accent/40"
          : "hover:bg-white/[0.03]"
      }`}
    >
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
      <td
        className="px-2 py-1.5 font-mono text-terminal-muted"
        title={
          t.leverage != null
            ? `${t.leverage}x · notional $${(t.notional_entry_usd ?? 0).toFixed(2)} · approx liq ${t.liquidation_price ?? "—"}`
            : undefined
        }
      >
        {t.margin_usd != null ? `$${t.margin_usd.toFixed(2)}` : "—"}
      </td>
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
