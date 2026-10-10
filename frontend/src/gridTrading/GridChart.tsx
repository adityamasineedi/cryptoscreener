import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
} from "lightweight-charts";
import type { GridCandle, GridFill } from "./client";

type Props = {
  candles: GridCandle[];
  levels: number[];
  stopPrice: number;
  fills: GridFill[];
};

function toUnix(time: GridCandle["time"], index: number): number {
  if (typeof time === "number" && Number.isFinite(time)) {
    return time > 1e12 ? Math.floor(time / 1000) : Math.floor(time);
  }
  const parsed = Date.parse(String(time));
  if (Number.isFinite(parsed)) return Math.floor(parsed / 1000);
  return index;
}

/** Futures-candle chart for one grid backtest. Copied from the research chart setup. */
export function GridChart({ candles, levels, stopPrice, fills }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8b95a8",
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      rightPriceScale: { borderVisible: false, autoScale: true },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
      },
      width: el.clientWidth,
      height: 320,
    });
    const series = chart.addCandlestickSeries({
      upColor: "#3dd68c",
      downColor: "#f07178",
      borderVisible: false,
      wickUpColor: "#3dd68c",
      wickDownColor: "#f07178",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    const ro = new ResizeObserver(() => {
      if (!hostRef.current || !chartRef.current) return;
      chartRef.current.applyOptions({ width: hostRef.current.clientWidth });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      priceLinesRef.current = [];
      seriesRef.current = null;
      chartRef.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    const rows: CandlestickData[] = [];
    const seen = new Set<number>();
    candles.forEach((bar, index) => {
      let time = toUnix(bar.time, index + 1);
      while (seen.has(time)) time += 1;
      seen.add(time);
      rows.push({
        time: time as CandlestickData["time"],
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      });
    });
    rows.sort((a, b) => Number(a.time) - Number(b.time));
    series.setData(rows);
    for (const line of priceLinesRef.current) {
      try {
        series.removePriceLine(line);
      } catch {
        /* disposed */
      }
    }
    priceLinesRef.current = [];
    const shown = levels.length > 24 ? [levels[0], levels[levels.length - 1]] : levels;
    shown.forEach((price, index) => {
      priceLinesRef.current.push(
        series.createPriceLine({
          price,
          color: "rgba(120, 162, 255, 0.85)",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: index === 0 ? "grid low" : index === shown.length - 1 ? "grid high" : "grid",
        }),
      );
    });
    if (stopPrice > 0) {
      priceLinesRef.current.push(
        series.createPriceLine({
          price: stopPrice,
          color: "#f07178",
          lineWidth: 1,
          lineStyle: 0,
          axisLabelVisible: true,
          title: "stop",
        }),
      );
    }
    const timeByIndex = rows.map((row) => row.time);
    series.setMarkers(
      fills
        .map((fill) => {
          const index = candles.findIndex((bar) => bar.time === fill.time);
          const time = timeByIndex[index >= 0 ? index : 0];
          if (time == null) return null;
          const buy = fill.side === "buy";
          return {
            time,
            position: buy ? ("belowBar" as const) : ("aboveBar" as const),
            color: buy ? "#3dd68c" : "#f07178",
            shape: buy ? ("arrowUp" as const) : ("arrowDown" as const),
            text: buy ? "B" : "S",
          };
        })
        .filter((marker): marker is NonNullable<typeof marker> => marker != null),
    );
    if (rows.length) chart.timeScale().fitContent();
  }, [candles, levels, stopPrice, fills]);

  return <div ref={hostRef} className="h-80 w-full" data-testid="grid-chart" />;
}
