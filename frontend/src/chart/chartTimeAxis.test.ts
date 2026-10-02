import { describe, expect, it } from "vitest";
import { TickMarkType, type Time } from "lightweight-charts";
import {
  formatChartCrosshairTime,
  formatChartTickMark,
  isIntradayTimeframe,
  timeScaleOptionsForTimeframe,
} from "./chartTimeAxis";

describe("chart time axis", () => {
  it("treats minute/hour TFs as intraday", () => {
    expect(isIntradayTimeframe("1m")).toBe(true);
    expect(isIntradayTimeframe("15m")).toBe(true);
    expect(isIntradayTimeframe("1h")).toBe(true);
    expect(isIntradayTimeframe("4h")).toBe(true);
    expect(isIntradayTimeframe("1d")).toBe(false);
  });

  it("shows HH:mm for Time ticks on 1h (not bare day-of-month)", () => {
    // 2026-10-01 08:00 UTC
    const t = (Date.UTC(2026, 9, 1, 8, 0, 0) / 1000) as Time;
    expect(formatChartTickMark(t, TickMarkType.Time, "1h")).toBe("08:00");
    expect(formatChartTickMark(t, TickMarkType.Time, "1h")).not.toBe("1");
  });

  it("shows Month Day for day boundary ticks", () => {
    const t = (Date.UTC(2026, 8, 30, 0, 0, 0) / 1000) as Time;
    expect(formatChartTickMark(t, TickMarkType.DayOfMonth, "1h")).toBe("Sep 30");
    expect(formatChartTickMark(t, TickMarkType.Time, "1h")).toBe("Sep 30");
  });

  it("enables timeVisible for intraday timeframes", () => {
    expect(timeScaleOptionsForTimeframe("1h").timeVisible).toBe(true);
    expect(timeScaleOptionsForTimeframe("1d").timeVisible).toBe(false);
  });

  it("crosshair includes UTC clock on 1h", () => {
    const t = (Date.UTC(2026, 9, 1, 14, 0, 0) / 1000) as Time;
    expect(formatChartCrosshairTime(t, "1h")).toMatch(/1 Oct 14:00 UTC/);
  });
});
