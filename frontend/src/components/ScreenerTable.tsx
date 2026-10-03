import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMarketStore } from "../store/marketStore";
import { FreshCell } from "./FreshCell";
import { Spinner } from "./Spinner";
import type { FreshValue, ScreenerRow } from "../types/market";
import {
  COL_WIDTHS,
  POTENTIAL_LEVELS_TOOLTIP,
  SCREEN_STATE_LEGEND_SHORT,
  TECHNICAL_CORE_IDS,
  TECHNICAL_MARKET_IDS,
  TECHNICAL_PINNED_IDS,
  TRADE_COL_IDS,
  V1_DIFFERS_HELP,
  dependencyTooltip,
  displayV1Status,
  formatCompactNumber,
  formatFundingCell,
  formatMarketSignalLabel,
  formatPctCell,
  formatPotentialLevel,
  formatPriceCell,
  formatSetupStateLabel,
  formatTechRatingDisplay,
  isMissingLevel,
  liquidationTooltip,
  matchesSetupFilter,
  matchesSignalFilter,
  potentialLevelsForRow,
  shouldResetTableScroll,
  stickyOffsets,
  tradeStatusLabel,
  v1StatusClass,
  type SetupFilter,
  type SignalFilter,
} from "../utils/screenerPresentation";
import { V1PaperOpenHint, V1WatcherPanel } from "./V1WatcherPanel";

type Col = {
  id: string;
  label: string;
  width: number;
  align?: "left" | "right";
  pinned?: boolean;
  stickyLeft?: number;
  render: (row: ScreenerRow) => React.ReactNode;
  /** Prefer numeric whitespace-nowrap (no ellipsis) */
  numeric?: boolean;
};

function LevelCell({
  row,
  kind,
}: {
  row: ScreenerRow;
  kind: "entry" | "stop" | "tp1" | "rr";
}) {
  const levels = potentialLevelsForRow(row);
  const value =
    kind === "entry"
      ? levels.entry
      : kind === "stop"
        ? levels.stop
        : kind === "tp1"
          ? levels.tp1
          : levels.rr;
  const entryMissing = levels.entry == null;
  const { text, muted, title } = formatPotentialLevel(value, {
    kind,
    referenceOnly: levels.referenceOnly,
    entryMissing,
  });
  return (
    <span
      className={`font-mono text-sm ${muted ? "text-terminal-muted/70" : "text-terminal-text"}`}
      title={title}
      data-testid={`potential-${kind}`}
    >
      {text}
    </span>
  );
}

function V1StatusCell({ row }: { row: ScreenerRow }) {
  const { text, title, tone } = displayV1Status(row);
  return (
    <div className="min-w-0">
      <span
        className={`block truncate text-[11px] font-medium ${v1StatusClass(tone)}`}
        title={title}
        data-testid={`v1-status-${row.symbol}`}
      >
        {text}
      </span>
      <V1PaperOpenHint row={row} />
    </div>
  );
}

function SetupStateCell({ fv }: { fv: FreshValue | undefined }) {
  const raw = fv?.value;
  if (raw === null || raw === undefined) {
    return <FreshCell fv={fv} compact />;
  }
  const label = formatSetupStateLabel(raw);
  const color = label.includes("LONG")
    ? "text-terminal-up"
    : label.includes("SHORT")
      ? "text-terminal-down"
      : label === "CONFLICT" || label === "INVALIDATED"
        ? "text-amber-300"
        : label === "ENTRY READY"
          ? "text-terminal-up"
          : "";
  return (
    <FreshCell
      fv={fv ? { ...fv, value: label } : fv}
      className={color}
      compact
    />
  );
}

function SignalCell({ fv }: { fv: FreshValue | undefined }) {
  const v = String(fv?.value ?? "");
  const color =
    v.includes("BUY")
      ? "text-terminal-up"
      : v.includes("SELL")
        ? "text-terminal-down"
        : v === "NEUTRAL"
          ? "text-terminal-muted"
          : "";
  const label = formatMarketSignalLabel(v);
  return (
    <FreshCell
      fv={fv ? { ...fv, value: label || fv.value } : fv}
      className={color}
      compact
    />
  );
}

