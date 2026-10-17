import { describe, expect, it } from "vitest";
import type { StrategyTradeRow } from "../api/client";
import {
  buildTradeOverlay,
  isStopLossOutcome,
  tradeEntryTime,
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

describe("backtestTradeOverlay trade-audit fixes", () => {
  const H = 3600;
  const t0 = Date.parse("2025-01-02T00:00:00Z") / 1000;
  const candles = Array.from({ length: 12 }, (_, i) => t0 + i * H);
  // Job 53c3cab8b696 BTCUSDT trade 1: signal 03:00, fill 04:00 (entry 95047.5 = 04:00 open + slippage), exit 09:00.
  const causal = trade({
    signal_time: "2025-01-02T03:00:00+00:00",
    fill_time: "2025-01-02T04:00:00+00:00",
    execution_model: "CAUSAL_NEXT_OPEN",
    exit_time: "2025-01-02T09:00:00+00:00",
    entry_price: 95047.5,
    stop_price: 94245.38,
    tp1: 96509.23,
    exit_price: 96460.98,
    outcome: "TP1",
  });
  const iso = (sec: number) => new Date(sec * 1000).toISOString();

  it("B3 puts the ENTRY marker on the next-open fill bar and marks the signal bar", () => {
    const plan = buildTradeOverlay(causal, candles);
    const entry = plan.markers.find((m) => m.text.startsWith("ENTRY"))!;
    expect(iso(entry.time)).toBe("2025-01-02T04:00:00.000Z");
    const signal = plan.markers.find((m) => m.text === "SIGNAL")!;
    expect(iso(signal.time)).toBe("2025-01-02T03:00:00.000Z");
    expect(plan.markers.map((m) => m.text)).toEqual(["SIGNAL", "ENTRY", "TP1"]);
    expect(iso(plan.focusFrom!)).toBe("2025-01-02T04:00:00.000Z");
  });

  it("B3 falls back to the signal bar when there is no fill_time", () => {
    const plan = buildTradeOverlay({ ...causal, fill_time: undefined }, candles);
    expect(plan.markers.map((m) => m.text)).toEqual(["ENTRY", "TP1"]);
    expect(iso(plan.markers[0].time)).toBe("2025-01-02T03:00:00.000Z");
  });

  it("keys numbered causal rows by trade_no, one key per row (audit #20)", () => {
    // Causal (DUAL) rows now carry trade_no 1..n and trade_id t1..tn in book order.
    const rows = [1, 2, 3].map((n) => ({ ...causal, trade_no: n, trade_id: `t${n}` }));
    expect(rows.map(tradeRowKey)).toEqual(["n1", "n2", "n3"]);
  });

  it("B3 keeps the trade key on the signal time", () => {
    expect(tradeRowKey({ ...causal, trade_no: undefined })).toBe(
      tradeRowKey({ ...causal, trade_no: undefined, fill_time: "2025-01-02T05:00:00+00:00" }),
    );
    expect(tradeEntryTime(causal)).toBe("2025-01-02T04:00:00+00:00");
    expect(tradeEntryTime({ ...causal, fill_time: null })).toBe("2025-01-02T03:00:00+00:00");
  });

  it("B12 colours time-stop and timeout exits neutral, stop-loss exits red", () => {
    const exitColor = (outcome: string) =>
      buildTradeOverlay({ ...causal, fill_time: undefined, outcome }, candles).markers.at(-1)!.color;
    for (const outcome of ["TIME_STOP", "timeout", "TIMEOUT", "EXPIRED"]) {
      expect(exitColor(outcome)).toBe("#e6c07b");
    }
    for (const outcome of ["SL", "STOP", "stop", "STOP_LOSS", "GAP_THROUGH_STOP"]) {
      expect(exitColor(outcome)).toBe("#f07178");
    }
    expect(isStopLossOutcome("TIME_STOP")).toBe(false);
    expect(isStopLossOutcome("SL")).toBe(true);
  });
});
