/**
 * Map a backtest blotter trade to chart markers / price lines.
 * Presentation-only — mirrors blotter fields; does not re-run entry gates.
 */

import type { StrategyTradeRow } from "../api/client";

export type TradeOverlayMarker = {
  time: number;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  text: string;
};

export type TradeOverlayPriceLine = {
  price: number;
  title: string;
  color: string;
  lineWidth: 1 | 2;
  lineStyle: number;
};

export type TradeOverlayPlan = {
  markers: TradeOverlayMarker[];
  priceLines: TradeOverlayPriceLine[];
  focusFrom: number | null;
  focusTo: number | null;
};

function toUnixSec(raw: string | null | undefined): number | null {
  if (!raw) return null;
  const ms = Date.parse(raw);
  if (!Number.isFinite(ms)) return null;
  return Math.floor(ms / 1000);
}

function snapToCandle(
  t: number | null,
  candleTimes: number[],
): number | null {
  if (t == null || !candleTimes.length) return null;
  // Exact match first
  if (candleTimes.includes(t)) return t;
  // Nearest at-or-before, else nearest after
  let best: number | null = null;
  let bestDist = Number.POSITIVE_INFINITY;
  for (const ct of candleTimes) {
    const d = Math.abs(ct - t);
    if (d < bestDist) {
      bestDist = d;
      best = ct;
    }
  }
  return best;
}

/** Stable key for blotter row selection. */
export function tradeRowKey(t: StrategyTradeRow): string {
  if (t.trade_no != null) return `n${t.trade_no}`;
  return `${t.signal_time ?? ""}|${t.entry_price}|${t.direction}`;
}

/**
 * Build markers + SL/TP/entry lines for a selected blotter trade.
 * `candleTimes` are unix seconds (ascending) from the loaded OHLCV window.
 */
export function buildTradeOverlay(
  trade: StrategyTradeRow,
  candleTimes: number[],
): TradeOverlayPlan {
  const isLong = String(trade.direction || "").toUpperCase() !== "SHORT";
  const entryT = snapToCandle(toUnixSec(trade.signal_time), candleTimes);
  const exitT = snapToCandle(toUnixSec(trade.exit_time), candleTimes);
  const outcome = String(trade.outcome || "").toUpperCase();

  const markers: TradeOverlayMarker[] = [];
  if (entryT != null) {
    markers.push({
      time: entryT,
      position: isLong ? "belowBar" : "aboveBar",
      color: "#3dd68c",
      shape: isLong ? "arrowUp" : "arrowDown",
      text: isLong ? "ENTRY" : "ENTRY↓",
    });
  }

  if (exitT != null) {
    const hitTp = outcome.startsWith("TP");
    const hitSl = outcome === "SL" || outcome.includes("STOP");
    markers.push({
      time: exitT,
      position: isLong ? "aboveBar" : "belowBar",
      color: hitSl ? "#f07178" : hitTp ? "#6cb6ff" : "#e6c07b",
      shape: "circle",
      text: outcome || "EXIT",
    });
  }

  const priceLines: TradeOverlayPriceLine[] = [];
  if (Number.isFinite(trade.entry_price)) {
    priceLines.push({
      price: Number(trade.entry_price),
      title: "ENTRY",
      color: "#3dd68c",
      lineWidth: 1,
      lineStyle: 2, // dashed
    });
  }
  if (Number.isFinite(trade.stop_price)) {
    priceLines.push({
      price: Number(trade.stop_price),
      title: "SL",
      color: "#f07178",
      lineWidth: 2,
      lineStyle: 0,
    });
  }
  if (trade.tp1 != null && Number.isFinite(trade.tp1)) {
    priceLines.push({
      price: Number(trade.tp1),
      title: "TP1",
      color: "#6cb6ff",
      lineWidth: 2,
      lineStyle: 0,
    });
  }
  if (trade.exit_price != null && Number.isFinite(trade.exit_price)) {
    priceLines.push({
      price: Number(trade.exit_price),
      title: "EXIT",
      color: "#e6c07b",
      lineWidth: 1,
      lineStyle: 2,
    });
  }

  return {
    markers,
    priceLines,
    focusFrom: entryT,
    focusTo: exitT ?? entryT,
  };
}
