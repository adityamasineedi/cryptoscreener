import { describe, expect, it } from "vitest";
import {
  API_BAR_CAP,
  FALLBACK_MAX_BARS,
  coverageRowLabel,
  maxLookbackBars,
} from "./availability";

describe("maxLookbackBars", () => {
  it("uses the smallest bar count among the selected cells", () => {
    expect(
      maxLookbackBars(
        [
          { symbol: "BTCUSDT", timeframe: "1h", bars: 15000 },
          { symbol: "ETHUSDT", timeframe: "1h", bars: 9000 },
          { symbol: "BTCUSDT", timeframe: "4h", bars: 8760 },
          { symbol: "ETHUSDT", timeframe: "4h", bars: 12000 },
        ],
        ["BTCUSDT", "ETHUSDT"],
        ["1h", "4h"],
      ),
    ).toEqual({ bars: 8760, source: "db", dbBars: 8760 });
  });

  it("ignores rows outside the selection", () => {
    expect(
      maxLookbackBars(
        [
          { symbol: "BTCUSDT", timeframe: "1h", bars: 15000 },
          { symbol: "SOLUSDT", timeframe: "1h", bars: 10 },
          { symbol: "BTCUSDT", timeframe: "15m", bars: 0 },
        ],
        ["BTCUSDT"],
        ["1h"],
      ),
    ).toEqual({ bars: 15000, source: "db", dbBars: 15000 });
  });

  it("caps at the API limit and reports the DB count", () => {
    expect(
      maxLookbackBars(
        [{ symbol: "BTCUSDT", timeframe: "1h", bars: 35040 }],
        ["BTCUSDT"],
        ["1h"],
      ),
    ).toEqual({ bars: API_BAR_CAP, source: "api-cap", dbBars: 35040 });
    expect(API_BAR_CAP).toBe(20000);
  });

  it("does not cap at exactly the API limit", () => {
    expect(
      maxLookbackBars(
        [{ symbol: "BTCUSDT", timeframe: "1h", bars: 20000 }],
        ["BTCUSDT"],
        ["1h"],
      ),
    ).toEqual({ bars: 20000, source: "db", dbBars: 20000 });
  });

  it("falls back when a selected cell has no row", () => {
    expect(
      maxLookbackBars(
        [{ symbol: "BTCUSDT", timeframe: "1h", bars: 15000 }],
        ["BTCUSDT", "ETHUSDT"],
        ["1h"],
      ),
    ).toEqual({ bars: FALLBACK_MAX_BARS, source: "fallback", dbBars: null });
    expect(maxLookbackBars([], ["BTCUSDT"], ["1h"])).toEqual({
      bars: 12200,
      source: "fallback",
      dbBars: null,
    });
  });

  it("falls back when the smallest selected cell is empty", () => {
    expect(
      maxLookbackBars(
        [
          { symbol: "BTCUSDT", timeframe: "1h", bars: 15000 },
          { symbol: "ETHUSDT", timeframe: "1h", bars: 0 },
        ],
        ["BTCUSDT", "ETHUSDT"],
        ["1h"],
      ),
    ).toEqual({ bars: FALLBACK_MAX_BARS, source: "fallback", dbBars: 0 });
  });
});

describe("coverageRowLabel", () => {
  it("formats symbol, timeframe, UTC days, and bar count", () => {
    expect(
      coverageRowLabel({
        symbol: "BTCUSDT",
        timeframe: "1h",
        bars: 35040,
        start: "2022-10-13T00:00:00+00:00",
        end: "2026-10-12T23:00:00+00:00",
      }),
    ).toBe("BTC/1h 2022-10-13→2026-10-12 · 35,040 bars");
  });

  it("shows ? for a missing date", () => {
    expect(
      coverageRowLabel({ symbol: "SOLUSDT", timeframe: "4h", bars: 0, start: null }),
    ).toBe("SOL/4h ?→? · 0 bars");
  });
});