function buildCol(
  id: string,
  label: string,
  render: Col["render"],
  opts?: Partial<Pick<Col, "align" | "pinned" | "stickyLeft" | "numeric">>,
): Col {
  return {
    id,
    label,
    width: COL_WIDTHS[id] ?? 100,
    align: opts?.align ?? "left",
    pinned: opts?.pinned,
    stickyLeft: opts?.stickyLeft,
    numeric: opts?.numeric,
    render,
  };
}

function futuresTradeColumns(): Col[] {
  const defs: Record<string, Col> = {
    symbol: buildCol(
      "symbol",
      "Symbol",
      (r) => (
        <div className="min-w-0 overflow-hidden">
          <div className="truncate font-semibold tracking-wide">{r.base_asset}</div>
          <div className="truncate font-mono text-[10px] text-terminal-muted">{r.symbol}</div>
        </div>
      ),
      { align: "left" },
    ),
    price: buildCol(
      "price",
      "Price",
      (r) => <FreshCell fv={r.price} format={formatPriceCell} showBadge compact />,
      { align: "right", numeric: true },
    ),
    change: buildCol(
      "change",
      "24H",
      (r) => {
        const n = r.change_24h_pct?.value;
        const color =
          typeof n === "number" ? (n >= 0 ? "text-terminal-up" : "text-terminal-down") : "";
        return <FreshCell fv={r.change_24h_pct} format={formatPctCell} className={color} compact />;
      },
      { align: "right", numeric: true },
    ),
    market_signal: buildCol(
      "market_signal",
      "Screen Signal",
      (r) => <SignalCell fv={r.market_signal} />,
    ),
    trend: buildCol(
      "trend",
      "Local Trend",
      (r) => <FreshCell fv={r.setup_trend ?? r.market_structure ?? r.structure} compact />,
    ),
    setup_signal: buildCol(
      "setup_signal",
      "Screen Setup",
      (r) => <SetupStateCell fv={r.setup_signal} />,
    ),
    v1_status: buildCol("v1_status", "V1 Status", (r) => <V1StatusCell row={r} />),
    setup_entry: buildCol(
      "setup_entry",
      "Potential Entry",
      (r) => <LevelCell row={r} kind="entry" />,
      { align: "right", numeric: true },
    ),
    setup_sl: buildCol(
      "setup_sl",
      "Potential SL",
      (r) => <LevelCell row={r} kind="stop" />,
      { align: "right", numeric: true },
    ),
    setup_tp1: buildCol(
      "setup_tp1",
      "Potential TP1",
      (r) => <LevelCell row={r} kind="tp1" />,
      { align: "right", numeric: true },
    ),
    setup_rr: buildCol(
      "setup_rr",
      "Potential R:R",
      (r) => <LevelCell row={r} kind="rr" />,
      { align: "right", numeric: true },
    ),
    status: buildCol(
      "status",
      "Screen Status",
      (r) => {
        const { text, title } = tradeStatusLabel(r);
        return (
          <span className="block truncate text-xs text-terminal-muted" title={title}>
            {text}
          </span>
        );
      },
    ),
  };

  return TRADE_COL_IDS.map((id) => defs[id]).filter(Boolean);
}

