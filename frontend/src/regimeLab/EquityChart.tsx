import { useEffect, useRef } from "react";
import { ColorType, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";

/** Marked-to-market equity, one point per bar (downsampled server-side). */
export function EquityChart({ points }: { points: { time: number; equity: number }[] }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const el = hostRef.current;
    if (!el || points.length < 2) return;
    const chart = createChart(el, {
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b95a8" },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      width: el.clientWidth,
      height: 260,
    });
    const series = chart.addAreaSeries({
      lineColor: "#3d9cf0",
      topColor: "rgba(61,156,240,0.25)",
      bottomColor: "rgba(61,156,240,0.02)",
      lineWidth: 2,
    });
    const seen = new Set<number>();
    series.setData(
      points
        .filter((p) => (seen.has(p.time) ? false : (seen.add(p.time), true)))
        .map((p) => ({ time: p.time as UTCTimestamp, value: p.equity })),
    );
    series.createPriceLine({
      price: points[0].equity,
      color: "rgba(139,155,180,0.5)",
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: false,
      title: "start",
    });
    chart.timeScale().fitContent();
    chartRef.current = chart;
    const ro = new ResizeObserver(() => {
      if (hostRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: hostRef.current.clientWidth });
      }
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      chartRef.current = null;
      chart.remove();
    };
  }, [points]);

  if (points.length < 2) return <p className="text-sm text-terminal-muted">No equity curve.</p>;
  return <div ref={hostRef} className="w-full" data-testid="regime-lab-equity" />;
}
