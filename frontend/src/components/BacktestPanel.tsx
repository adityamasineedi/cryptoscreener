import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  fetchResearchOhlcvRange,
  fetchStrategyRank,
  startRegimeBacktest,
  type OhlcvRangeRow,
  type StrategyRankResponse,
  type StrategyMatrixResponse,
  type StrategyMatrixRow,
  type StrategyTradeRow,
} from "../api/client";
import { pickAutoSetupTimeframe } from "../backtest/autoTimeframe";
import {
  API_BAR_CAP,
  coverageRowLabel,
  FALLBACK_MAX_BARS,
  maxLookbackBars,
} from "../backtest/availability";
import {
  checkJobInternallyConsistent,
  checkRunMatchesRequest,
  directionLabel,
  htfRequirementLabel,
} from "../backtest/runGuard";
import {
  allFrozenV1Symbols,
  assessProductionComparable,
  BACKTEST_CAP_SYMBOLS,
  BACKTEST_LARGE_SYMBOLS,
  BACKTEST_MID_SYMBOLS,
  BACKTEST_STRATEGY_OPTIONS,
  backtestStrategyLabel,
  bookChipLabel,
  HEDGE_STRATEGY_IDS,
  LONG_RUN_STRATEGY_ID,
  PASSED_RUN_STRATEGY_ID,
  passedIdsForBook,
  strategyIdsForBook,
  barsToApproxDays,
  filterBacktestCapSymbols,
  type BacktestStrategyId,
  type TradeBook,
  SD_DEFAULT_VARIANT,
  SD_VARIANT_OPTIONS,
  FEE_DISPLAY,
  formatBarsDuration,
  lastNDaysUtc,
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
} from "../research/symbols";
import { useBacktestHandoffStore } from "../store/backtestHandoffStore";
import { useBacktestJobStore } from "../store/backtestJobStore";
import { BacktestDataMap } from "./BacktestDataMap";
import { BacktestTradeChart } from "./BacktestTradeChart";
import { GridTradingPanel } from "../gridTrading/GridTradingPanel";
import { RegimeBookResults } from "./RegimeBookResults";
import { StopOutShortReplay } from "./StopOutShortReplay";
import { CapBookAgentsPanel } from "./CapBookAgentsPanel";
import { DiscoveryRankingPanel } from "./DiscoveryRankingPanel";
import { LazyResearchMount } from "./LazyResearchMount";
import { MarketStructurePanel } from "./MarketStructurePanel";
import { StructureExperimentPanel } from "./StructureExperimentPanel";
import { StructureLabelPanel } from "./StructureLabelPanel";

type PeriodMode = "lookback" | "dates";

const YEAR_PRESETS = [
  { id: "2023", start: "2023-01-01", end: "2023-12-31", label: "2023" },
  { id: "2024", start: "2024-01-01", end: "2024-12-31", label: "2024" },
  { id: "2025", start: "2025-01-01", end: "2025-12-31", label: "2025" },
  { id: "2026ytd", start: "2026-01-01", end: "", label: "2026 YTD" },
] as const;