function futuresTechnicalColumns(): Col[] {
  const pinLeft = stickyOffsets(TECHNICAL_PINNED_IDS);

  const catalog: Record<string, Col> = {
    symbol: buildCol(
      "symbol",
      "Symbol",
      (r) => (
        <div className="min-w-0 overflow-hidden">
          <div className="truncate font-semibold">{r.base_asset}</div>
          <div className="truncate font-mono text-[10px] text-terminal-muted">{r.symbol}</div>
        </div>
      ),
      { pinned: true, stickyLeft: pinLeft.symbol },
    ),
    price: buildCol(
      "price",
      "Price",
      (r) => <FreshCell fv={r.price} format={formatPriceCell} showBadge compact />,
      { align: "right", numeric: true, pinned: true, stickyLeft: pinLeft.price },
    ),
    market_signal: buildCol(
      "market_signal",
      "Screen Signal",
      (r) => <SignalCell fv={r.market_signal} />,
      { pinned: true, stickyLeft: pinLeft.market_signal },
    ),
    setup_signal: buildCol(
      "setup_signal",
      "Screen Setup",
      (r) => <SetupStateCell fv={r.setup_signal} />,
      { pinned: true, stickyLeft: pinLeft.setup_signal },
    ),
    rating: buildCol("rating", "Tech Rating", (r) => {
      const { text, title } = formatTechRatingDisplay(r.tech_rating ?? r.technical_state);
      return (
        <span className="block truncate text-xs text-terminal-muted" title={title}>
          {text}
        </span>
      );
    }),
    trend: buildCol(
      "trend",
      "Local Trend",
      (r) => <FreshCell fv={r.setup_trend ?? r.market_structure ?? r.structure} compact />,
    ),
    setup_bos: buildCol(
      "setup_bos",
      "BOS",
      (r) => <FreshCell fv={r.setup_bos ?? r.bos} compact />,
    ),
    impulse: buildCol(
      "impulse",
      "Impulse",
      (r) => <FreshCell fv={r.setup_impulse} compact />,
    ),
    pullback: buildCol(
      "pullback",
      "Pullback",
      (r) => <FreshCell fv={r.setup_pullback} compact />,
    ),
    v1_status: buildCol("v1_status", "V1 Status", (r) => <V1StatusCell row={r} />),
    setup_entry: buildCol(
      "setup_entry",
      "Potential Entry",
      (r) => <LevelCell row={r} kind="entry" />,
      { align: "right", numeric: true },
    ),
    setup_sl: buildCol(
      "setup_sl",
      "Potential SL",
      (r) => <LevelCell row={r} kind="stop" />,
      { align: "right", numeric: true },
    ),
    setup_tp1: buildCol(
      "setup_tp1",
      "Potential TP1",
      (r) => <LevelCell row={r} kind="tp1" />,
      { align: "right", numeric: true },
    ),
    setup_rr: buildCol(
      "setup_rr",
      "Potential R:R",
      (r) => <LevelCell row={r} kind="rr" />,
      { align: "right", numeric: true },
    ),
    mcap: buildCol(
      "mcap",
      "Market Cap",
      (r) => <FreshCell fv={r.market_cap} format={formatCompactNumber} showBadge compact />,
      { align: "right", numeric: true },
    ),
    fdv: buildCol(
      "fdv",
      "FDV",
      (r) => <FreshCell fv={r.fdv} format={formatCompactNumber} compact />,
      { align: "right", numeric: true },
    ),
    vol: buildCol(
      "vol",
      "Volume",
      (r) => <FreshCell fv={r.quote_volume_24h} format={formatCompactNumber} compact />,
      { align: "right", numeric: true },
    ),
    oi: buildCol(
      "oi",
      "OI",
      (r) => <FreshCell fv={r.open_interest} format={formatCompactNumber} showBadge compact />,
      { align: "right", numeric: true },
    ),
    funding: buildCol(
      "funding",
      "Funding",
      (r) => <FreshCell fv={r.funding_rate} format={formatFundingCell} showBadge compact />,
      { align: "right", numeric: true },
    ),
    rvol: buildCol(
      "rvol",
      "RVOL",
      (r) => <FreshCell fv={r.relative_volume} compact />,
      { align: "right" },
    ),
    structure: buildCol(
      "structure",
      "Structure",
      (r) => <FreshCell fv={r.market_structure ?? r.structure} compact />,
    ),
    zone: buildCol("zone", "Zone", (r) => <FreshCell fv={r.zone} compact />),
    liq: buildCol("liq", "Liquidation", (r) => {
      const fv = r.liquidation;
      const st = String(fv?.status || "WAITING").toUpperCase();
      if (st === "WAITING" || st === "UNAVAILABLE" || fv?.value == null) {
        const label = st === "UNAVAILABLE" ? "N/A" : "WAITING";
        return (
          <span className="text-xs text-terminal-muted" title={liquidationTooltip(fv)}>
            {label}
          </span>
        );
      }
      return <FreshCell fv={fv} compact />;
    }),
    entry: buildCol("entry", "Entry/Exit", (r) => (
      <SetupStateCell fv={r.entry_exit_state ?? r.setup_signal} />
    )),
  };

  const ids = [
    ...TECHNICAL_PINNED_IDS,
    ...TECHNICAL_CORE_IDS,
    ...TECHNICAL_MARKET_IDS,
  ];
  return ids.map((id) => catalog[id]).filter(Boolean);
}

/* ── Other domains (unchanged intent; compact empty labels via FreshCell) ── */

