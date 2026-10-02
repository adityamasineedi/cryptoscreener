import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMarketStore } from "../store/marketStore";
import { FreshCell } from "./FreshCell";
import { Spinner } from "./Spinner";
import type { FreshValue, ScreenerRow } from "../types/market";
import {
  COL_WIDTHS,
  TECHNICAL_CORE_IDS,
  TECHNICAL_MARKET_IDS,
  TECHNICAL_PINNED_IDS,
  TRADE_COL_IDS,
  dependencyTooltip,
  formatCompactNumber,
  formatFundingCell,
  formatMarketSignalLabel,
  formatPctCell,
  formatPriceCell,
  formatSetupStateLabel,
  formatTechRatingDisplay,
  isMissingLevel,
  liquidationTooltip,
  matchesSetupFilter,
  matchesSignalFilter,
  shouldResetTableScroll,
  stickyOffsets,
  tradeStatusLabel,
  type SetupFilter,
  type SignalFilter,
} from "../utils/screenerPresentation";

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

function LevelCell({ fv }: { fv: FreshValue | undefined }) {
  if (isMissingLevel(fv)) {
    return (
      <span className="font-mono text-sm text-terminal-muted" title={dependencyTooltip(fv)}>
        —
      </span>
    );
  }
  return <FreshCell fv={fv} format={formatPriceCell} compact />;
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
    market_signal: buildCol("market_signal", "Signal", (r) => <SignalCell fv={r.market_signal} />),
    trend: buildCol(
      "trend",
      "Trend",
      (r) => <FreshCell fv={r.setup_trend ?? r.market_structure ?? r.structure} compact />,
    ),
    setup_signal: buildCol("setup_signal", "Setup", (r) => <SetupStateCell fv={r.setup_signal} />),
    setup_entry: buildCol(
      "setup_entry",
      "Entry",
      (r) => <LevelCell fv={r.setup_entry} />,
      { align: "right", numeric: true },
    ),
    setup_sl: buildCol(
      "setup_sl",
      "SL",
      (r) => <LevelCell fv={r.setup_sl} />,
      { align: "right", numeric: true },
    ),
    setup_tp1: buildCol(
      "setup_tp1",
      "TP1",
      (r) => <LevelCell fv={r.setup_tp1} />,
      { align: "right", numeric: true },
    ),
    setup_rr: buildCol(
      "setup_rr",
      "R:R",
      (r) => {
        if (isMissingLevel(r.setup_rr)) {
          return (
            <span className="font-mono text-sm text-terminal-muted" title={dependencyTooltip(r.setup_rr)}>
              —
            </span>
          );
        }
        return (
          <FreshCell fv={r.setup_rr} format={(v) => Number(v).toFixed(2)} compact />
        );
      },
      { align: "right", numeric: true },
    ),
    status: buildCol("status", "Status", (r) => {
      const { text, title } = tradeStatusLabel(r);
      return (
        <span className="block truncate text-xs text-terminal-muted" title={title}>
          {text}
        </span>
      );
    }),
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
      "Signal",
      (r) => <SignalCell fv={r.market_signal} />,
      { pinned: true, stickyLeft: pinLeft.market_signal },
    ),
    setup_signal: buildCol(
      "setup_signal",
      "Setup",
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
      "Trend",
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
    setup_entry: buildCol(
      "setup_entry",
      "Entry",
      (r) => <LevelCell fv={r.setup_entry} />,
      { align: "right", numeric: true },
    ),
    setup_sl: buildCol(
      "setup_sl",
      "SL",
      (r) => <LevelCell fv={r.setup_sl} />,
      { align: "right", numeric: true },
    ),
    setup_tp1: buildCol(
      "setup_tp1",
      "TP1",
      (r) => <LevelCell fv={r.setup_tp1} />,
      { align: "right", numeric: true },
    ),
    setup_rr: buildCol(
      "setup_rr",
      "R:R",
      (r) => {
        if (isMissingLevel(r.setup_rr)) {
          return (
            <span className="font-mono text-sm text-terminal-muted" title={dependencyTooltip(r.setup_rr)}>
              —
            </span>
          );
        }
        return <FreshCell fv={r.setup_rr} format={(v) => Number(v).toFixed(2)} compact />;
      },
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const savedScrollLeft = useRef(0);
  const modeRef = useRef<"trade" | "technical">("trade");

  /** Default: Trade View. Checkbox enables Technical View. */
  const [technicalView, setTechnicalView] = useState(false);
  const [signalFilter, setSignalFilter] = useState<SignalFilter>("ALL");
  const [setupFilter, setSetupFilter] = useState<SetupFilter>("ALL");
  const [switching, setSwitching] = useState(false);

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

  return (
    <div className="table-wrapper relative min-w-0 min-h-0">
      {loading ? (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-terminal-bg/55 backdrop-blur-[1px]">
          <Spinner label={`Loading ${domain}…`} />
        </div>
      ) : null}

      {domain === "Futures" && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-terminal-border/60 px-2 py-1 text-[10px] text-terminal-muted">
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
            Signal
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
            Setup
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
