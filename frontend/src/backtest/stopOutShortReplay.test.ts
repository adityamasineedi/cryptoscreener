import { describe, expect, it } from "vitest";
import type { StrategyTradeRow } from "../api/client";
import {
  favorableStopNote,
  formatReplayHit,
  replayShortsAtLongStops,
  replaySummary,
  type ReplayCandle,
} from "./stopOutShortReplay";

function longStop(overrides: Partial<StrategyTradeRow> = {}): StrategyTradeRow {
  return {
    symbol: "DOTUSDT",
    timeframe: "1h",
    direction: "LONG",
    outcome: "SL",
    entry_price: 1,
    stop_price: 0.9,
    tp1: 1.3,
    exit_time: "2026-08-30T20:00:00Z",
    signal_time: "2026-08-30T10:00:00Z",
    ...overrides,
  };
}

function bar(hour: string, high: number, low: number): ReplayCandle {
  return { open_time: `2026-08-30T${hour}:00:00Z`, high, low };
}

describe("replayShortsAtLongStops", () => {
  it("stops the short on the next bar and ignores the fill bar", () => {
    const replay = replayShortsAtLongStops(
      [longStop()],
      [
        bar("20", 1.2, 0.5),
        bar("21", 1.01, 0.88),
      ],
    );
    expect(replay.rows).toHaveLength(1);
    expect(replay.rows[0].shortEntry).toBe(0.9);
    expect(formatReplayHit(replay.rows[0].full)).toBe("SL in 1 bar, −1R");
    expect(formatReplayHit(replay.rows[0].oneR)).toBe("SL in 1 bar, −1R");
  });

  it("books 1R before the mirrored target, and gives the stop the same bar", () => {
    const candles: ReplayCandle[] = [bar("20", 0.95, 0.9)];
    for (let hour = 21; hour <= 28; hour += 1) {
      const label = String(hour).padStart(2, "0");
      candles.push(bar(label, hour === 23 ? 0.95 : 0.94, hour === 23 ? 0.8 : 0.85));
    }
    candles.push(bar("29", 1.0, 0.84));
    const replay = replayShortsAtLongStops([longStop()], candles);
    expect(formatReplayHit(replay.rows[0].oneR)).toBe("TP in 3 bars, +1R");
    expect(formatReplayHit(replay.rows[0].full)).toBe("SL in 9 bars, −1R");
  });

  it("v1 (archived default) skips the stop bar; v2 counts it, stop first", () => {
    // Short entry 0.9, short stop 1.0, 1R target 0.8. The stop bar trades back up to
    // 1.05 (through the short stop); the next bar falls to 0.75.
    const candles: ReplayCandle[] = [bar("20", 1.05, 0.88), bar("21", 0.95, 0.75)];
    const v1 = replayShortsAtLongStops([longStop()], candles);
    const v1Explicit = replayShortsAtLongStops([longStop()], candles, "v1_skip_stop_bar");
    const v2 = replayShortsAtLongStops([longStop()], candles, "v2_stop_bar_counts");
    expect(formatReplayHit(v1.rows[0].oneR)).toBe("TP in 1 bar, +1R");
    expect(v1Explicit.oneRR).toBe(v1.oneRR);
    expect(formatReplayHit(v2.rows[0].oneR)).toBe("SL in 1 bar, −1R");
    expect(v2.oneRR).toBe(-1);
  });

  it("v2 agrees with v1 when the stop bar stays inside the short stop", () => {
    const candles: ReplayCandle[] = [bar("20", 0.95, 0.88), bar("21", 0.95, 0.75)];
    const v1 = replayShortsAtLongStops([longStop()], candles);
    const v2 = replayShortsAtLongStops([longStop()], candles, "v2_stop_bar_counts");
    expect(v1.oneRR).toBe(1);
    expect(v2.oneRR).toBe(1);
    expect(formatReplayHit(v2.rows[0].oneR)).toBe("TP in 2 bars, +1R");
  });

  it("counts the stop when the stop bar also trades through 1R", () => {
    const replay = replayShortsAtLongStops(
      [longStop({ exit_time: "2026-09-07T03:00:00Z" })],
      [
        { open_time: "2026-09-07T03:00:00Z", high: 0.95, low: 0.9 },
        { open_time: "2026-09-07T04:00:00Z", high: 1.0, low: 0.78 },
      ],
    );
    expect(formatReplayHit(replay.rows[0].oneR)).toBe("SL in 1 bar, −1R");
    expect(replay.rows[0].mfeR).toBeCloseTo(1.2, 5);
    expect(favorableStopNote(replay.rows)).toBe(
      "Price did move in favor after stop-out 1, by about 1.2R, then came back through the short stop.",
    );
    expect(replaySummary(replay)).toBe(
      "Holding for the original targets is −1R on top of the longs that already lost. Taking only 1R on the short is −1R (0 winners, 1 stop).",
    );
  });
});