const legacyBase: Col[] = [
  buildCol(
    "symbol",
    "Coin",
    (r) => (
      <div className="min-w-0 overflow-hidden">
        <div className="truncate font-semibold">{r.base_asset}</div>
        <div className="truncate font-mono text-[10px] text-terminal-muted">{r.symbol}</div>
      </div>
    ),
  ),
  buildCol(
    "price",
    "Price",
    (r) => <FreshCell fv={r.price} format={formatPriceCell} showBadge compact />,
    { align: "right", numeric: true },
  ),
  buildCol(
    "change",
    "24h %",
    (r) => {
      const n = r.change_24h_pct?.value;
      const color =
        typeof n === "number" ? (n >= 0 ? "text-terminal-up" : "text-terminal-down") : "";
      return <FreshCell fv={r.change_24h_pct} format={formatPctCell} className={color} compact />;
    },
    { align: "right", numeric: true },
  ),
];

function columnsForOtherDomain(domain: string): Col[] {
  const pick = (ids: string[], builders: Record<string, Col>) =>
    ids.map((id) => builders[id]).filter(Boolean);

  const map: Record<string, Col> = {
    mcap: buildCol(
      "mcap",
      "Market Cap",
      (r) => <FreshCell fv={r.market_cap} format={formatCompactNumber} compact />,
      { align: "right", numeric: true },
    ),
    fdv: buildCol(
      "fdv",
      "FDV",
      (r) => <FreshCell fv={r.fdv} format={formatCompactNumber} compact />,
      { align: "right", numeric: true },
    ),
    vol: buildCol(
      "vol",
      "Volume",
      (r) => <FreshCell fv={r.quote_volume_24h} format={formatCompactNumber} compact />,
      { align: "right", numeric: true },
    ),
    oi: buildCol(
      "oi",
      "OI",
      (r) => <FreshCell fv={r.open_interest} format={formatCompactNumber} showBadge compact />,
      { align: "right", numeric: true },
    ),
    funding: buildCol(
      "funding",
      "Funding",
      (r) => <FreshCell fv={r.funding_rate} format={formatFundingCell} compact />,
      { align: "right", numeric: true },
    ),
    rvol: buildCol("rvol", "RVOL", (r) => <FreshCell fv={r.relative_volume} compact />),
    structure: buildCol(
      "structure",
      "Structure",
      (r) => <FreshCell fv={r.market_structure ?? r.structure} compact />,
    ),
    zone: buildCol("zone", "Zone", (r) => <FreshCell fv={r.zone} compact />),
    liq: buildCol("liq", "Liquidation", (r) => <FreshCell fv={r.liquidation} compact />),
    entry: buildCol("entry", "Entry/Exit", (r) => (
      <SetupStateCell fv={r.entry_exit_state ?? r.setup_signal} />
    )),
    rating: buildCol("rating", "Tech Rating", (r) => {
      const { text, title } = formatTechRatingDisplay(r.tech_rating ?? r.technical_state);
      return (
        <span className="block truncate text-xs text-terminal-muted" title={title}>
          {text}
        </span>
      );
    }),
    market_signal: buildCol("market_signal", "Signal", (r) => <SignalCell fv={r.market_signal} />),
    trend: buildCol(
      "trend",
      "Trend",
      (r) => <FreshCell fv={r.setup_trend ?? r.market_structure ?? r.structure} compact />,
    ),
    setup_bos: buildCol("setup_bos", "BOS", (r) => (
      <FreshCell fv={r.setup_bos ?? r.bos} compact />
    )),
    impulse: buildCol("impulse", "Impulse", (r) => <FreshCell fv={r.setup_impulse} compact />),
    pullback: buildCol("pullback", "Pullback", (r) => (
      <FreshCell fv={r.setup_pullback} compact />
    )),
    setup_signal: buildCol("setup_signal", "Setup", (r) => (
      <SetupStateCell fv={r.setup_signal} />
    )),
    choch: buildCol("choch", "CHOCH", (r) => <FreshCell fv={r.choch} compact />),
    vol_mcap: buildCol("vol_mcap", "Vol/MCap", (r) => <FreshCell fv={r.volume_mcap} compact />),
    tvl: buildCol(
      "tvl",
      "TVL",
      (r) => <FreshCell fv={r.tvl} format={formatCompactNumber} compact />,
      { align: "right" },
    ),
    nvt: buildCol("nvt", "NVT", (r) => <FreshCell fv={r.nvt} compact />),
    velocity: buildCol("velocity", "Velocity", (r) => <FreshCell fv={r.velocity} compact />),
    williams: buildCol("williams", "Williams %R", (r) => <FreshCell fv={r.williams_r} compact />),
    oi_chg: buildCol(
      "oi_chg",
      "OI Δ",
      (r) => <FreshCell fv={r.oi_change_pct ?? r.oi_change_24h} format={formatPctCell} compact />,
      { align: "right" },
    ),
    long_liq: buildCol(
      "long_liq",
      "Long Liq",
      (r) => <FreshCell fv={r.long_liquidations} format={formatCompactNumber} compact />,
      { align: "right" },
    ),
    short_liq: buildCol(
      "short_liq",
      "Short Liq",
      (r) => <FreshCell fv={r.short_liquidations} format={formatCompactNumber} compact />,
      { align: "right" },
    ),
    volatility: buildCol("volatility", "Volat.", (r) => <FreshCell fv={r.volatility} compact />),
    supply: buildCol("supply", "Supply Z", (r) => (
      <FreshCell fv={r.nearest_supply_zone} compact />
    )),
    demand: buildCol("demand", "Demand Z", (r) => (
      <FreshCell fv={r.nearest_demand_zone} compact />
    )),
  };

  switch (domain) {
    case "Fundamentals":
      return [
        ...legacyBase,
        ...pick(
          ["mcap", "fdv", "vol", "vol_mcap", "tvl", "nvt", "velocity", "williams", "rating"],
          map,
        ),
      ];
    case "Market Structure":
      return [
        ...legacyBase,
        ...pick(
          [
            "trend",
            "setup_bos",
            "choch",
            "impulse",
            "pullback",
            "market_signal",
            "setup_signal",
            "entry",
            "rating",
            "rvol",
          ],
          map,
        ),
      ];
    case "Supply/Demand":
      return [...legacyBase, ...pick(["entry", "zone", "supply", "demand", "rvol"], map)];
    case "Liquidations":
      return [...legacyBase, ...pick(["liq", "long_liq", "short_liq", "oi", "funding"], map)];
    case "Volume Analysis":
      return [...legacyBase, ...pick(["vol", "rvol", "vol_mcap", "volatility", "williams"], map)];
    case "Open Interest":
      return [...legacyBase, ...pick(["oi", "oi_chg", "funding", "vol", "rating"], map)];
    default:
      return legacyBase;
  }
}

