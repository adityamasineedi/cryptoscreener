import { describe, expect, it } from "vitest";
import type { StrategyTradeRow } from "../api/client";
import {
  buildTradeOverlay,
  tradeRowKey,
} from "./backtestTradeOverlay";

function trade(partial: Partial<StrategyTradeRow>): StrategyTradeRow {
  return {
    symbol: "BTCUSDT",
    timeframe: "1h",
    direction: "LONG",
    entry_price: 100,
    stop_price: 90,
    tp1: 120,
    ...partial,
  };
}

describe("backtestTradeOverlay", () => {
  it("builds entry/exit markers and SL/TP lines snapped to candles", () => {
    const t = trade({
      trade_no: 2,
      signal_time: "2025-06-01T10:00:00+00:00",
      exit_time: "2025-06-01T14:00:00+00:00",
      outcome: "TP1",
      exit_price: 120,
    });
    const times = [
      Date.parse("2025-06-01T08:00:00Z") / 1000,
      Date.parse("2025-06-01T10:00:00Z") / 1000,
      Date.parse("2025-06-01T12:00:00Z") / 1000,
      Date.parse("2025-06-01T14:00:00Z") / 1000,
      Date.parse("2025-06-01T16:00:00Z") / 1000,
    ];
    const plan = buildTradeOverlay(t, times);
    expect(plan.markers).toHaveLength(2);
    expect(plan.markers[0].text).toBe("ENTRY");
    expect(plan.markers[0].shape).toBe("arrowUp");
    expect(plan.markers[1].text).toBe("TP1");
    expect(plan.markers[1].color).toBe("#6cb6ff");
    expect(plan.priceLines.map((l) => l.title)).toEqual([
      "ENTRY",
      "SL",
      "TP1",
      "EXIT",
    ]);
    expect(plan.focusFrom).toBe(times[1]);
    expect(plan.focusTo).toBe(times[3]);
  });

  it("colors SL exits red and shorts use down entry arrow", () => {
    const t = trade({
      direction: "SHORT",
      signal_time: "2025-06-01T10:00:00Z",
      exit_time: "2025-06-01T11:00:00Z",
      outcome: "SL",
    });
    const times = [
      Date.parse("2025-06-01T10:00:00Z") / 1000,
      Date.parse("2025-06-01T11:00:00Z") / 1000,
    ];
    const plan = buildTradeOverlay(t, times);
    expect(plan.markers[0].shape).toBe("arrowDown");
    expect(plan.markers[1].color).toBe("#f07178");
  });

  it("tradeRowKey prefers trade_no", () => {
    expect(tradeRowKey(trade({ trade_no: 7, signal_time: "x" }))).toBe("n7");
  });
});
