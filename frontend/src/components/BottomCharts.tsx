import { useEffect, useRef, useState } from "react";
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type HistogramData,
  type IPriceLine,
  ColorType,
} from "lightweight-charts";
import {
  fetchLiquidations,
  fetchOhlcv,
  fetchSetupAnnotations,
} from "../api/client";
import { useMarketStore } from "../store/marketStore";
import {
  CANDLE_SCALE_MARGINS,
  CHART_RIGHT_OFFSET_BARS,
  MAX_VISIBLE_STRUCTURE_LABELS,
  type AnnToggle,
  type ChartAnnotation,
  buildPriorityBands,
  computeChartPriceRange,
  dedupeAnnotations,
  filterAnnotationsByToggles,
  isLiquidationsCompact,
  liquidationsSubtitle,
  placePriceLines,
  placeStructureMarkers,
  sanitizeLiquidationEvents,
  thinCrowdedMarkers,
  tradePlanTitle,
  colorForKind,
} from "../chart/chartAnnotations";
import {
  isIntradayTimeframe,
  localizationForTimeframe,
  timeScaleOptionsForTimeframe,
} from "../chart/chartTimeAxis";

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;

export { MAX_VISIBLE_STRUCTURE_LABELS };

const chartBaseOptions = {
  layout: {
    background: { type: ColorType.Solid as const, color: "transparent" },
    textColor: "#8b95a8",
  },
  grid: {
    vertLines: { color: "rgba(255,255,255,0.04)" },
    horzLines: { color: "rgba(255,255,255,0.04)" },
  },
  rightPriceScale: {
    borderVisible: false,
    autoScale: true,
    scaleMargins: { top: 0.05, bottom: 0.14 },
  },
  timeScale: {
    borderVisible: false,
    rightOffset: CHART_RIGHT_OFFSET_BARS,
    shiftVisibleRangeOnNewBar: false,
    timeVisible: true,
    secondsVisible: false,
  },
};

type ChartSizeMode = "default" | "expanded" | "collapsed";

const CHART_SIZE_KEY = "cs.charts.size";