export function ScreenerTable({ domain = "Futures" }: { domain?: string }) {
  const rows = useMarketStore((s) => s.rows);
  const ordered = useMarketStore((s) => s.orderedSymbols);
  const selected = useMarketStore((s) => s.selectedSymbol);
  const setSelected = useMarketStore((s) => s.setSelected);
  const domainLoading = useMarketStore((s) => s.domainLoading);
  const screenSize = useMarketStore((s) => s.screenSize);
  const setScreenSize = useMarketStore((s) => s.setScreenSize);
  const screenFilter = useMarketStore((s) => s.screenFilter);
  const setScreenFilter = useMarketStore((s) => s.setScreenFilter);
  const screenMeta = useMarketStore((s) => s.screenMeta);
  const scrollRef = useRef<HTMLDivElement>(null);
  const savedScrollLeft = useRef(0);
  const modeRef = useRef<"trade" | "technical">("trade");

  /** Default: Trade View. Checkbox enables Technical View. */
  const [technicalView, setTechnicalView] = useState(false);
  const [signalFilter, setSignalFilter] = useState<SignalFilter>("ALL");
  const [setupFilter, setSetupFilter] = useState<SetupFilter>("ALL");
  const [switching, setSwitching] = useState(false);
  const [showWhyExcluded, setShowWhyExcluded] = useState(false);
  const [showHowDiffers, setShowHowDiffers] = useState(false);
  const [showLegend, setShowLegend] = useState(false);

  const mode: "trade" | "technical" = technicalView ? "technical" : "trade";

  const columns = useMemo(() => {
    if (domain === "Futures") {
      return mode === "technical" ? futuresTechnicalColumns() : futuresTradeColumns();
    }
    return columnsForOtherDomain(domain);
  }, [domain, mode]);

  useEffect(() => {
    setSwitching(true);
    const id = window.setTimeout(() => setSwitching(false), 180);
    return () => window.clearTimeout(id);
  }, [domain]);

  // Reset horizontal scroll on domain change or Trade ↔ Technical mode switch
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const reset =
      domain !== "Futures" || shouldResetTableScroll(modeRef.current, mode) || modeRef.current !== mode;
    if (reset) {
      el.scrollLeft = 0;
      savedScrollLeft.current = 0;
      const id = requestAnimationFrame(() => {
        if (scrollRef.current) scrollRef.current.scrollLeft = 0;
      });
      modeRef.current = mode;
      return () => cancelAnimationFrame(id);
    }
    modeRef.current = mode;
  }, [domain, mode]);

  // Preserve manual horizontal scroll across live row updates
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      savedScrollLeft.current = el.scrollLeft;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const loading = domainLoading || switching;

  const data = useMemo(() => {
    const all = ordered
      .map((sym, i) => ({ ...rows[sym], rank: rows[sym]?.rank ?? i + 1 }))
      .filter(Boolean) as ScreenerRow[];
    if (domain !== "Futures") return all;
    return all.filter(
      (r) => matchesSignalFilter(r, signalFilter) && matchesSetupFilter(r, setupFilter),
    );
  }, [ordered, rows, domain, signalFilter, setupFilter]);

  const headerHeight = 32;
  const virtualizer = useVirtualizer({
    count: data.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 44,
    overscan: 12,
    paddingStart: headerHeight,
  });

  const totalWidth = columns.reduce((a, c) => a + c.width, 0);

  // After virtualizer remeasure / data updates, restore scrollLeft if user scrolled
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || domain !== "Futures") return;
    if (mode === "technical" && savedScrollLeft.current > 0) {
      el.scrollLeft = savedScrollLeft.current;
    }
  }, [data.length, domain, mode]);

  const universe = screenMeta?.total_universe ?? 0;
  const shown = screenMeta?.returned_count ?? data.length;
  const eligible = screenMeta?.eligible_count ?? shown;
  const excluded = screenMeta?.excluded || {};
  const screenTf =
    screenMeta?.screen_timeframe ||
    screenMeta?.screener_identity?.screen_setup_timeframe ||
    "15m";
  const identity = screenMeta?.screener_identity;

  return (
    <div className="table-wrapper relative min-w-0 min-h-0">
      {loading ? (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-terminal-bg/55 backdrop-blur-[1px]">
          <Spinner label={`Loading ${domain}…`} />
        </div>
      ) : null}

      {domain === "Futures" && (
        <>
          <div
            className="border-b border-amber-500/30 bg-amber-500/5 px-2 py-2"
            data-testid="general-screener-banner"
          >
            <div className="font-mono text-[12px] font-semibold tracking-wide text-amber-100">
              {identity?.title || "GENERAL MARKET SCREENER"}
            </div>
            <p className="mt-0.5 text-[11px] text-terminal-text">
              {identity?.subtitle ||
                "Structure and setup discovery only. Not a COMBO_02 v1 trade signal."}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-terminal-muted">
              <span>
                Screen setup timeframe:{" "}
                <span className="font-mono text-terminal-text">{screenTf}</span>
              </span>
              <span>
                COMBO_02 v1 execution:{" "}
                <span className="text-terminal-text">
                  {identity?.v1_execution ||
                    "1h setup + 4h/1h HTF alignment + confirmed BOS"}
                </span>
              </span>
              <button
                type="button"
                className="rounded border border-terminal-border px-1.5 py-0.5 hover:text-terminal-text"
                onClick={() => setShowHowDiffers((v) => !v)}
                data-testid="how-differs-from-v1"
                title={V1_DIFFERS_HELP}
              >
                How this differs from v1
              </button>
            </div>
            {showHowDiffers ? (
              <pre
                className="mt-1 whitespace-pre-wrap rounded border border-terminal-border/60 bg-black/30 p-2 font-mono text-[10px] text-terminal-muted"
                data-testid="how-differs-body"
              >
                {V1_DIFFERS_HELP}
              </pre>
            ) : null}
          </div>
          <V1WatcherPanel rows={screenMeta?.v1_watcher_view} />
          <div className="flex flex-col gap-1 border-b border-terminal-border/60 px-2 py-1.5 text-[10px] text-terminal-muted">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <div className="font-mono text-[11px] text-terminal-text" data-testid="screen-universe-label">
              <span className="text-terminal-muted">SCREEN</span>{" "}
              <span className="text-terminal-accent">{shown}</span>
              {" / "}
              {universe || "—"} symbols
              {screenMeta?.search_mode ? (
                <span className="ml-2 text-amber-300">search result</span>
              ) : (
                <span className="ml-2 text-terminal-muted">
                  {shown} of {eligible} eligible · SCREEN TOP 100
                </span>
              )}
            </div>
            <label className="inline-flex items-center gap-1">
              Screen size
              <select
                className="rounded border border-terminal-border bg-terminal-panel px-1 py-0.5 text-[10px] text-terminal-text"
                value={screenSize}
                onChange={(e) => setScreenSize(Number(e.target.value) as 25 | 50 | 100)}
                data-testid="screen-size"
              >
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
            </label>
            <label className="inline-flex items-center gap-1">
              Screen filter
              <select
                className="rounded border border-terminal-border bg-terminal-panel px-1 py-0.5 text-[10px] text-terminal-text"
                value={screenFilter}
                onChange={(e) => setScreenFilter(e.target.value as typeof screenFilter)}
                data-testid="screen-filter"
              >
                <option value="ALL_ELIGIBLE">ALL ELIGIBLE</option>
                <option value="SETUPS">SETUPS</option>
                <option value="ENTRY_READY">ENTRY READY</option>
                <option value="BUY_BIAS">BUY BIAS</option>
                <option value="SELL_BIAS">SELL BIAS</option>
                <option value="WAITING">WAITING</option>
                <option value="CONFLICT">CONFLICT</option>
              </select>
            </label>
            <button
              type="button"
              className="rounded border border-terminal-border px-1.5 py-0.5 text-[10px] hover:text-terminal-text"
              onClick={() => setShowWhyExcluded((v) => !v)}
              data-testid="why-not-top-100"
            >
              Why not in Top 100?
            </button>
            <button
              type="button"
              className="rounded border border-terminal-border px-1.5 py-0.5 text-[10px] hover:text-terminal-text"
              onClick={() => setShowLegend((v) => !v)}
              data-testid="screen-state-legend"
              title={POTENTIAL_LEVELS_TOOLTIP}
            >
              Screen state legend
            </button>
          </div>
          {showLegend ? (
            <div
              className="grid gap-1 font-mono text-[10px] text-terminal-muted sm:grid-cols-2"
              data-testid="screen-state-legend-body"
            >
              {SCREEN_STATE_LEGEND_SHORT.map((item) => (
                <div key={item.state}>
                  <span className="text-terminal-text">{item.state}</span>
                  {" — "}
                  {item.meaning}
                </div>
              ))}
              <div className="sm:col-span-2 text-[9px] opacity-80" title={POTENTIAL_LEVELS_TOOLTIP}>
                Potential Entry/SL/TP1/R:R are reference levels only — not orders, not paper, not Telegram.
              </div>
            </div>
          ) : (
            <div className="text-[9px] text-terminal-muted/80">
              NEUTRAL · WAITING · CONFLICT · DISCOVERY ONLY are screen states — not v1 trade signals.
            </div>
          )}
          {showWhyExcluded ? (
            <div className="font-mono text-[10px] text-terminal-muted" data-testid="exclusion-diagnostics">
              Excluded — missing price: {excluded.excluded_missing_price ?? 0} · stale:{" "}
              {excluded.excluded_stale ?? 0} · unavailable: {excluded.excluded_unavailable ?? 0} ·
              missing OHLCV: {excluded.excluded_missing_ohlcv ?? 0} · screen filter:{" "}
              {excluded.excluded_screen_filter ?? 0}
              <span className="ml-2 opacity-70">
                (backend still monitors full universe; WATCHLIST is separate when enabled)
              </span>
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <label
            className="inline-flex cursor-pointer items-center gap-1.5"
            title="Show detailed structure/setup columns."
          >
            <input
              type="checkbox"
              checked={technicalView}
              onChange={(e) => {
                setTechnicalView(e.target.checked);
                savedScrollLeft.current = 0;
              }}
              className="accent-terminal-accent"
              data-testid="technical-view-toggle"
            />
            Technical columns
          </label>
          <span className="opacity-70">
            {technicalView ? "Technical view — why this state" : "Trade view — what to investigate"}
          </span>
          <label className="inline-flex items-center gap-1">
            Screen Signal
            <select
              className="rounded border border-terminal-border bg-terminal-panel px-1 py-0.5 text-[10px] text-terminal-text"
              value={signalFilter}
              onChange={(e) => setSignalFilter(e.target.value as SignalFilter)}
              data-testid="signal-filter"
            >
              <option value="ALL">ALL</option>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
              <option value="NEUTRAL">NEUTRAL</option>
              <option value="WAITING">WAITING</option>
            </select>
          </label>
          <label className="inline-flex items-center gap-1">
            Screen Setup
            <select
              className="rounded border border-terminal-border bg-terminal-panel px-1 py-0.5 text-[10px] text-terminal-text"
              value={setupFilter}
              onChange={(e) => setSetupFilter(e.target.value as SetupFilter)}
              data-testid="setup-filter"
            >
              <option value="ALL">ALL</option>
              <option value="ENTRY_READY">ENTRY READY</option>
              <option value="LONG_ENTRY_CANDIDATE">LONG CANDIDATE</option>
              <option value="SHORT_ENTRY_CANDIDATE">SHORT CANDIDATE</option>
              <option value="NO_SETUP">NO SETUP</option>
              <option value="CONFLICT">CONFLICT</option>
              <option value="WAITING">WAITING</option>
            </select>
          </label>
          </div>
        </div>
        </>
      )}

      <div ref={scrollRef} className="table-scroll min-w-0 min-h-0" data-testid="screener-table-scroll">
        {data.length === 0 && !loading ? (
          <div className="flex h-full min-h-[10rem] items-center justify-center text-sm text-terminal-muted">
            Waiting for live data...
          </div>
        ) : data.length === 0 ? (
          <div className="h-full min-h-[10rem]" />
        ) : (
          <div
            className="relative"
            style={{ height: virtualizer.getTotalSize(), width: totalWidth }}
            data-testid="screener-table-body"
            data-mode={domain === "Futures" ? mode : "domain"}
          >
            <div
              className="sticky top-0 z-10 flex border-b border-terminal-border bg-terminal-panel text-[11px] uppercase tracking-wide text-terminal-muted"
              style={{ width: totalWidth, height: headerHeight }}
            >
              {columns.map((c) => (
                <div
                  key={c.id}
                  className={`shrink-0 px-2 py-2 ${
                    c.align === "right" ? "text-right" : "text-left"
                  } ${c.pinned ? "table-sticky-cell table-sticky-cell--header" : "truncate"}`}
                  style={{
                    width: c.width,
                    ...(c.pinned && c.stickyLeft != null
                      ? { left: c.stickyLeft }
                      : {}),
                  }}
                  title={c.label}
                  data-pinned={c.pinned ? "true" : undefined}
                  data-col={c.id}
                >
                  {c.label}
                </div>
              ))}
            </div>
            {virtualizer.getVirtualItems().map((vi) => {
              const row = data[vi.index];
              if (!row) return null;
              const active = selected === row.symbol;
              return (
                <button
                  type="button"
                  key={row.symbol}
                  onClick={() => setSelected(row.symbol)}
                  data-testid={`screener-row-${row.symbol}`}
                  title={
                    row.screen_priority_reason
                      ? `Screen priority: ${row.screen_priority_reason}`
                      : undefined
                  }
                  className={`screener-row absolute left-0 flex items-center border-b border-terminal-border/60 text-left transition hover:bg-white/[0.03] ${
                    active ? "screener-row--active bg-terminal-accent/10" : ""
                  }`}
                  style={{
                    height: vi.size,
                    transform: `translateY(${vi.start}px)`,
                    width: totalWidth,
                  }}
                >
                  {columns.map((c) => (
                    <div
                      key={c.id}
                      className={`min-w-0 shrink-0 px-2 ${
                        c.align === "right" ? "text-right" : "text-left"
                      } ${c.pinned ? "table-sticky-cell" : "overflow-hidden"} ${
                        c.numeric ? "table-cell-numeric" : ""
                      }`}
                      style={{
                        width: c.width,
                        ...(c.pinned && c.stickyLeft != null
                          ? { left: c.stickyLeft }
                          : {}),
                      }}
                      data-pinned={c.pinned ? "true" : undefined}
                      data-col={c.id}
                    >
                      {c.render(row)}
                    </div>
                  ))}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
