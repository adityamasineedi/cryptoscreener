import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
} from "lightweight-charts";
import {
  fetchResearchOhlcvCandles,
  type StrategyTradeRow,
} from "../api/client";
import {
  buildTradeOverlay,
  tradeRowKey,
} from "../chart/backtestTradeOverlay";
import {
  ensureAscendingByTime,
  localizationForTimeframe,
  timeScaleOptionsForTimeframe,
} from "../chart/chartTimeAxis";

type Props = {
  trade: StrategyTradeRow;
};

/**
 * Price chart for one selected backtest blotter trade.
 * Loads Postgres OHLCV around entry→exit and overlays entry/exit/SL/TP.
 */
export function BacktestTradeChart({ trade }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const [status, setStatus] = useState<"LOADING" | "OK" | "EMPTY" | "ERROR">(
    "LOADING",
  );
  const [error, setError] = useState<string | null>(null);
  const [barCount, setBarCount] = useState(0);

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

    const start = trade.signal_time;
    if (!start) {
      setStatus("EMPTY");
      setError("Trade has no entry time");
      series.setData([]);
      series.setMarkers([]);
      return;
    }

    let alive = true;
    setStatus("LOADING");
    setError(null);

    (async () => {
      try {
        const data = await fetchResearchOhlcvCandles({
          symbol: trade.symbol,
          timeframe: trade.timeframe,
          start,
          end: trade.exit_time || start,
          pad_bars: 64,
        });
        if (!alive) return;

        if (data.status === "UNAVAILABLE" || data.status === "ERROR") {
          setStatus("ERROR");
          setError(data.reason || data.status);
          series.setData([]);
          series.setMarkers([]);
          return;
        }

        const candles: CandlestickData[] = [];
        const times: number[] = [];
        for (const c of data.candles || []) {
          const t = Math.floor(new Date(String(c.open_time)).getTime() / 1000);
          if (!Number.isFinite(t)) continue;
          times.push(t);
          candles.push({
            time: t as CandlestickData["time"],
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
          });
        }
        const sorted = ensureAscendingByTime(candles);
        series.setData(sorted);
        setBarCount(sorted.length);

        for (const pl of priceLinesRef.current) {
          try {
            series.removePriceLine(pl);
          } catch {
            /* disposed */
          }
        }
        priceLinesRef.current = [];

        if (!sorted.length) {
          setStatus("EMPTY");
          series.setMarkers([]);
          return;
        }

        const overlay = buildTradeOverlay(trade, times);
        for (const line of overlay.priceLines) {
          const pl = series.createPriceLine({
            price: line.price,
            color: line.color,
            lineWidth: line.lineWidth,
            lineStyle: line.lineStyle,
            axisLabelVisible: true,
            title: line.title,
          });
          priceLinesRef.current.push(pl);
        }
        series.setMarkers(
          overlay.markers.map((m) => ({
            time: m.time as CandlestickData["time"],
            position: m.position,
            color: m.color,
            shape: m.shape,
            text: m.text,
          })),
        );

        chart.applyOptions({
          localization: localizationForTimeframe(trade.timeframe),
        });
        chart.timeScale().applyOptions({
          ...timeScaleOptionsForTimeframe(trade.timeframe),
        });

        if (overlay.focusFrom != null && overlay.focusTo != null) {
          const padSec =
            trade.timeframe === "15m"
              ? 15 * 60 * 12
              : trade.timeframe === "4h"
                ? 4 * 3600 * 8
                : 3600 * 16;
          chart.timeScale().setVisibleRange({
            from: (overlay.focusFrom - padSec) as CandlestickData["time"],
            to: (overlay.focusTo + padSec) as CandlestickData["time"],
          });
        } else {
          chart.timeScale().fitContent();
        }

        setStatus("OK");
      } catch (err) {
        if (!alive) return;
        setStatus("ERROR");
        setError(err instanceof Error ? err.message : String(err));
        series.setData([]);
        series.setMarkers([]);
      }
    })();

    return () => {
      alive = false;
    };
  }, [trade]);

  const title = `#${trade.trade_no ?? "—"} · ${trade.symbol} ${trade.timeframe} · ${trade.direction}`;

  return (
    <div className="rounded border border-terminal-border/60 bg-black/20">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-terminal-border/40 px-3 py-2">
        <div className="text-[11px] uppercase tracking-wide text-terminal-muted">
          Trade chart overlay
        </div>
        <div className="font-mono text-[11px] text-terminal-text">{title}</div>
        <div className="font-mono text-[10px] text-terminal-muted">
          {status === "LOADING"
            ? "Loading…"
            : status === "OK"
              ? `${barCount} bars · entry/SL/TP1/exit`
              : status === "EMPTY"
                ? "No candles in window"
                : error || "Failed"}
        </div>
      </div>
      <div ref={hostRef} className="h-[320px] w-full" />
      <div className="border-t border-terminal-border/40 px-3 py-1.5 font-mono text-[10px] text-terminal-muted">
        key={tradeRowKey(trade)} · green=entry · red=SL · blue=TP1 · markers from blotter times
      </div>
    </div>
  );
}