/** Large-cap and mid-cap book only. Nothing else can be selected on this screen. */
const SYMBOL_OPTIONS = BACKTEST_CAP_SYMBOLS;
const SYMBOL_GROUPS = [
  { id: "large", label: "Large cap", symbols: BACKTEST_LARGE_SYMBOLS as readonly string[] },
  { id: "mid", label: "Mid cap", symbols: BACKTEST_MID_SYMBOLS as readonly string[] },
] as const;
const NOT_IN_CAP_BOOK = "not in the large-cap or mid-cap book";
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
  // Max is resolved from DB coverage (maxLookbackBars); this is the unknown-size fallback.
  { id: "max", limit: FALLBACK_MAX_BARS },
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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function formatUtcDay(value: string | null | undefined, withYear: boolean): string {
  if (!value) return "—";
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return String(value).slice(0, 10);
  const date = new Date(ms);
  const day = `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
  return withYear ? `${day} ${date.getUTCFullYear()}` : day;
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

/** Peak-to-trough drawdown of cumulative net $ over the trades. Peak starts at 0. */
function tradesMaxDrawdownUsd(trades: StrategyTradeRow[]): number {
  let cum = 0;
  let peak = 0;
  let dd = 0;
  for (const t of trades) {
    cum += Number(t.net_pnl_usd || 0);
    if (cum > peak) peak = cum;
    if (peak - cum > dd) dd = peak - cum;
  }
  return dd;
}

function jobStrategyLabel(strategyId: string | undefined): string {
  if (!strategyId) return "—";
  if (strategyId === "COMBO_02_V2_1_A") return "COMBO_02 V2.1-A";
  if (strategyId.startsWith("COMBO_SD_")) return `SD + CHoCH + BOS · ${strategyId}`;
  return strategyId;
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

function passedAutoLabel(book: TradeBook): string {
  if (book === "long") return "Passed long strategies (auto)";
  if (book === "hedge") return "Hedge strategy (auto)";
  return "Passed strategies (auto)";
}

export function BacktestPanel() {
  const [book, setBook] = useState<TradeBook>("short");
  const [symbols, setSymbols] = useState<string[]>(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
  const [customSymbols, setCustomSymbols] = useState("");
  const [coinFilterNotice, setCoinFilterNotice] = useState<string | null>(null);
  const [timeframes, setTimeframes] = useState<string[]>(["1h"]);
  const [autoTimeframe, setAutoTimeframe] = useState(true);
  const [autoTimeframeNote, setAutoTimeframeNote] = useState<string | null>(null);
  const handoffRevision = useBacktestHandoffStore((s) => s.revision);
  const handoffSymbols = useBacktestHandoffStore((s) => s.symbols);
  const [autoPassed, setAutoPassed] = useState(true);
  const [regimeActive, setRegimeActive] = useState(false);
  const [strategyId, setStrategyId] = useState<BacktestStrategyId>(PASSED_RUN_STRATEGY_ID);
  const [sdVariant, setSdVariant] = useState(SD_DEFAULT_VARIANT);
  const [direction, setDirection] = useState<"LONG" | "SHORT">("SHORT");
  const [lookbackId, setLookbackId] = useState<(typeof LOOKBACK_LIMITS)[number]["id"]>("60d");
  const [customLimit, setCustomLimit] = useState("");
  const [periodMode, setPeriodMode] = useState<PeriodMode>("lookback");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [principalUsd, setPrincipalUsd] = useState(1000);
  const [riskPct, setRiskPct] = useState(2);
  const [riskUsd, setRiskUsd] = useState(20);
  const [researchRiskOverride, setResearchRiskOverride] = useState(false);
  const [shortNotice, setShortNotice] = useState<string | null>(
    "Passed short strategies stay selected. The regime run uses them only while BTC daily is bearish."
  );
  const [leverage, setLeverage] = useState(2);
  const [takerFeePct, setTakerFeePct] = useState(0.04);
  const [makerFeePct, setMakerFeePct] = useState(0.02);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [preferredSymbol, setPreferredSymbol] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<OhlcvRangeRow[]>([]);
  const bookStrategies = useMemo(
    () => BACKTEST_STRATEGY_OPTIONS.filter((opt) => strategyIdsForBook(book).includes(opt.id)),
    [book],
  );
  const passedIds = passedIdsForBook(book);
  const passedIdSet = useMemo(() => new Set(passedIds), [passedIds]);
  const workingStrategies = bookStrategies.filter((opt) => opt.status === "Production candidate");
  const passedStrategies = bookStrategies.filter(
    (opt) => opt.status !== "Production candidate" && passedIdSet.has(opt.id),
  );
  const researchStrategies = bookStrategies.filter(
    (opt) => opt.status !== "Production candidate" && !passedIdSet.has(opt.id),
  );
  const isV21A = book === "hedge" || strategyId === "COMBO_02_V2_1_A";
  const isSd = strategyId === "COMBO_SD_STRUCTURE";
  const strategyMeta =
    BACKTEST_STRATEGY_OPTIONS.find((o) => o.id === strategyId) ??
    BACKTEST_STRATEGY_OPTIONS[0];
  const isDirect = Boolean(strategyMeta.direct);
  const isEngine = Boolean(strategyMeta.engineTimeframes);
  const researchUi = isV21A || isSd || isDirect;
  // Frozen COMBO_02 v1 is LONG only. Its SHORT used COMBO_04_HTF, which made zero trades.
  const frozenV1View = !researchUi;
  const [rank, setRank] = useState<StrategyRankResponse | null>(null);

  useEffect(() => {
    if (frozenV1View && direction === "SHORT") setDirection("LONG");
  }, [frozenV1View, direction]);

  // Ranked board, read-only. Only used to offer the coins that passed.
  useEffect(() => {
    let alive = true;
    Promise.resolve()
      .then(() => fetchStrategyRank())
      .then((r) => {
        if (alive && r?.status === "OK") setRank(r);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const job = useBacktestJobStore((s) => s.job);
  const storedRequest = useBacktestJobStore((s) => s.request);
  const foreignJob = useBacktestJobStore((s) => s.foreignJob);
  const storeError = useBacktestJobStore((s) => s.error);
  const active = useBacktestJobStore((s) => s.active);
  const startJob = useBacktestJobStore((s) => s.start);
  const cancelJob = useBacktestJobStore((s) => s.cancel);
  const setStoreError = useBacktestJobStore((s) => s.setError);

  const loading = active || job?.status === "running";
  const frozenV1Selection = allFrozenV1Symbols(symbols);
  const lockV1Risk =
    !researchUi && frozenV1Selection && !researchRiskOverride;
  const primaryTf = timeframes.includes("1h")
    ? "1h"
    : timeframes[0] || "1h";

  /** Ranked row for the selected strategy, direction and setup timeframe. */
  const rankedRow = useMemo(() => {
    if (isV21A || isSd) return null;
    const rankId = isDirect
      ? strategyMeta.combinationId
      : direction === "SHORT"
        ? null
        : "COMBO_02";
    if (!rankId) return null;
    for (const group of rank?.groups ?? []) {
      const hit = group.rows.find(
        (r) => r.strategy_id === rankId && r.direction === direction && r.timeframe === primaryTf,
      );
      if (hit) return hit;
    }
    return null;
  }, [rank, isV21A, isSd, isDirect, strategyMeta, direction, primaryTf]);

  function loadPassedCoins() {
    if (!rankedRow?.passing_symbols.length) return;
    setSymbols(rankedRow.passing_symbols.map((coin) => `${coin}USDT`));
    setAutoTimeframe(false);
    setCoinFilterNotice(null);
    const win = rank?.window;
    if (win?.start && win?.end) {
      // The ranked window stops before win.end; the date field is an inclusive day.
      const last = new Date(Date.parse(`${win.end}T00:00:00Z`) - 24 * 60 * 60 * 1000);
      setPeriodMode("dates");
      setStart(win.start);
      setEnd(last.toISOString().slice(0, 10));
    }
  }

  const maxLookback = useMemo(
    () => maxLookbackBars(coverage, symbols, timeframes),
    [coverage, symbols, timeframes]
  );

  const limit = useMemo(() => {
    // Date mode loads by calendar bounds from Postgres (full series slice).
    // Keep a high safety cap for warmup/unbounded paths.
    if (periodMode === "dates" && (start || end)) return API_BAR_CAP;
    const custom = Number(customLimit);
    if (customLimit && Number.isFinite(custom) && custom >= 50) {
      return Math.min(Math.floor(custom), API_BAR_CAP);
    }
    if (lookbackId === "max") return maxLookback.bars;
    return LOOKBACK_LIMITS.find((l) => l.id === lookbackId)?.limit ?? 5760;
  }, [customLimit, lookbackId, periodMode, start, end, maxLookback]);

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
        strategyId,
      }),
    [
      symbols,
      timeframes,
      direction,
      researchRiskOverride,
      leverage,
      takerFeePct,
      makerFeePct,
      strategyId,
    ]
  );
  // Settings match the frozen v1 profile; only the shown fill book differs.
  const frozenV1Settings =
    comparability.productionComparable ||
    (comparability.reasons.length === 1 && comparability.reasons[0] === "next_open_fill_book");
  const formStatusLabel = comparability.productionComparable
    ? "Paper forward-test"
    : frozenV1Settings
      ? "Research · next-open fill book"
      : "Research";

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
    if (!handoffRevision || !handoffSymbols.length) return;
    const { allowed, rejected } = filterBacktestCapSymbols(handoffSymbols);
    if (!allowed.length) {
      setCoinFilterNotice(
        `No screener coin was loaded: ${rejected.join(", ")} ${rejected.length === 1 ? "is" : "are"} ${NOT_IN_CAP_BOOK}. The current coins are unchanged.`,
      );
      return;
    }
    setSymbols(allowed);
    setAutoTimeframe(true);
    setCoinFilterNotice(
      rejected.length
        ? `${rejected.join(", ")} ${rejected.length === 1 ? "is" : "are"} ${NOT_IN_CAP_BOOK}.`
        : null,
    );
    setAutoTimeframeNote(
      `${allowed.length} screener coin${allowed.length === 1 ? "" : "s"} loaded.${
        rejected.length ? ` ${rejected.length} skipped: ${NOT_IN_CAP_BOOK}.` : ""
      } Setup timeframe follows stored OHLCV.`,
    );
  }, [handoffRevision, handoffSymbols]);

  useEffect(() => {
    if (!autoTimeframe || !symbols.length) return;
    let alive = true;
    fetchResearchOhlcvRange({ symbols, timeframes: ["15m", "1h", "4h"] })
      .then((r) => {
        if (!alive) return;
        const tf = pickAutoSetupTimeframe(r.rows || [], symbols);
        const covered = (r.rows || []).filter(
          (row) => row.timeframe === tf && (row.bars || 0) >= 50,
        ).length;
        setTimeframes((prev) => (prev.length === 1 && prev[0] === tf ? prev : [tf]));
        setAutoTimeframeNote(
          covered > 0
            ? `Auto timeframe ${tf}: ${covered} of ${symbols.length} coins have stored bars.`
            : "Auto timeframe 1h: no stored coverage yet, using the screener setup timeframe.",
        );
      })
      .catch(() => {
        if (!alive) return;
        setTimeframes((prev) => (prev.length === 1 && prev[0] === "1h" ? prev : ["1h"]));
      });
    return () => {
      alive = false;
    };
  }, [autoTimeframe, symbols]);

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

  // A job that is not what the form asked for, or that contradicts itself,
  // is not shown as a result.
  const runProblems = useMemo(() => {
    if (!job || job.status === "idle") return [];
    const problems = foreignJob ? [] : checkRunMatchesRequest(job, storedRequest);
    if (job.status === "done" || job.status === "cancelled") {
      problems.push(...checkJobInternallyConsistent(job));
    }
    return problems;
  }, [job, storedRequest, foreignJob]);
  const runBlocked = runProblems.length > 0;
  const shownResult = runBlocked ? null : result;

  // The result header describes the job that ran, not the form.
  const jobProductionComparable = job?.production_comparable === true;
  const jobIsSd = Boolean(job?.strategy_id?.startsWith("COMBO_SD_"));
  const jobIsV21A = job?.strategy_id === "COMBO_02_V2_1_A";
  const jobPrincipalUsd = job?.principal_usd ?? principalUsd;
  const jobSymbols = job?.symbols ?? [];
  const outputRef = useRef<HTMLDivElement>(null);
  const prevJobStatus = useRef<string | null>(null);

  useEffect(() => {
    const status = job?.status ?? null;
    const finished = status === "done" && prevJobStatus.current !== "done";
    prevJobStatus.current = status;
    if (finished && typeof outputRef.current?.scrollIntoView === "function") {
      outputRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [job?.status]);

  // Open one coin at a time. A chosen coin wins; otherwise the first coin with trades.
  useEffect(() => {
    const rows = shownResult?.rows ?? [];
    if (!rows.length) return;
    if (preferredSymbol) {
      const chosen = rows.find((r) => r.symbol === preferredSymbol);
      if (chosen) {
        const key = `${chosen.symbol}:${chosen.timeframe}`;
        if (selectedKey !== key) setSelectedKey(key);
        return;
      }
    }
    if (selectedKey && rows.some((r) => `${r.symbol}:${r.timeframe}` === selectedKey)) {
      return;
    }
    const firstWithTrades = rows.find(
      (r) => (r.trades?.length || 0) > 0 || r.sample_size > 0
    );
    const pick = firstWithTrades || rows[0];
    if (pick) setSelectedKey(`${pick.symbol}:${pick.timeframe}`);
  }, [shownResult?.rows, selectedKey, preferredSymbol]);

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
    if (periodMode === "dates" && !start && !end) {
      setStoreError("Custom dates mode needs a start and/or end day (UTC)");
      return;
    }
    setSelectedKey(null);
    if (autoPassed && book !== "hedge") {
      setRegimeActive(true);
      // Each book runs only its own side of the passed book.
      const side: "LONG" | "SHORT" = book === "long" ? "LONG" : "SHORT";
      setShortNotice(
        side === "LONG"
          ? "Long book: passed long strategies only. COMBO_03_TRANSITION_B LONG opens while BTC daily is bullish; nothing opens otherwise. Fills at the next bar's open."
          : "Short book: passed short strategies only. They open while BTC daily is bearish; nothing opens otherwise. Fills at the next bar's open."
      );
      try {
        await startRegimeBacktest({ symbols, limit, directions: [side] });
      } catch (err) {
        setStoreError(err instanceof Error ? err.message : "Regime backtest failed to start");
      }
      return;
    }
    if (isSd) {
      setShortNotice(
        sdVariant === "COMBO_SD_LIQ"
          ? "Research only. Equal-high or equal-low sweep must close back inside before zone, CHoCH, and BOS. Auto timeframe follows stored OHLCV for BTC, ETH, and SOL."
          : "Research only. Zone, then CHoCH, then BOS, with the selected volume rule. Auto timeframe follows stored OHLCV for BTC, ETH, and SOL."
      );
    } else if (isV21A) {
      setShortNotice(
        "COMBO_02 V2.1-A research-only: CHOPPY → WAIT. Native LONG+SHORT. Does not replace COMBO_02 V2."
      );
    } else if (isEngine) {
      setShortNotice(
        `Research only. Runs ${strategyMeta.combinationId} ${direction} on its own engine and fill model.`
      );
    } else if (isDirect) {
      setShortNotice(
        `Research only. Runs ${strategyMeta.combinationId} ${direction}. Does not switch to COMBO_04_HTF.`
      );
    } else {
      setShortNotice(null);
    }
    const researchBook = researchUi;
    await startJob({
      symbols,
      timeframes,
      // V2.1-A is native LONG+SHORT; keep direction LONG for request validation.
      direction: isV21A || frozenV1View ? "LONG" : direction,
      combination_id: isSd
        ? sdVariant
        : isV21A || isDirect
          ? strategyMeta.combinationId
          : "COMBO_02",
      strategy_id: isSd
        ? sdVariant
        : isV21A || isDirect
          ? strategyId
          : "COMBO_02_V1",
      combo_version: isSd
        ? "sd-structure"
        : isV21A || isDirect
          ? strategyMeta.comboVersion
          : "v1",
      setup_timeframe: primaryTf,
      risk_mode:
        researchBook || researchRiskOverride
          ? "RESEARCH_OVERRIDE"
          : "V1_PRODUCTION_PROFILE",
      research_risk_override: researchBook || researchRiskOverride,
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
    autoPassed,
    book,
    direction,
    strategyId,
    strategyMeta,
    isV21A,
    isSd,
    isDirect,
    researchUi,
    sdVariant,
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

  const rows = shownResult?.rows ?? [];
  const selected = rows.find(
    (r) => `${r.symbol}:${r.timeframe}` === selectedKey
  );
  const viewingSymbol = preferredSymbol && symbols.includes(preferredSymbol) ? preferredSymbol : symbols[0] || null;
  const viewingIndex = viewingSymbol ? symbols.indexOf(viewingSymbol) : -1;
  function stepCoin(delta: number) {
    if (symbols.length < 2) return;
    const base = viewingIndex >= 0 ? viewingIndex : 0;
    const next = (base + delta + symbols.length) % symbols.length;
    const symbol = symbols[next];
    setPreferredSymbol(symbol);
    const row = rows.find((item) => item.symbol === symbol);
    if (row) setSelectedKey(`${row.symbol}:${row.timeframe}`);
  }
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
    const ending = net == null ? null : jobPrincipalUsd + net;
    const retPct =
      net == null || !jobPrincipalUsd ? null : (net / jobPrincipalUsd) * 100;
    return { net, fees, ending, retPct };
  }, [selected, jobPrincipalUsd]);

  // Evaluated bars when the job reports them; older jobs only have bars_loaded.
  const barsUsed = selected?.bars_evaluated ?? selected?.bars_loaded ?? null;
  const warmupBars = Number(selected?.warmup_bars || 0);

  function applyRiskFromPrincipal(nextPrincipal: number, nextRiskPct: number) {
    const p = Math.max(0, nextPrincipal);
    const rp = Math.max(0, nextRiskPct);
    setPrincipalUsd(p);
    setRiskPct(rp);
    setRiskUsd(Math.max(1, Math.round((p * rp) / 100)));
  }

  function openTradeBook(next: TradeBook) {
    setBook(next);
    if (next === "grid") return;
    setAutoPassed(true);
    if (next === "long") {
      setStrategyId(LONG_RUN_STRATEGY_ID);
      setDirection("LONG");
      setShortNotice("Passed long strategies. The regime run uses COMBO_03_TRANSITION_B only while BTC daily is bullish.");
      return;
    }
    if (next === "hedge") {
      setStrategyId(HEDGE_STRATEGY_IDS[0]);
      setDirection("LONG");
      setShortNotice(
        "Hedge book is COMBO_02 V2.1-A. One run includes LONG and SHORT. It does not open an offsetting pair."
      );
      return;
    }
    setStrategyId(PASSED_RUN_STRATEGY_ID);
    setDirection("SHORT");
    setShortNotice(
      "Passed short strategies stay selected. The regime run uses them only while BTC daily is bearish."
    );
  }

  /** Adds typed coins. Only large-cap and mid-cap book symbols are merged. */
  function addTypedCoins() {
    const typed = parseSymbolList(customSymbols);
    if (!typed.length) return;
    const { allowed, rejected } = filterBacktestCapSymbols(typed);
    if (allowed.length) setSymbols((prev) => mergeSymbols(prev, allowed));
    setCoinFilterNotice(
      rejected.length
        ? `${rejected.join(", ")} ${rejected.length === 1 ? "is" : "are"} ${NOT_IN_CAP_BOOK}.`
        : null,
    );
    setCustomSymbols("");
  }

  function chooseStrategy(next: string) {
    if (next === "PASSED_AUTO") {
      openTradeBook(book === "grid" ? "short" : book);
      return;
    }
    setAutoPassed(false);
    const id = next as BacktestStrategyId;
    setStrategyId(id);
    if (book === "hedge" || id === "COMBO_02_V2_1_A") {
      setShortNotice(
        "COMBO_02 V2.1-A research-only: CHOPPY → WAIT. Native LONG+SHORT. Does not replace COMBO_02 V2."
      );
      return;
    }
    if (id === "COMBO_SD_STRUCTURE") {
      setSymbols(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
      setAutoTimeframe(true);
      setSdVariant(SD_DEFAULT_VARIANT);
      setDirection(book === "short" ? "SHORT" : "LONG");
      setShortNotice(
        book === "short"
          ? "SHORT uses the same supply/demand structure variant. It does not switch to COMBO_04_HTF."
          : null
      );
      return;
    }
    if (id === "COMBO_02_V1") {
      setDirection("LONG");
      setShortNotice(null);
      return;
    }
    const engineTfs = BACKTEST_STRATEGY_OPTIONS.find((o) => o.id === id)?.engineTimeframes;
    if (engineTfs) {
      // These engines only take their own setup timeframes; 1h is the ranked one.
      setAutoTimeframe(false);
      setTimeframes((prev) => {
        const keep = prev.filter((tf) => engineTfs.includes(tf));
        return keep.length ? keep : ["1h"];
      });
      setDirection("LONG");
      setShortNotice(
        `Research only. Runs ${id} LONG on its own engine and fill model. Timeframes: ${engineTfs.join(", ")}.`
      );
      return;
    }
    if (BACKTEST_STRATEGY_OPTIONS.find((o) => o.id === id)?.direct) {
      setDirection(book === "long" ? "LONG" : "SHORT");
      setShortNotice(
        `Research only. Runs ${id} ${book === "long" ? "LONG" : "SHORT"}. Does not switch to COMBO_04_HTF.`
      );
    }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto p-4">
      <div className="mb-4">
        <h1 className="font-display text-lg font-semibold text-terminal-text">
          Strategy Backtest
        </h1>
        <p className="mt-1 max-w-3xl text-xs text-terminal-muted">
          Research backtest on Postgres OHLCV. Passed strategies (auto) apply the BTC daily
          regime on each 1h bar and show that book's results. Picking one strategy runs that
          strategy alone. Same engines as the research scripts.
        </p>
        <RegimeBookResults active={regimeActive} />
        <div className="mt-3 flex flex-wrap gap-2" role="tablist" aria-label="Trade book">
          {(
            [
              ["long", "Long trades"],
              ["short", "Short trades"],
              ["hedge", "Hedge trades"],
              ["grid", "Grid trades"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={book === id}
              onClick={() => openTradeBook(id)}
              className={`rounded border px-3 py-1 text-xs ${
                book === id
                  ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                  : "border-terminal-border text-terminal-muted"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {book === "grid" ? <GridTradingPanel embedded /> : (
      <>

      <section className="mb-4 rounded border border-terminal-border/80 p-3">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <label className="flex items-center gap-2 rounded border border-terminal-accent/40 bg-terminal-accent/10 px-2 py-1 font-mono text-terminal-accent">
            <span>Strategy</span>
            <select
              aria-label="Strategy"
              value={autoPassed ? "PASSED_AUTO" : strategyId}
              onChange={(e) => chooseStrategy(e.target.value)}
              className="rounded border border-terminal-border bg-terminal-bg px-1.5 py-0.5 font-mono text-xs text-terminal-text focus:border-terminal-accent focus:outline-none"
            >
              {workingStrategies.length ? (
                <optgroup label="Working">
                  {workingStrategies.map((opt) => (
                    <option key={opt.id} value={opt.id}>
                      {bookChipLabel(opt.id, book)}
                    </option>
                  ))}
                </optgroup>
              ) : null}
              <option value="PASSED_AUTO">{passedAutoLabel(book)}</option>
              {passedStrategies.length ? (
                <optgroup label="Passed">
                  {passedStrategies.map((opt) => (
                    <option key={opt.id} value={opt.id}>
                      {bookChipLabel(opt.id, book)}
                    </option>
                  ))}
                </optgroup>
              ) : null}
              {researchStrategies.length ? (
                <optgroup label="Research">
                  {researchStrategies.map((opt) => (
                    <option key={opt.id} value={opt.id}>
                      {bookChipLabel(opt.id, book)}
                    </option>
                  ))}
                </optgroup>
              ) : null}
            </select>
          </label>
          {!autoPassed && rankedRow ? (
            <span
              className="flex flex-wrap items-center gap-2 rounded border border-terminal-border/70 px-2 py-1 text-[10px] text-terminal-muted"
              data-testid="ranked-passed-coins"
            >
              <span>
                Ranked {primaryTf} {direction}: {rankedRow.coins_passing} of {rankedRow.coins} coins
                passed
                {rankedRow.passing_symbols.length
                  ? ` (${rankedRow.passing_symbols.join(" ")})`
                  : ""}
                {rank?.window?.start ? `, tested ${rank.window.start} to ${rank.window.end}` : ""}.
              </span>
              {rankedRow.passing_symbols.length ? (
                <button
                  type="button"
                  onClick={loadPassedCoins}
                  className="rounded border border-emerald-500/50 bg-emerald-500/15 px-2 py-0.5 font-mono text-emerald-100"
                >
                  Use passed coins and tested dates
                </button>
              ) : null}
            </span>
          ) : null}
          {autoPassed ? (
            <div className="flex flex-wrap items-center gap-1" data-testid="passed-strategy-selection">
              <span className="text-[10px] text-terminal-muted">
                {book === "long"
                  ? "Passed long strategies stay selected. Run uses COMBO_03_TRANSITION_B LONG only while BTC daily is bullish."
                  : book === "hedge"
                    ? "Hedge strategy stays selected. Run uses COMBO_02 V2.1-A LONG and SHORT together."
                    : "Passed short strategies stay selected. Run uses them only while BTC daily is bearish."}
              </span>
              {passedIds.map((id) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed="true"
                  onClick={() => chooseStrategy(id)}
                  className="rounded border border-emerald-500/50 bg-emerald-500/15 px-2 py-0.5 font-mono text-[10px] text-emerald-100"
                >
                  {bookChipLabel(id, book)}
                </button>
              ))}
            </div>
          ) : null}
          {isSd ? (
            <label className="flex items-center gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 font-mono text-amber-100">
              <span>Variant</span>
              <select
                aria-label="Structure variant"
                value={sdVariant}
                onChange={(e) => setSdVariant(e.target.value)}
                className="rounded border border-terminal-border bg-terminal-bg px-1.5 py-0.5 font-mono text-xs text-terminal-text focus:border-terminal-accent focus:outline-none"
              >
                {SD_VARIANT_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <span className="rounded border border-terminal-accent/40 bg-terminal-accent/10 px-2 py-1 font-mono text-terminal-accent">
            Strategy: {autoPassed ? passedAutoLabel(book) : bookChipLabel(strategyId, book)}
            {isSd
              ? ` · ${SD_VARIANT_OPTIONS.find((opt) => opt.id === sdVariant)?.label ?? sdVariant}`
              : ""}
          </span>
          <span
            data-testid="backtest-status-chip"
            className={`rounded border px-2 py-0.5 text-[10px] ${
              comparability.productionComparable
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-amber-500/40 bg-amber-500/10 text-amber-100"
            }`}
          >
            Status: {formStatusLabel}
          </span>
          {researchUi ? (
            <span className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-100">
              RESEARCH ONLY
            </span>
          ) : null}
          <span
            className={`rounded border px-2 py-0.5 text-[10px] ${
              researchUi
                ? "border-terminal-border bg-white/5 text-terminal-text"
                : direction === "SHORT"
                  ? "border-rose-500/40 bg-rose-500/10 text-rose-200"
                  : "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
            }`}
          >
            Direction: {isV21A ? "LONG+SHORT (native)" : direction}
          </span>
          <span className="rounded border border-terminal-border bg-white/5 px-2 py-0.5 text-[10px] text-terminal-text">
            Setup TF: {isSd ? primaryTf : "1h"}
          </span>
          <span className="rounded border border-terminal-border bg-white/5 px-2 py-0.5 text-[10px] text-terminal-text">
            {isSd
              ? "Auto timeframe · closed 1h/4h zone when those candles exist"
              : isV21A
                ? "HTF: soft 4h + 15m confirm (v2 playbooks)"
                : isDirect
                  ? `Runs ${strategyMeta.combinationId} ${direction}`
                  : `HTF: 1h + 4h ${direction === "SHORT" ? "bearish" : "bullish"} alignment`}
          </span>
          <span className="rounded border border-terminal-border bg-white/5 px-2 py-0.5 text-[10px] text-terminal-muted">
            Source:{" "}
            {isSd
              ? "SD_STRUCTURE_RESEARCH_BACKTEST"
              : isV21A
                ? "V21A_RESEARCH_BACKTEST"
                : isDirect
                  ? "PASSED_CAP_BOOK"
                  : "V1_RESEARCH_BACKTEST"}
          </span>
          {!isV21A && !isSd ? (
            <span className="rounded border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-[10px] text-rose-200">
              SHORT status:{" "}
              {direction === "SHORT" ? "RESEARCH_ONLY" : "RESEARCH_ENABLED"}
            </span>
          ) : null}
        </div>

        {comparability.productionComparable ? (
          <div className="mb-3 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-[11px] text-emerald-100">
            <div className="font-semibold">FROZEN V1 CONFIGURATION</div>
            <div>Production-comparable research run</div>
          </div>
        ) : frozenV1Settings ? (
          <div
            className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-100"
            data-testid="v1-fill-book-banner"
          >
            <div className="font-semibold">FROZEN V1 SETTINGS · NOT PRODUCTION COMPARABLE</div>
            <div>
              Settings match the frozen COMBO_02 v1 profile, but the P&amp;L shown is the
              next-open fill book.
            </div>
            <div className="mt-1 text-[10px] text-amber-100/80" data-testid="fill-book-note">
              Not production comparable: {comparability.note}
            </div>
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
            {comparability.note ? (
              <div className="mt-1 text-[10px] text-amber-100/80" data-testid="fill-book-note">
                Not production comparable: {comparability.note}
              </div>
            ) : null}
          </div>
        )}

        <div className="grid gap-3 term-md:grid-cols-2 term-lg:grid-cols-4">
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Coins in this run ({symbols.length})
            </div>
            {symbols.length > 1 ? (
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px]" data-testid="coin-stepper">
                <button
                  type="button"
                  onClick={() => stepCoin(-1)}
                  className="rounded border border-terminal-border px-2 py-1 text-terminal-muted hover:text-terminal-text"
                >
                  Previous coin
                </button>
                <span className="font-mono text-terminal-text">
                  Coin {viewingIndex + 1} of {symbols.length}: {(viewingSymbol || "").replace("USDT", "")}
                </span>
                <button
                  type="button"
                  onClick={() => stepCoin(1)}
                  className="rounded border border-terminal-border px-2 py-1 text-terminal-muted hover:text-terminal-text"
                >
                  Next coin
                </button>
                <span className="text-terminal-muted">
                  Run includes every coin in this row. Output shows the coin you are viewing.
                </span>
              </div>
            ) : null}
            <div className="flex flex-wrap gap-1" data-testid="selected-coins">
              {symbols.map((s) => {
                const preset = (SYMBOL_OPTIONS as readonly string[]).includes(s);
                const symTier =
                  s === "BTCUSDT"
                    ? "core"
                    : s === "ETHUSDT" || s === "SOLUSDT"
                      ? "secondary"
                      : null;
                const badge = symTier && frozenV1Settings ? v1TierBadge(symTier) : null;
                return (
                  <button
                    key={s}
                    type="button"
                    title={
                      preset
                        ? frozenV1Settings
                          ? symbolRoleDisplay(s, primaryTf)
                          : `${s} — in this run, click to remove`
                        : `Remove ${s}`
                    }
                    onClick={() => setSymbols((prev) => prev.filter((x) => x !== s))}
                    className={`rounded border px-2 py-1 font-mono text-xs ${
                      s === viewingSymbol
                        ? "border-terminal-accent bg-terminal-accent/25 text-terminal-accent"
                        : "border-terminal-accent/40 bg-terminal-accent/10 text-terminal-accent"
                    }`}
                  >
                    {s.replace("USDT", "")}
                    {badge ? (
                      <span className={`ml-1 text-[9px] ${badge.className} rounded px-1`}>
                        {badge.label}
                      </span>
                    ) : null}
                    <span className="ml-1 text-[10px] text-terminal-muted">×</span>
                  </button>
                );
              })}
            </div>
            <div className="mb-1 mt-2 text-[10px] text-terminal-muted">
              Not in this run. Click a coin to add it.{" "}
              <span data-testid="coin-rule">Large cap and mid cap coins only (cap book).</span>
            </div>
            <div className="flex flex-col gap-1" data-testid="available-coins">
              {SYMBOL_GROUPS.map((group) => {
                const available = group.symbols.filter((s) => !symbols.includes(s));
                if (!available.length) return null;
                return (
                  <div
                    key={group.id}
                    className="flex flex-wrap items-center gap-1"
                    data-testid={`available-coins-${group.id}`}
                  >
                    <span className="w-16 text-[10px] text-terminal-muted">{group.label}</span>
                    {available.map((s) => (
                      <button
                        key={s}
                        type="button"
                        title={`${s} — not in this run, click to add`}
                        onClick={() => setSymbols((prev) => toggleInList(prev, s))}
                        className="rounded border border-terminal-border px-2 py-1 font-mono text-xs text-terminal-muted"
                      >
                        {s.replace("USDT", "")}
                      </button>
                    ))}
                  </div>
                );
              })}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              <input
                value={customSymbols}
                onChange={(e) => setCustomSymbols(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  e.preventDefault();
                  addTypedCoins();
                }}
                placeholder="Type a large or mid cap coin, e.g. INJ"
                className="min-w-[12rem] flex-1 rounded border border-terminal-border bg-transparent px-2 py-1 font-mono text-xs focus:border-terminal-accent focus:outline-none"
              />
              <button
                type="button"
                onClick={addTypedCoins}
                className="rounded border border-terminal-border px-2 py-1 text-xs text-terminal-muted hover:text-terminal-text"
              >
                Add
              </button>
            </div>
            {coinFilterNotice ? (
              <div
                className="mt-1 text-[10px] text-amber-200"
                data-testid="coin-filter-notice"
                role="status"
              >
                {coinFilterNotice}
              </div>
            ) : null}
            <div className="mt-1 text-[10px] text-terminal-muted">
              Only the coins above are backtested. A coin needs stored OHLCV (History tab) before it returns trades.
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
                    onClick={() => {
                      setAutoTimeframe(false);
                      setAutoTimeframeNote(null);
                      setTimeframes((prev) => toggleInList(prev, tf));
                    }}
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
            <div className="mt-1 text-[10px] text-terminal-muted" data-testid="auto-timeframe-note">
              {autoTimeframe
                ? autoTimeframeNote || "Auto timeframe is on. 1h is used until OHLCV coverage loads."
                : "Timeframe is manual. Turn auto back on to follow stored OHLCV."}
              {autoTimeframe ? null : (
                <button
                  type="button"
                  className="ml-2 text-terminal-accent hover:underline"
                  onClick={() => setAutoTimeframe(true)}
                >
                  Use auto timeframe
                </button>
              )}
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
            {isV21A ? (
              <div className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-100">
                V2.1-A runs native LONG+SHORT (direction filter off). COMBO_04
                SHORT path is not used.
              </div>
            ) : (
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
                {frozenV1View ? null : (
                <button
                  type="button"
                  title={
                    isSd
                      ? "SHORT research-only: supply zone, bearish CHoCH, then bearish BOS"
                      : `SHORT research-only: ${strategyMeta.combinationId}`
                  }
                  onClick={() => {
                    setDirection("SHORT");
                    setShortNotice(
                      isSd
                        ? "SHORT uses the same supply/demand structure variant. It does not switch to COMBO_04_HTF."
                        : `Research only. Runs ${strategyMeta.combinationId} SHORT. Does not switch to COMBO_04_HTF.`
                    );
                  }}
                  className={`rounded border px-2 py-1 text-left font-mono text-xs ${
                    direction === "SHORT"
                      ? "border-rose-500/50 bg-rose-500/15 text-rose-100"
                      : "border-rose-500/30 bg-rose-500/5 text-rose-200/80"
                  }`}
                >
                  <div>SHORT</div>
                  <div className="text-[9px] leading-tight text-rose-200/70">
                    Research only
                  </div>
                  <div className="text-[9px] leading-tight text-rose-200/60">
                    {isDirect ? strategyMeta.combinationId : "Impulse + pullback"}
                  </div>
                </button>
                )}
              </div>
            )}
            <div className="mt-1 font-mono text-[10px] text-terminal-muted">
              Direction: {isV21A ? "LONG+SHORT" : direction}
              {!isV21A
                ? ` · SHORT status: ${
                    direction === "SHORT" ? "RESEARCH_ONLY" : "RESEARCH_ENABLED"
                  }`
                : " · RESEARCH ONLY"}
            </div>
            {shortNotice ? (
              <div
                className={`mt-2 rounded border px-2 py-1.5 text-[10px] ${
                  isV21A
                    ? "border-amber-500/40 bg-amber-500/10 text-amber-100"
                    : "border-rose-500/40 bg-rose-500/10 text-rose-100"
                }`}
              >
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
            BTCUSDT core — 2% / ${Math.round((principalUsd * 2) / 100)}
          </div>
          <div className="font-mono text-[11px] text-terminal-text">
            ETHUSDT secondary — 2% / ${Math.round((principalUsd * 2) / 100)}
          </div>
          <div className="font-mono text-[11px] text-terminal-text">
            SOLUSDT secondary — 2% / ${Math.round((principalUsd * 2) / 100)}
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
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="text-[11px] uppercase tracking-wide text-terminal-muted">
              Period
            </span>
            <button
              type="button"
              onClick={() => {
                const range = lastNDaysUtc(365);
                setPeriodMode("dates");
                setStart(range.start);
                setEnd(range.end);
              }}
              className={`rounded border px-2 py-1 text-xs ${
                periodMode === "dates" &&
                start === lastNDaysUtc(365).start &&
                end === lastNDaysUtc(365).end
                  ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                  : "border-terminal-border text-terminal-muted"
              }`}
            >
              Last 365 days
            </button>
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
                  const bars = l.id === "max" ? maxLookback.bars : l.limit;
                  const days = barsToApproxDays(bars, primaryTf);
                  const label =
                    l.id !== "max"
                      ? lookbackLabelForTf(l.limit, primaryTf)
                      : maxLookback.source === "api-cap"
                        ? `Max (${bars.toLocaleString()} bars · API cap, DB has ${(maxLookback.dbBars ?? 0).toLocaleString()})`
                        : maxLookback.source === "fallback"
                          ? `Max (${bars.toLocaleString()} bars · DB size unknown)`
                          : `Max (${bars.toLocaleString()} bars in DB)`;
                  return (
                    <button
                      key={l.id}
                      type="button"
                      data-testid={l.id === "max" ? "lookback-max" : undefined}
                      title={formatBarsDuration(bars, primaryTf)}
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
            <div>
              Strategy: {autoPassed ? passedAutoLabel(book) : bookChipLabel(strategyId, book)}
              {isSd
                ? ` · ${sdVariant} · RESEARCH ONLY`
                : isV21A || isDirect
                  ? " · RESEARCH ONLY"
                  : ""}
            </div>
            <div>
              Status:{" "}
              {formStatusLabel}
            </div>
            <div>
              Direction: {isV21A ? "LONG+SHORT (native)" : direction}
            </div>
            <div>Setup timeframe: {primaryTf} ({timeframeRoleLabel(primaryTf)})</div>
            <div>
              {isSd
                ? "Auto timeframe on. Location uses a closed 1h or 4h zone when those candles are available."
                : isV21A
                ? "HTF: soft 4h policy + 15m confirmation (v2 playbooks; CHOPPY WAIT)"
                : isDirect
                ? `Runs ${strategyMeta.combinationId} on the selected direction. COMBO_04_HTF is not used.`
                : `HTF requirement: 1h + 4h ${
                    direction === "SHORT" ? "bearish" : "bullish"
                  } alignment${
                    direction === "SHORT" ? " · impulse + pullback required" : ""
                  }`}
            </div>
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
              {!comparability.productionComparable && comparability.note ? (
                <span className="ml-2 text-[10px] text-amber-100/80">({comparability.note})</span>
              ) : null}
            </div>
          </div>

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
              {coverage.slice(0, 4).map(coverageRowLabel).join(" · ")}
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

      <div ref={outputRef} id="backtest-output" className="scroll-mt-2">
      <h2 className="mb-2 font-display text-sm font-semibold text-terminal-text">
        Backtest output
        {selected ? ` · ${selected.symbol.replace("USDT", "")}` : viewingSymbol ? ` · ${viewingSymbol.replace("USDT", "")}` : ""}
      </h2>
      {symbols.length > 1 && selected ? (
        <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px]">
          <button
            type="button"
            onClick={() => stepCoin(-1)}
            className="rounded border border-terminal-border px-2 py-1 text-terminal-muted hover:text-terminal-text"
          >
            Previous coin
          </button>
          <span className="font-mono text-terminal-text">
            {selected.symbol} · {selected.timeframe} · coin {Math.max(1, symbols.indexOf(selected.symbol) + 1)} of {symbols.length}
          </span>
          <button
            type="button"
            onClick={() => stepCoin(1)}
            className="rounded border border-terminal-border px-2 py-1 text-terminal-muted hover:text-terminal-text"
          >
            Next coin
          </button>
        </div>
      ) : null}
      {error ? (
        <div className="mb-3 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      ) : null}

      {foreignJob && job && job.status !== "idle" ? (
        <div
          className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-100"
          data-testid="backtest-foreign-run"
        >
          <div className="font-semibold">
            This is a different run than the form above. It was not started by this form&apos;s Run button.
          </div>
          <div className="mt-1 font-mono">
            Job {job.job_id || "—"} · {jobStrategyLabel(job.strategy_id)} · {directionLabel(job)} ·{" "}
            {jobSymbols.join(", ") || "—"} · {(job.timeframes ?? []).join(", ") || "—"}
          </div>
          <div className="mt-1 text-amber-100/80">The header below describes that job.</div>
        </div>
      ) : null}

      {runBlocked ? (
        <div
          className="mb-3 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-[11px] text-red-200"
          data-testid="backtest-run-mismatch"
        >
          <div className="font-semibold">
            Result not shown: the job does not match what was requested, or contradicts itself.
          </div>
          <ul className="mt-1 list-disc pl-4 font-mono">
            {runProblems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {shownResult ? (
        <>
          {selected ? (
            <RunLead
              row={selected}
              strategyLabel={jobStrategyLabel(job?.strategy_id)}
              requestedBars={job?.limit ?? 0}
              productionComparable={jobProductionComparable}
            />
          ) : null}
          <div className="mb-3 grid gap-2 rounded border border-terminal-accent/30 bg-terminal-accent/5 px-3 py-3 term-md:grid-cols-4">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
                Principal
              </div>
              <div className="font-mono text-lg text-terminal-text">
                ${jobPrincipalUsd.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
                {selected
                  ? `Net profit (${selected.symbol} ${selected.timeframe})`
                  : "Net profit (summed over cells)"}
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
                {selected
                  ? `Ending equity (${selected.symbol} ${selected.timeframe} only)`
                  : "Principal + summed net"}
              </div>
              <div className="font-mono text-lg text-terminal-accent">
                {selected
                  ? selectedCapital.ending == null
                    ? "—"
                    : `$${selectedCapital.ending.toFixed(2)}`
                  : `$${(jobPrincipalUsd + pooled.pnlNet).toFixed(2)}`}
              </div>
              {selected ? null : (
                <div className="mt-0.5 text-[10px] text-terminal-muted">
                  Cells are independent accounts, each starting from the principal. This is
                  not one account&apos;s ending equity.
                </div>
              )}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
                {selected ? "Return on principal" : "Summed net ÷ one principal"}
              </div>
              <div className="font-mono text-lg text-terminal-text">
                {selected
                  ? selectedCapital.retPct == null
                    ? "—"
                    : `${selectedCapital.retPct >= 0 ? "+" : ""}${selectedCapital.retPct.toFixed(2)}%`
                  : jobPrincipalUsd
                    ? `${pooled.pnlNet >= 0 ? "+" : ""}${((pooled.pnlNet / jobPrincipalUsd) * 100).toFixed(2)}%`
                    : "—"}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-terminal-muted">
                Fees {money(-(selected ? selectedCapital.fees : pooled.fees)).replace("+", "-")} · 1R
                = ${selected?.risk_usd ?? job?.risk_usd ?? riskUsd}
                {selected ? ` · closed ${selected.sample_size}` : ""}
              </div>
            </div>
          </div>
          {selected ? (
            <SelectedDetail
              key={`${selected.symbol}:${selected.timeframe}:${selected.direction}`}
              row={selected}
              principalUsd={jobPrincipalUsd}
            />
          ) : null}
          <div
            className="mb-3 grid gap-1 rounded border border-terminal-border/80 bg-black/20 px-3 py-2 font-mono text-[11px] text-terminal-muted term-md:grid-cols-2"
            data-testid="backtest-result-header"
          >
            <div>
              Strategy:{" "}
              <span className="text-terminal-text">
                {jobStrategyLabel(job?.strategy_id)} · {job?.combo_version || "—"}
              </span>
              {jobProductionComparable ? null : (
                <span className="ml-2 rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-100">
                  RESEARCH ONLY
                </span>
              )}
            </div>
            <div>
              Status:{" "}
              <span className="text-terminal-text">
                {jobProductionComparable ? "Paper forward-test" : "Research"}
              </span>
            </div>
            <div>
              Direction:{" "}
              <span className="text-terminal-text">{directionLabel(job)}</span>
              {!jobIsV21A && !jobIsSd ? (
                <>
                  {" · "}
                  SHORT status:{" "}
                  <span className="text-rose-200">
                    {job?.short_status ||
                      (job?.direction === "SHORT" ? "RESEARCH_ONLY" : "RESEARCH_ENABLED")}
                  </span>
                </>
              ) : null}
            </div>
            <div>
              Symbol role:{" "}
              <span className="text-terminal-text">
                {selected
                  ? symbolRoleDisplay(selected.symbol, selected.timeframe)
                  : jobSymbols
                      .map((s) => symbolRoleDisplay(s, job?.setup_timeframe || "1h"))
                      .join("; ") || "—"}
              </span>
            </div>
            <div>
              Effective risk:{" "}
              <span className="text-terminal-text">
                {selected?.effective_risk_percent != null
                  ? `${(selected.effective_risk_percent * 100).toFixed(2)}% / $${num(selected.effective_risk_amount ?? selected.risk_usd, 0)}`
                  : job?.effective_risk_percent != null
                    ? `${(job.effective_risk_percent * 100).toFixed(2)}% / $${num(job.effective_risk_amount, 0)}`
                    : job?.risk_usd != null
                      ? `${job.principal_usd ? `${((job.risk_usd / job.principal_usd) * 100).toFixed(2)}% / ` : ""}$${num(job.risk_usd, 0)}`
                      : "—"}
              </span>
            </div>
            <div>
              Risk source:{" "}
              <span className="text-terminal-text">
                {selected?.risk_source || job?.risk_source || "—"}
              </span>
            </div>
            <div>
              Setup timeframe:{" "}
              <span className="text-terminal-text">
                {selected?.timeframe || job?.setup_timeframe || job?.timeframes?.[0] || "—"} (
                {timeframeRoleLabel(
                  selected?.timeframe || job?.setup_timeframe || job?.timeframes?.[0] || ""
                )}
                )
              </span>
            </div>
            <div>
              HTF requirement:{" "}
              <span className="text-terminal-text">{htfRequirementLabel(job)}</span>
            </div>
            <div>
              Requested range:{" "}
              <span className="text-terminal-text">
                {job?.start_date || job?.end_date
                  ? `Calendar ${job.start_date || "…"} → ${job.end_date || "…"}`
                  : `DB-tail ${job?.limit ?? "—"} bars (evaluated window; warm-up bars are loaded in addition)`}
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
                {barsUsed ?? "—"}
                {selected?.timeframe
                  ? ` · ≈ ${barsToApproxDays(Number(barsUsed || 0), selected.timeframe) ?? "—"} days`
                  : ""}
                {barsUsed != null && warmupBars > 0
                  ? ` · + ${warmupBars.toLocaleString()} warm-up = ${(barsUsed + warmupBars).toLocaleString()} total`
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
                  jobProductionComparable ? "text-emerald-300" : "text-amber-200"
                }
              >
                {jobProductionComparable ? "YES" : "NO"}
              </span>
              {job?.mismatch_reasons?.length ? (
                <span className="ml-2 text-[10px] text-amber-100/80">
                  Reasons: {job.mismatch_reasons.join(", ")}
                </span>
              ) : null}
              {!jobProductionComparable && job?.production_comparable_note ? (
                <span className="ml-2 text-[10px] text-amber-100/80" data-testid="job-fill-book-note">
                  ({job.production_comparable_note})
                </span>
              ) : null}
            </div>
            <div>
              Research-only status:{" "}
              <span className="text-terminal-text">
                {jobProductionComparable ? "NO" : "YES"}
              </span>
            </div>
            <div>
              Playbook:{" "}
              <span className="text-terminal-text">{shownResult.playbook || "—"}</span>
            </div>
            <div>
              Elapsed:{" "}
              <span className="text-terminal-text">
                {shownResult.elapsed_seconds != null ? `${shownResult.elapsed_seconds}s` : "—"}
              </span>
            </div>
            <div>
              Pooled n={pooled.n} · avg R gross={num(pooled.avgR)} · gross PnL={money(pooled.pnl)}
              {" · "}net PnL={money(pooled.pnlNet)}
            </div>
            <div>
              Fees: taker {(job?.taker_fee_pct ?? takerFeePct).toFixed(2)}% / maker{" "}
              {(job?.maker_fee_pct ?? makerFeePct).toFixed(2)}% · leverage{" "}
              {job?.leverage ?? leverage}x · entry {FEE_DISPLAY.entryType}
            </div>
            {rows.some((row) => row.sd_review) ? (
              <div className="text-terminal-muted term-md:col-span-2">
                {rows
                  .filter((row) => row.sd_review)
                  .map((row) => {
                    const review = row.sd_review!;
                    const split = review.chronological_split;
                    const daily = review.daily_loss_slice;
                    const regime = review.regime_counts;
                    return (
                      <div key={`${row.symbol}:${row.timeframe}`}>
                        {row.symbol} {row.timeframe}: perpetual 24h, no contract roll, funding not charged.
                        Slippage {(Number(review.slippage_rate || 0) * 100).toFixed(2)}% per side is in net R.
                        {review.sample_warning
                          ? ` Closed sample ${row.sample_size} is under ${review.min_closed_sample}.`
                          : ""}
                        {split
                          ? ` Develop n=${split.development_n} avg net R ${num(split.development_avg_r_net)} · holdout n=${split.holdout_n} avg net R ${num(split.holdout_avg_r_net)}.`
                          : ""}
                        {daily
                          ? ` After a ${daily.max_loss_r}R UTC daily stop: kept ${daily.kept_n}, skipped ${daily.skipped_n}, avg net R ${num(daily.avg_r_net)}.`
                          : ""}
                        {regime
                          ? ` Regime counts trend ${regime.trend ?? 0}, range ${regime.range ?? 0}.`
                          : ""}
                        {" "}Random-entry baseline is not run. Macro event days are not removed.
                      </div>
                    );
                  })}
              </div>
            ) : null}
            <div className="text-amber-100/90 term-md:col-span-2">
              Historical research only. Not a profitability claim. No paper or live
              trade created.
            </div>
            <div
              className="text-amber-100/90 term-md:col-span-2"
              data-testid="backtest-in-sample-warning"
            >
              In-sample only: every figure is computed on the same history used to pick
              the strategy and settings. There is no out-of-sample split and no
              multiple-comparison adjustment, so a positive result is a hypothesis, not
              evidence of an edge.
            </div>
            <div className="term-md:col-span-2 text-terminal-text">
              paper_trade_created ={" "}
              {String(job?.paper_trade_created ?? false)} · live_trade_created ={" "}
              {String(job?.live_trade_created ?? false)} · telegram_sent ={" "}
              {String(job?.telegram_sent ?? false)}
            </div>
            <div className="text-terminal-muted term-md:col-span-2">{shownResult.disclaimer}</div>
          </div>
        </>
      ) : null}

      {runBlocked ? null : (
      <div className="min-w-0 overflow-x-auto rounded border border-terminal-border/80">
        <table className="w-full min-w-[1000px] border-collapse text-left text-xs">
          <thead className="bg-black/30 text-[11px] uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-2 py-2">Symbol</th>
              <th className="px-2 py-2">TF</th>
              <th className="px-2 py-2">n</th>
              <th className="px-2 py-2">Avg R gross</th>
              <th className="px-2 py-2">Avg R net</th>
              <th className="px-2 py-2">TP1</th>
              <th className="px-2 py-2">SL</th>
              <th className="px-2 py-2">PF gross</th>
              <th className="px-2 py-2">PF net</th>
              <th className="px-2 py-2">Max DD R (gross)</th>
              <th className="px-2 py-2">Gross PnL</th>
              <th className="px-2 py-2">Net PnL</th>
              <th className="px-2 py-2">Fees</th>
              <th className="px-2 py-2">Equity R (gross)</th>
              <th className="px-2 py-2">Period</th>
            </tr>
          </thead>
          <tbody>
            {loading && !rows.length ? (
              <tr>
                <td colSpan={15} className="px-3 py-8 text-center text-terminal-muted">
                  {progressView
                    ? `${progressView.pct}% complete — ${progressView.done}/${progressView.total} · ETA ${progressView.etaLabel}`
                    : "Starting backtest…"}
                </td>
              </tr>
            ) : null}
            {!loading && !rows.length ? (
              <tr>
                <td colSpan={15} className="px-3 py-8 text-center text-terminal-muted">
                  {shownResult
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
                    onClick={() => {
                      setPreferredSymbol(row.symbol);
                      setSelectedKey(key);
                    }}
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
                    <td
                      className={`px-2 py-2 font-mono ${
                        row.average_R_net == null
                          ? "text-terminal-muted"
                          : row.average_R_net >= 0
                            ? "text-emerald-400"
                            : "text-red-400"
                      }`}
                    >
                      {row.sample_size ? num(row.average_R_net) : "—"}
                    </td>
                    <td className="px-2 py-2 font-mono">{pct(row.tp1_hit_rate)}</td>
                    <td className="px-2 py-2 font-mono">{pct(row.sl_rate)}</td>
                    <td className="px-2 py-2 font-mono">{num(row.profit_factor)}</td>
                    <td className="px-2 py-2 font-mono">{num(row.profit_factor_net)}</td>
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
                      {row.pnl_model === "CAUSAL_NEXT_OPEN" ? (
                        <div className="text-[9px] uppercase tracking-wide text-terminal-accent">
                          causal next-open
                        </div>
                      ) : null}
                    </td>
                    <td className="px-2 py-2 font-mono text-terminal-muted">
                      {row.sample_size ? money(-(row.fees_usd ?? 0)).replace("+", "-") : "—"}
                    </td>
                    <td
                      className="px-2 py-2"
                      title={
                        row.pnl_model === "CAUSAL_NEXT_OPEN" && row.trades?.length
                          ? "This line is causal net R from the trades, not gross engine R."
                          : undefined
                      }
                    >
                      <Sparkline
                        values={
                          row.pnl_model === "CAUSAL_NEXT_OPEN" && row.trades?.length
                            ? causalEquityR(row.trades)
                            : row.equity_curve_r || []
                        }
                      />
                      {row.pnl_model === "CAUSAL_NEXT_OPEN" && row.trades?.length ? (
                        <div className="text-[9px] text-terminal-muted">causal net R</div>
                      ) : null}
                    </td>
                    <td className="px-2 py-2 font-mono text-terminal-muted">
                      <div className="text-[10px]">
                        Actual: {String(row.period_start || "—").slice(0, 10)} →{" "}
                        {String(row.period_end || "—").slice(0, 10)}
                      </div>
                      <div className="text-[10px]">
                        {row.bars_evaluated != null ? "Bars evaluated" : "Bars loaded"}:{" "}
                        {row.bars_evaluated ?? row.bars_loaded ?? "—"}
                        {row.timeframe
                          ? ` · ≈ ${barsToApproxDays(Number(row.bars_evaluated ?? row.bars_loaded ?? 0), row.timeframe) ?? "—"}d`
                          : ""}
                      </div>
                      <div className="text-[10px]">
                        {row.risk_source || "—"} ·{" "}
                        {jobProductionComparable ? "prod-comparable" : "research-only"}
                      </div>
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>
      )}
      </div>

      <BacktestDataMap
        strategyLabel={backtestStrategyLabel(strategyId)}
        symbols={symbols.length}
        ohlcvBars={
          rows.length
            ? rows.reduce((sum, row) => sum + Number(row.bars_loaded || 0), 0)
            : null
        }
        runs={rows.length ? 1 : 0}
        trades={rows.reduce((sum, row) => sum + Number(row.sample_size || 0), 0)}
      />

      <CapBookAgentsPanel />

      <LazyResearchMount panelId="structure-experiment" label="OHLCV BOS/CHoCH timing experiment">
        <StructureExperimentPanel />
      </LazyResearchMount>
      <LazyResearchMount panelId="structure-labels" label="Closed structure labels">
        <StructureLabelPanel />
      </LazyResearchMount>
      <LazyResearchMount panelId="discovery-ranking" label="Market structure discovery">
        <DiscoveryRankingPanel />
      </LazyResearchMount>
      </>
      )}
    </div>
  );
}

function causalEquityR(trades: StrategyTradeRow[]): number[] {
  let running = 0;
  return trades.map((trade) => {
    running += Number(trade.r_net || 0);
    return running;
  });
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
  const endingFromRow = principalUsd + Number(row.pnl_usd_net || 0);
  // Same principal and trades as the Equity $ column, so the total is its last cell.
  const ending =
    withEquity.length && trades.every((t) => t.net_pnl_usd != null)
      ? withEquity[withEquity.length - 1].equity
      : endingFromRow;
  const winRateNet = trades.length
    ? trades.filter((t) => Number(t.net_pnl_usd || 0) > 0).length / trades.length
    : null;
  const maxDdUsd = trades.length ? tradesMaxDrawdownUsd(trades) : null;
  const rowRiskUsd = Number(row.risk_usd || 0);
  // $ basis: 1R is the configured row.risk_usd, not the risk each trade realized.
  const usdBasisAvg = (pick: (t: StrategyTradeRow) => number | null | undefined) =>
    rowRiskUsd > 0 && trades.some((t) => pick(t) != null)
      ? trades.reduce((sum, t) => sum + Number(pick(t) || 0), 0) / trades.length / rowRiskUsd
      : null;
  const avgRUsdGross = row.average_R_usd_basis_gross ?? usdBasisAvg((t) => t.gross_pnl_usd);
  const avgRUsdNet = row.average_R_usd_basis_net ?? usdBasisAvg((t) => t.net_pnl_usd);
  const equityCurve = row.equity_curve_usd_net?.length
    ? { label: "Equity curve ($ net, equity_curve_usd_net)", values: row.equity_curve_usd_net }
    : row.pnl_model === "CAUSAL_NEXT_OPEN" && trades.length
      ? { label: "Equity curve (causal net R, from trades)", values: causalEquityR(trades) }
      : { label: "Equity curve (gross R, equity_curve_r)", values: row.equity_curve_r || [] };
  const blotterScrollRef = useRef<HTMLDivElement | null>(null);
  const blotterVirtualizer = useVirtualizer({
    count: withEquity.length,
    getScrollElement: () => blotterScrollRef.current,
    estimateSize: () => 28,
    overscan: 16,
  });
  return (
    <section className="mt-4 rounded border border-terminal-border/80 p-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-terminal-text">
          {row.pnl_model === "CAUSAL_NEXT_OPEN" ? "Causal trade blotter" : "Trade blotter"} —{" "}
          {row.symbol} · {row.timeframe} · {row.direction}
        </h2>
        <span className="font-mono text-[11px] text-terminal-muted">
          {row.pnl_model === "CAUSAL_NEXT_OPEN" ? "CAUSAL_NEXT_OPEN · " : ""}
          Principal ${principalUsd.toFixed(2)} → total ${ending.toFixed(2)} · net{" "}
          {money(row.pnl_usd_net)} · net R {num(row.average_R_net)} · win rate (net &gt; 0){" "}
          {pct(winRateNet)} · max DD {maxDdUsd == null ? "—" : `$${maxDdUsd.toFixed(2)}`} /{" "}
          {num(row.max_drawdown_R)}
          {row.pnl_model === "CAUSAL_NEXT_OPEN" ? " causal R" : "R gross"} · fees{" "}
          {money(-(row.fees_usd ?? 0)).replace("+", "-")}
        </span>
      </div>
      {row.pnl_model === "CAUSAL_NEXT_OPEN" && trades[0] ? (
        <p className="mb-2 font-mono text-[11px] text-terminal-text" data-testid="causal-trade-detail">
          Trade {String(trades[0].trade_id ?? trades[0].trade_no ?? 1)} {trades[0].direction} entry{" "}
          {px(trades[0].entry_price)} exit {px(trades[0].exit_price)} outcome {trades[0].outcome || "—"} net{" "}
          {money(trades[0].net_pnl_usd)} R {num(trades[0].r_net)} fee {money(trades[0].fee_total_usd)}
        </p>
      ) : null}
      {row.optimistic_comparison ? (
        <div
          className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-[11px] text-amber-100"
          data-testid="optimistic-comparison"
        >
          <div className="font-semibold uppercase tracking-wide">
            {row.optimistic_comparison.label || "Optimistic comparison — LEGACY_SIGNAL_CLOSE"}
          </div>
          <div className="mt-1 font-mono">
            LEGACY_SIGNAL_CLOSE net {money(row.optimistic_comparison.net_pnl)} · net R{" "}
            {num(row.optimistic_comparison.net_r)} · win rate {pct(row.optimistic_comparison.win_rate)} ·
            max DD {money(row.optimistic_comparison.max_drawdown_usd == null ? null : -Math.abs(Number(row.optimistic_comparison.max_drawdown_usd)))} ·
            fees {money(row.optimistic_comparison.fees == null ? null : -Math.abs(Number(row.optimistic_comparison.fees)))} ·
            trades {row.optimistic_comparison.trades ?? "—"} · winners {row.optimistic_comparison.winners ?? "—"}
          </div>
        </div>
      ) : null}

      <div className="mb-3 grid gap-3 term-md:grid-cols-2">
        <div>
          <div
            className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted"
            data-testid="equity-curve-label"
          >
            {equityCurve.label}
          </div>
          <div className="rounded border border-terminal-border/60 bg-black/20 p-2">
            <SparklineLarge values={equityCurve.values} />
          </div>
        </div>
        <div className="font-mono text-[11px] text-terminal-muted">
          <div>
            Realized-risk avg R: gross {num(row.average_R)} · net {num(row.average_R_net)}
          </div>
          <div>
            $ basis avg R (1R = ${num(row.risk_usd)}): gross {num(avgRUsdGross)} · net{" "}
            {num(avgRUsdNet)}
          </div>
          {row.r_definition ? <div>R definition: {row.r_definition}</div> : null}
          <div className="mt-1">
            {row.engine_fill_model
              ? `Fill model (${row.engine_family} engine): ${row.engine_fill_model}. Times are candle open times (UTC).`
              : "System entry = engine entry_price (LIMIT_RETEST broken level or market close). Exit = first touch of TP1/TP2/TP3/SL on later bars. Times are candle open times (UTC)."}
          </div>
          <div className="mt-2 text-terminal-text">
            Principal ${principalUsd.toFixed(2)} + net profit {money(row.pnl_usd_net)} ={" "}
            <span className="text-terminal-accent">total ${endingFromRow.toFixed(2)}</span>
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

      <div
        ref={blotterScrollRef}
        className="max-h-[420px] min-w-0 overflow-auto rounded border border-terminal-border/60"
        data-testid="backtest-trade-blotter"
      >
        <table className="w-full min-w-[1640px] border-collapse text-left text-[11px]">
          <thead className="sticky top-0 z-10 bg-black/80 text-[10px] uppercase tracking-wide text-terminal-muted">
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
              <th
                className="px-2 py-2"
                title="At the signal bar: does the swing trend back the trade side, trend, volume against its 20-bar average, and whether the close was in the lower or upper half of the prior 24-bar range. Display only."
              >
                Market context
              </th>
              <th className="px-2 py-2">Hold</th>
              <th className="px-2 py-2">Qty</th>
              <th className="px-2 py-2">Margin</th>
              <th className="px-2 py-2">Type</th>
              <th className="px-2 py-2">Fees</th>
              <th className="px-2 py-2">Gross</th>
              <th className="px-2 py-2">Net</th>
              <th className="px-2 py-2">R net</th>
              <th className="px-2 py-2">R net ($ basis)</th>
              <th className="px-2 py-2">Equity $</th>
            </tr>
          </thead>
          <tbody>
            {!trades.length ? (
              <tr>
                <td colSpan={20} className="px-3 py-6 text-center text-terminal-muted">
                  No closed trades for this cell
                </td>
              </tr>
            ) : (
              <>
                {blotterVirtualizer.getVirtualItems().length > 0 ? (
                  <tr aria-hidden style={{ height: blotterVirtualizer.getVirtualItems()[0]?.start ?? 0 }}>
                    <td colSpan={20} />
                  </tr>
                ) : null}
                {blotterVirtualizer.getVirtualItems().map((vRow) => {
                  const { t, equity } = withEquity[vRow.index];
                  const key = tradeRowKey(t);
                  return (
                    <TradeRow
                      key={`${t.signal_time}-${t.entry_price}-${t.trade_no}`}
                      t={t}
                      equityUsd={equity}
                      rowRiskUsd={rowRiskUsd}
                      selected={key === selectedTradeKey}
                      onSelect={() => setSelectedTradeKey(key)}
                    />
                  );
                })}
                {blotterVirtualizer.getVirtualItems().length > 0 ? (
                  <tr
                    aria-hidden
                    style={{
                      height: Math.max(
                        0,
                        blotterVirtualizer.getTotalSize() -
                          (blotterVirtualizer.getVirtualItems().at(-1)?.end ?? 0),
                      ),
                    }}
                  >
                    <td colSpan={20} />
                  </tr>
                ) : null}
              </>
            )}
          </tbody>
        </table>
      </div>

      <StopOutShortReplay
        trades={trades}
        symbol={row.symbol}
        timeframe={row.timeframe}
        periodEnd={row.period_end}
      />

      <LazyResearchMount panelId="market-structure" label="Market Structure & Regime">
        <MarketStructurePanel
          analytics={row.market_structure}
          symbol={row.symbol}
          timeframe={row.timeframe}
        />
      </LazyResearchMount>
    </section>
  );
}

/** One cell: structure verdict, trend, volume and zone at the signal bar. */
function MarketContextCell({ context }: { context: StrategyTradeRow["market_context"] }) {
  if (!context?.label) {
    return <td className="px-2 py-1.5 text-terminal-muted">—</td>;
  }
  const [verdict, ...rest] = context.label.split(" · ");
  const verdictCls =
    context.structure === "CORRECT"
      ? "text-emerald-400"
      : context.structure === "WRONG"
        ? "text-red-400"
        : "text-terminal-muted";
  const detail = [
    context.relative_volume != null ? `volume ${context.relative_volume.toFixed(2)}x its 20-bar average` : null,
    context.range_position != null
      ? `close at ${(context.range_position * 100).toFixed(0)}% of the prior 24-bar range`
      : null,
    context.decision_bar ? `bar ${context.decision_bar}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <td
      className="whitespace-nowrap px-2 py-1.5"
      title={detail || undefined}
      data-testid="trade-market-context"
    >
      <span className={verdictCls}>{verdict}</span>
      <span className="text-terminal-muted"> · {rest.join(" · ")}</span>
    </td>
  );
}

function TradeRow({
  t,
  equityUsd,
  rowRiskUsd,
  selected,
  onSelect,
}: {
  t: StrategyTradeRow;
  equityUsd: number;
  /** The cell's configured 1R in $, for the $ basis R column. */
  rowRiskUsd: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const net = t.net_pnl_usd;
  const netCls =
    net == null ? "text-terminal-muted" : net >= 0 ? "text-emerald-400" : "text-red-400";
  const marginTitle = [
    t.leverage != null
      ? `${t.leverage}x · notional $${(t.notional_entry_usd ?? 0).toFixed(2)} · approx liq ${t.liquidation_price ?? "—"}`
      : null,
    t.account_equity != null ? `equity at entry $${t.account_equity.toFixed(2)}` : null,
    t.requested_risk_usd != null ? `requested risk $${t.requested_risk_usd.toFixed(2)}` : null,
    t.capped_by_leverage ? "CAPPED by leverage" : null,
  ]
    .filter(Boolean)
    .join(" · ");
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
      <MarketContextCell context={t.market_context} />
      <td className="px-2 py-1.5 font-mono">{t.holding_bars ?? "—"}</td>
      <td className="px-2 py-1.5 font-mono">{num(t.qty, 4)}</td>
      <td
        className="px-2 py-1.5 font-mono text-terminal-muted"
        title={marginTitle || undefined}
      >
        {t.margin_usd != null ? `$${t.margin_usd.toFixed(2)}` : "—"}
        {t.capped_by_leverage ? <span className="ml-1 text-amber-200">cap</span> : null}
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
      <td className={`px-2 py-1.5 font-mono ${netCls}`}>
        {net != null && rowRiskUsd > 0 ? num(net / rowRiskUsd, 3) : "—"}
      </td>
      <td className="px-2 py-1.5 font-mono text-terminal-accent">${equityUsd.toFixed(2)}</td>
    </tr>
  );
}

function RunLead({
  row,
  strategyLabel,
  requestedBars,
  productionComparable,
}: {
  row: StrategyMatrixRow;
  strategyLabel: string;
  requestedBars: number;
  /** The job's flag. Rows do not decide this. */
  productionComparable: boolean;
}) {
  // Evaluated bars when the job reports them; older jobs only have bars_loaded.
  const counted = row.bars_evaluated ?? row.bars_loaded;
  const countedLabel = row.bars_evaluated != null ? "evaluated" : "loaded";
  const warmupBars = Number(row.warmup_bars || 0);
  const warmup = warmupBars > 0 ? `plus ${warmupBars.toLocaleString()} warm-up, ` : "";
  const range = `${formatUtcDay(row.period_start, true)} → ${formatUtcDay(row.period_end, true)}`;
  // Warm-up bars are not part of the request, so only the evaluated count is compared with it.
  const bars =
    counted != null && requestedBars && counted !== requestedBars
      ? `${requestedBars.toLocaleString()} bars (${counted.toLocaleString()} ${countedLabel}, ${warmup}${range})`
      : `${(counted ?? requestedBars ?? 0).toLocaleString()} bars (${warmup}${range})`;
  const trades = row.trades || [];
  const wins = trades.filter((trade) => String(trade.outcome || "").toUpperCase().startsWith("TP")).length;
  const stops = trades.filter((trade) => String(trade.outcome || "").toUpperCase().startsWith("SL")).length;
  const stamps = trades
    .flatMap((trade) => [trade.signal_time, trade.exit_time])
    .map((value) => (value ? Date.parse(value) : NaN))
    .filter((value) => Number.isFinite(value));
  return (
    <div className="mb-3">
      <div className="font-mono text-[12px] text-terminal-text">
        {row.symbol} · {row.timeframe} · {strategyLabel} · {row.direction} · {bars}
      </div>
      {stamps.length ? (
        <p className="mt-1 text-[11px] text-terminal-muted">
          The {trades.length} trades run from {formatUtcDay(new Date(Math.min(...stamps)).toISOString(), false)} through{" "}
          {formatUtcDay(new Date(Math.max(...stamps)).toISOString(), true)} ({wins} TP, {stops} stop-outs).{" "}
          {productionComparable
            ? "Production comparable is YES."
            : "This is a research run: production comparable is NO."}
        </p>
      ) : (
        <p className="mt-1 text-[11px] text-terminal-muted">
          {productionComparable
            ? "Production comparable is YES."
            : "This is a research run: production comparable is NO."}
        </p>
      )}
      {row.combination_id === "CANDLE12_V2" ? (
        <ul
          data-testid="candle12-v2-warnings"
          className="mt-2 list-disc rounded border border-amber-500/40 bg-amber-500/10 py-1 pl-6 pr-3 text-[11px] text-amber-100"
        >
          {CANDLE12_V2_WARNINGS.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Known CANDLE12_V2 reporting defects. V2 is frozen, so they are flagged, not fixed. */
const CANDLE12_V2_WARNINGS = [
  "Avg R gross is net R.",
  "market_context uses Candle-2 close.",
  "HTF label is not applicable to CANDLE12_V2.",
] as const;

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
