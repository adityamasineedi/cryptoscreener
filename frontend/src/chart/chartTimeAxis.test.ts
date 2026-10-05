import { describe, expect, it } from "vitest";
import { TickMarkType, type Time } from "lightweight-charts";
import {
  formatChartCrosshairTime,
  formatChartTickMark,
  isIntradayTimeframe,
  ensureAscendingByTime,
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

  it("shows HH:mm for Time ticks on 1h in UTC", () => {
    // 2026-10-01 08:00 UTC
    const t = (Date.UTC(2026, 9, 1, 8, 0, 0) / 1000) as Time;
    expect(formatChartTickMark(t, TickMarkType.Time, "1h", "UTC")).toBe("08:00");
    expect(formatChartTickMark(t, TickMarkType.Time, "1h", "UTC")).not.toBe("1");
  });

  it("shows IST clock for Time ticks on 1h", () => {
    // 2026-10-01 08:00 UTC → 13:30 IST
    const t = (Date.UTC(2026, 9, 1, 8, 0, 0) / 1000) as Time;
    expect(formatChartTickMark(t, TickMarkType.Time, "1h", "IST")).toBe("13:30");
  });

  it("shows Month Day for day boundary ticks in UTC", () => {
    const t = (Date.UTC(2026, 8, 30, 0, 0, 0) / 1000) as Time;
    expect(formatChartTickMark(t, TickMarkType.DayOfMonth, "1h", "UTC")).toBe("Sep 30");
    expect(formatChartTickMark(t, TickMarkType.Time, "1h", "UTC")).toBe("Sep 30");
  });

  it("uses IST midnight as day boundary for Time ticks", () => {
    // 2026-09-29 18:30 UTC = 2026-09-30 00:00 IST
    const t = (Date.UTC(2026, 8, 29, 18, 30, 0) / 1000) as Time;
    expect(formatChartTickMark(t, TickMarkType.Time, "1h", "IST")).toBe("Sep 30");
  });

  it("Month ticks on intraday use month+day not year", () => {
    const t = (Date.UTC(2026, 9, 1, 0, 0, 0) / 1000) as Time;
    expect(formatChartTickMark(t, TickMarkType.Month, "15m", "UTC")).toBe("Oct 1");
    expect(formatChartTickMark(t, TickMarkType.Month, "1d", "UTC")).toBe("Oct 2026");
  });

  it("enables timeVisible for intraday timeframes", () => {
    expect(timeScaleOptionsForTimeframe("1h").timeVisible).toBe(true);
    expect(timeScaleOptionsForTimeframe("1d").timeVisible).toBe(false);
  });

  it("crosshair includes UTC clock on 1h", () => {
    const t = (Date.UTC(2026, 9, 1, 14, 0, 0) / 1000) as Time;
    expect(formatChartCrosshairTime(t, "1h", "UTC")).toMatch(/1 Oct 14:00 UTC/);
  });

  it("crosshair includes IST clock on 1h", () => {
    // 14:00 UTC → 19:30 IST
    const t = (Date.UTC(2026, 9, 1, 14, 0, 0) / 1000) as Time;
    expect(formatChartCrosshairTime(t, "1h", "IST")).toMatch(/1 Oct 19:30 IST/);
  });

  it("defaults display zone to IST", () => {
    const t = (Date.UTC(2026, 9, 1, 8, 0, 0) / 1000) as Time;
    expect(formatChartTickMark(t, TickMarkType.Time, "1h")).toBe("13:30");
    expect(formatChartCrosshairTime(t, "1h")).toMatch(/1 Oct 13:30 IST/);
  });

  it("sorts and dedupes series points ascending by time", () => {
    const rows = [
      { time: 300 as Time, close: 3 },
      { time: 100 as Time, close: 1 },
      { time: 200 as Time, close: 2 },
      { time: 200 as Time, close: 22 },
    ];
    expect(ensureAscendingByTime(rows)).toEqual([
      { time: 100, close: 1 },
      { time: 200, close: 22 },
      { time: 300, close: 3 },
    ]);
  });
});