export function BottomCharts({
  variant = "docked",
  showSizeToggle = true,
}: {
  variant?: "docked" | "page";
  showSizeToggle?: boolean;
}) {
  const selected = useMarketStore((s) => s.selectedSymbol);
  const setSelected = useMarketStore((s) => s.setSelected);
  const orderedSymbols = useMarketStore((s) => s.orderedSymbols);
  const rows = useMarketStore((s) => s.rows);
  const [tf, setTf] = useState<(typeof TIMEFRAMES)[number]>("15m");
  const [symbolQuery, setSymbolQuery] = useState("");
  const [sizeMode, setSizeMode] = useState<ChartSizeMode>(() => {
    try {
      const v = localStorage.getItem(CHART_SIZE_KEY);
      if (v === "expanded" || v === "collapsed" || v === "default") return v;
    } catch {
      /* ignore */
    }
    return "default";
  });
  const [ann, setAnn] = useState<AnnToggle>({
    structure: true,
    bosChoch: true,
    setup: false,
    tradePlan: true,
    signal: true,
  });
  const [autoFollow, setAutoFollow] = useState(false);
  const [liqCompact, setLiqCompact] = useState(true);

  function toggle(key: keyof AnnToggle) {
    setAnn((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function cycleSize() {
    setSizeMode((prev) => {
      const next =
        prev === "default" ? "expanded" : prev === "expanded" ? "collapsed" : "default";
      try {
        localStorage.setItem(CHART_SIZE_KEY, next);
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  const sizeClass =
    variant === "page"
      ? "charts-section--page"
      : sizeMode === "expanded"
        ? "charts-section--expanded"
        : sizeMode === "collapsed"
          ? "charts-section--collapsed"
          : "";

  const suggestions = (() => {
    const q = symbolQuery.trim().toUpperCase();
    if (!q) return orderedSymbols.slice(0, 12);
    return orderedSymbols
      .filter((s) => s.includes(q) || (rows[s]?.base_asset || "").toUpperCase().includes(q))
      .slice(0, 12);
  })();

  function applySymbol(raw: string) {
    const sym = raw.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!sym) return;
    const normalized = sym.endsWith("USDT") ? sym : `${sym}USDT`;
    setSelected(normalized);
    setSymbolQuery(normalized);
  }

  return (
    <div
      className={`charts-section border-t border-terminal-border bg-terminal-panel/80 ${sizeClass}`}
    >
      <div className="chart-toolbar border-b border-terminal-border px-3 py-1.5">
        <span className="min-w-0 truncate text-[11px] uppercase tracking-wide text-terminal-muted">
          Charts {selected ? `· ${selected}` : ""}
        </span>
        {variant === "page" ? (
          <div className="relative min-w-[10rem] max-w-[14rem] flex-1">
            <input
              value={symbolQuery || selected || ""}
              onChange={(e) => setSymbolQuery(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === "Enter") applySymbol(symbolQuery || selected || "");
              }}
              placeholder="Symbol e.g. BTC"
              className="w-full rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono text-[11px] text-terminal-text outline-none focus:border-terminal-accent"
            />
            {symbolQuery && suggestions.length > 0 ? (
              <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-48 overflow-auto rounded border border-terminal-border bg-terminal-panel shadow-lg">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="block w-full px-2 py-1 text-left font-mono text-[11px] text-terminal-text hover:bg-white/5"
                    onClick={() => applySymbol(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
        <div className="chart-timeframes">
          {TIMEFRAMES.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTf(t)}
              className={`shrink-0 rounded px-2 py-0.5 text-[11px] ${
                tf === t ? "bg-white/10 text-terminal-text" : "text-terminal-muted"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-[10px] text-terminal-muted">
          {(
            [
              ["structure", "Structure"],
              ["bosChoch", "BOS/CHOCH"],
              ["setup", "Setup"],
              ["tradePlan", "Trade Plan"],
              ["signal", "Signal"],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="inline-flex cursor-pointer items-center gap-1">
              <input
                type="checkbox"
                checked={ann[key]}
                onChange={() => toggle(key)}
                className="accent-terminal-accent"
              />
              {label}
            </label>
          ))}
          <label className="inline-flex cursor-pointer items-center gap-1" title="When on, chart follows new candles">
            <input
              type="checkbox"
              checked={autoFollow}
              onChange={() => setAutoFollow((v) => !v)}
              className="accent-terminal-accent"
            />
            Follow
          </label>
          {variant === "docked" && showSizeToggle ? (
            <button
              type="button"
              onClick={cycleSize}
              className="rounded border border-terminal-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-terminal-text hover:bg-white/5"
              title="Cycle chart size: default → expanded → collapsed"
            >
              {sizeMode === "expanded"
                ? "Shrink"
                : sizeMode === "collapsed"
                  ? "Show"
                  : "Expand"}
            </button>
          ) : null}
        </div>
      </div>
      <div className={`chart-grid${liqCompact ? " chart-grid--liq-compact" : ""}`}>
        <PriceVolumeChart
          symbol={selected}
          timeframe={tf}
          ann={ann}
          autoFollow={autoFollow}
        />
        <LiqChart symbol={selected} onCompactChange={setLiqCompact} />
      </div>
    </div>
  );
}

function ChartEmpty({
  title,
  subtitle,
  compact = false,
}: {
  title: string;
  subtitle: string;
  compact?: boolean;
}) {
  return (
    <div className={`chart-empty${compact ? " chart-empty--compact" : ""}`}>
      <div className="text-[11px] font-medium uppercase tracking-wide text-terminal-muted">
        {title}
      </div>
      <div className="text-xs text-terminal-muted/80">{subtitle}</div>
    </div>
  );
}

function ChartShell({
  title,
  status,
  children,
  showHeader = true,
  compact = false,
}: {
  title: string;
  status: string;
  children: React.ReactNode;
  showHeader?: boolean;
  compact?: boolean;
}) {
  return (
    <div className={`chart-panel${compact ? " chart-panel--compact" : ""}`}>
      {showHeader ? (
        <div className="pointer-events-none absolute left-2 top-1 z-10 flex max-w-[calc(100%-1rem)] items-center gap-2 text-[10px] uppercase tracking-wide text-terminal-muted">
          <span className="truncate">{title}</span>
          <span className="shrink-0 opacity-70">{status}</span>
        </div>
      ) : null}
      {children}
    </div>
  );
}

function useChartHost(active: boolean) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!active) {
      chartRef.current = null;
      return;
    }
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      ...chartBaseOptions,
      width: el.clientWidth || el.parentElement?.clientWidth || 640,
      height: el.clientHeight || el.parentElement?.clientHeight || 280,
    });
    chartRef.current = chart;

    const ro = new ResizeObserver(() => {
      if (!containerRef.current || !chartRef.current) return;
      chartRef.current.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [active]);

  return { containerRef, chartRef };
}

function PriceVolumeChart({
  symbol,
  timeframe,
  ann,
  autoFollow,
}: {
  symbol: string | null;
  timeframe: string;
  ann: AnnToggle;
  autoFollow: boolean;
}) {
  const [status, setStatus] = useState("WAITING");
  const [candles, setCandles] = useState<CandlestickData[]>([]);
  const [volumes, setVolumes] = useState<HistogramData[]>([]);
  const [annotations, setAnnotations] = useState<ChartAnnotation[]>([]);
  const [tradeLevels, setTradeLevels] = useState<
    Array<{ kind: string; title: string; price: number; color: string }>
  >([]);
  const hasData = candles.length > 0;
  const { containerRef, chartRef } = useChartHost(hasData);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const fitKeyRef = useRef<string>("");
  const priceRangeRef = useRef<{ min: number; max: number } | null>(null);
  const candlesRef = useRef(candles);
  const annotationsRef = useRef(annotations);
  const annRef = useRef(ann);
  candlesRef.current = candles;
  annotationsRef.current = annotations;
  annRef.current = ann;

  const candleLikes = candles.map((c) => ({
    time: Number(c.time),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }));

  function applyPriceScale() {
    const series = candleRef.current;
    const chart = chartRef.current;
    const cdata = candlesRef.current;
    if (!series || !chart || cdata.length === 0) return;

    const likes = cdata.map((c) => ({
      time: Number(c.time),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    let visible = likes;
    const logical = chart.timeScale().getVisibleLogicalRange();
    if (logical) {
      const from = Math.max(0, Math.floor(logical.from));
      const to = Math.min(likes.length - 1, Math.ceil(logical.to));
      if (to >= from) {
        const slice = likes.slice(from, to + 1);
        if (slice.length > 0) visible = slice;
      }
    }

    const extras = annotationsRef.current
      .filter((a) => a.group === "trade_plan" && annRef.current.tradePlan)
      .map((a) => Number(a.price))
      .filter((p) => Number.isFinite(p));
    const range = computeChartPriceRange(visible, { extraPrices: extras });
    priceRangeRef.current = { min: range.chartMin, max: range.chartMax };
    series.applyOptions({
      autoscaleInfoProvider: () => {
        const r = priceRangeRef.current;
        if (!r) return null;
        return {
          priceRange: { minValue: r.min, maxValue: r.max },
        };
      },
    });
    series.priceScale().applyOptions({
      autoScale: true,
      scaleMargins: { ...CANDLE_SCALE_MARGINS },
    });
  }

  useEffect(() => {
    if (!hasData || !chartRef.current) return;
    const chart = chartRef.current;
    const extras = annotations
      .filter((a) => a.group === "trade_plan" && ann.tradePlan)
      .map((a) => Number(a.price))
      .filter((p) => Number.isFinite(p));
    const seed = computeChartPriceRange(
      candles.map((c) => ({
        time: Number(c.time),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
      { extraPrices: extras },
    );
    priceRangeRef.current = { min: seed.chartMin, max: seed.chartMax };

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#3dd68c",
      downColor: "#f07178",
      borderVisible: false,
      wickUpColor: "#3dd68c",
      wickDownColor: "#f07178",
      autoscaleInfoProvider: () => {
        const r = priceRangeRef.current;
        if (!r) return null;
        return {
          priceRange: { minValue: r.min, maxValue: r.max },
        };
      },
    });
    candleSeries.priceScale().applyOptions({
      autoScale: true,
      scaleMargins: { ...CANDLE_SCALE_MARGINS },
    });
    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    candleRef.current = candleSeries;
    volRef.current = volSeries;
    candleSeries.setData(candles);
    volSeries.setData(volumes);

    chart.applyOptions({
      localization: localizationForTimeframe(timeframe),
    });
    chart.timeScale().applyOptions({
      rightOffset: CHART_RIGHT_OFFSET_BARS,
      shiftVisibleRangeOnNewBar: false,
      ...timeScaleOptionsForTimeframe(timeframe),
    });

    chart.timeScale().fitContent();
    fitKeyRef.current = `${symbol}|${timeframe}`;

    const onVisibleRange = () => {
      // Recalculate Y range from currently visible candles (not full history)
      applyPriceScale();
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRange);
    applyPriceScale();

    return () => {
      try {
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRange);
      } catch {
        /* disposed */
      }
      candleRef.current = null;
      volRef.current = null;
      priceLinesRef.current = [];
    };
    // Chart instance is recreated when hasData toggles; seed with current series data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasData, chartRef]);

  useEffect(() => {
    if (!hasData || !chartRef.current) return;
    candleRef.current?.setData(candles);
    volRef.current?.setData(volumes);
    applyPriceScale();

    const key = `${symbol}|${timeframe}`;
    const isNewViewport = fitKeyRef.current !== key;
    chartRef.current.applyOptions({
      localization: localizationForTimeframe(timeframe),
    });
    chartRef.current.timeScale().applyOptions({
      rightOffset: CHART_RIGHT_OFFSET_BARS,
      // Only shift on new bars when the user explicitly enables Follow
      shiftVisibleRangeOnNewBar: autoFollow,
      ...timeScaleOptionsForTimeframe(timeframe),
    });

    // Fit once on symbol/timeframe change — never on routine live candle polls
    if (isNewViewport) {
      chartRef.current.timeScale().fitContent();
      fitKeyRef.current = key;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, volumes, hasData, chartRef, symbol, timeframe, autoFollow]);

  useEffect(() => {
    const series = candleRef.current;
    if (!series || !hasData || !symbol) return;

    for (const pl of priceLinesRef.current) {
      try {
        series.removePriceLine(pl);
      } catch {
        /* series may have been disposed */
      }
    }
    priceLinesRef.current = [];

    const deduped = dedupeAnnotations(annotations, { symbol, timeframe });
    const visible = filterAnnotationsByToggles(deduped, ann);
    const currentPrice =
      candleLikes.length > 0 ? candleLikes[candleLikes.length - 1].close : null;
    const bands = buildPriorityBands(
      visible.filter((a) => a.group === "trade_plan" || a.group === "signal"),
      currentPrice,
    );

    const span =
      candleLikes.length > 0
        ? Math.max(...candleLikes.map((c) => c.high)) -
          Math.min(...candleLikes.map((c) => c.low))
        : 0;
    const prox = Math.max(span * 0.012, 1e-8);

    const lines = placePriceLines(visible, ann, {
      suppressAxisNear: bands,
      proximity: prox,
    });

    const levels: Array<{ kind: string; title: string; price: number; color: string }> = [];
    for (const line of lines) {
      const pl = series.createPriceLine({
        price: line.price,
        color: line.color,
        lineWidth: line.lineWidth as 1 | 2 | 3 | 4,
        lineStyle: line.lineStyle,
        axisLabelVisible: line.axisLabelVisible,
        title: line.title,
      });
      priceLinesRef.current.push(pl);
      if (line.group === "trade_plan") {
        levels.push({
          kind: line.kind,
          title: line.title || tradePlanTitle({ kind: line.kind, group: "trade_plan" }),
          price: line.price,
          color: line.color,
        });
      }
    }

    const markers = thinCrowdedMarkers(
      placeStructureMarkers(visible, candleLikes, {
        maxVisible: MAX_VISIBLE_STRUCTURE_LABELS,
        priorityBands: bands,
      }),
      candleLikes,
    ).map((m) => ({
      time: m.time as CandlestickData["time"],
      position: m.position,
      color: m.color,
      shape: m.shape,
      text: m.text,
    }));

    series.setMarkers(markers);
    setTradeLevels(levels);
    applyPriceScale();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [annotations, ann, hasData, candles, symbol, timeframe]);

  useEffect(() => {
    if (!symbol) {
      setStatus("WAITING");
      setCandles([]);
      setVolumes([]);
      setAnnotations([]);
      setTradeLevels([]);
      return;
    }
    let alive = true;
    // Keep prior candles visible while the new TF loads (avoids blank chart flash)
    setStatus((prev) => (prev === "LIVE" ? "LIVE" : "WAITING"));
    async function load() {
      try {
        const [data, annData] = await Promise.all([
          fetchOhlcv(symbol!, timeframe, 250),
          fetchSetupAnnotations(symbol!).catch(() => ({ annotations: [] })),
        ]);
        if (!alive) return;
        setStatus(data.status);
        setAnnotations(annData.annotations || []);
        const cdata: CandlestickData[] = [];
        const vdata: HistogramData[] = [];
        for (const c of data.candles) {
          const t = Math.floor(new Date(String(c.open_time)).getTime() / 1000);
          if (!Number.isFinite(t)) continue;
          cdata.push({
            time: t as CandlestickData["time"],
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
          });
          vdata.push({
            time: t as HistogramData["time"],
            value: Number(c.volume) || 0,
            color:
              Number(c.close) >= Number(c.open)
                ? "rgba(61,214,140,0.4)"
                : "rgba(240,113,120,0.4)",
          });
        }
        if (cdata.length > 0 && data.status !== "WAITING") {
          setCandles(cdata);
          setVolumes(vdata);
        } else if (cdata.length === 0) {
          // Only blank the chart when the new TF truly has no bars
          setCandles([]);
          setVolumes([]);
        }
      } catch {
        if (alive) {
          setStatus("UNAVAILABLE");
        }
      }
    }
    load();
    const id = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [symbol, timeframe]);

  if (!hasData) {
    return (
      <ChartShell title="Price + Volume" status={status} showHeader={false}>
        <ChartEmpty title="Price + Volume" subtitle={liquidationsSubtitle(symbol, status)} />
      </ChartShell>
    );
  }

  return (
    <ChartShell title="Price + Volume" status={status}>
      <div ref={containerRef} className="chart-canvas" />
      <div className="pointer-events-none absolute left-2 top-5 z-10 max-w-[70%] text-[9px] text-terminal-muted">
        OHLCV candle close — may differ from Coin Detail ticker/mark
        {isIntradayTimeframe(timeframe) ? " · axis times UTC" : ""}
      </div>
      {/* Keep volume note above the time axis so tick labels stay readable */}
      <div className="pointer-events-none absolute bottom-7 right-14 z-10 max-w-[45%] text-right text-[9px] uppercase tracking-wide text-terminal-muted/80">
        Vol = base / candle (not 24h quote)
      </div>
      {ann.tradePlan ? (
        <div className="pointer-events-none absolute bottom-10 left-2 z-10 flex flex-wrap gap-1.5 rounded border border-terminal-border/70 bg-terminal-panel/90 px-2 py-1.5 text-[10px] font-mono">
          {tradeLevels.length > 0 ? (
            tradeLevels.map((lv) => (
              <span
                key={`${lv.title}-${lv.price}`}
                style={{ color: lv.color || colorForKind(lv.kind) }}
              >
                {lv.title} {lv.price}
              </span>
            ))
          ) : (
            <span className="text-terminal-muted">
              No active BUY/SELL setup (WAITING) — levels appear when entry confirms
            </span>
          )}
        </div>
      ) : null}
    </ChartShell>
  );
}

function LiqChart({
  symbol,
  onCompactChange,
}: {
  symbol: string | null;
  onCompactChange?: (compact: boolean) => void;
}) {
  const [status, setStatus] = useState("WAITING");
  const [points, setPoints] = useState<HistogramData[]>([]);
  const hasData = points.length > 0;
  const compact = isLiquidationsCompact(status, points.length);
  const { containerRef, chartRef } = useChartHost(hasData);
  const seriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  useEffect(() => {
    onCompactChange?.(compact);
  }, [compact, onCompactChange]);

  useEffect(() => {
    if (!hasData || !chartRef.current) return;
    const series = chartRef.current.addHistogramSeries({ priceFormat: { type: "volume" } });
    seriesRef.current = series;
    series.setData(points);
    chartRef.current.timeScale().fitContent();
    return () => {
      seriesRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasData, chartRef]);

  useEffect(() => {
    if (!hasData) return;
    seriesRef.current?.setData(points);
    chartRef.current?.timeScale().fitContent();
  }, [points, hasData, chartRef]);

  useEffect(() => {
    if (!symbol) {
      setStatus("WAITING");
      setPoints([]);
      return;
    }
    let alive = true;
    async function load() {
      try {
        const data = await fetchLiquidations(symbol!);
        if (!alive) return;
        setStatus(data.status);
        const events = sanitizeLiquidationEvents(
          (data.events || []) as Array<Record<string, unknown>>,
          data.status,
        );
        const buckets = new Map<number, { long: number; short: number }>();
        for (const e of events) {
          const t = Math.floor(new Date(String(e.timestamp)).getTime() / 60000) * 60;
          const b = buckets.get(t) || { long: 0, short: 0 };
          const notional = Number(e.notional) || 0;
          if (String(e.side).toUpperCase() === "SELL") b.long += notional;
          else b.short += notional;
          buckets.set(t, b);
        }
        const next: HistogramData[] = [...buckets.entries()]
          .sort((a, b) => a[0] - b[0])
          .map(([t, v]) => ({
            time: t as HistogramData["time"],
            value: v.long + v.short,
            color: v.long >= v.short ? "rgba(240,113,120,0.7)" : "rgba(61,214,140,0.7)",
          }));
        setPoints(
          next.length > 0 && (data.status === "LIVE" || data.status === "STALE")
            ? next
            : [],
        );
      } catch {
        if (alive) {
          setStatus("UNAVAILABLE");
          setPoints([]);
        }
      }
    }
    load();
    const id = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [symbol]);

  if (!hasData) {
    return (
      <ChartShell title="Liquidations" status={status} showHeader={false} compact={compact}>
        <ChartEmpty
          title="Liquidations"
          subtitle={liquidationsSubtitle(symbol, status)}
          compact={compact}
        />
      </ChartShell>
    );
  }

  return (
    <ChartShell title="Liquidations" status={status}>
      <div ref={containerRef} className="chart-canvas" />
    </ChartShell>
  );
}
