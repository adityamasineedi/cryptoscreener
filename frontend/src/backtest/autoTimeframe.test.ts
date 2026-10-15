import { describe, expect, it } from "vitest";
import { pickAutoSetupTimeframe } from "./autoTimeframe";

describe("pickAutoSetupTimeframe", () => {
  it("keeps 1h when coverage is empty", () => {
    expect(pickAutoSetupTimeframe([], ["AVAXUSDT"])).toBe("1h");
  });

  it("prefers 1h when every coin has 1h bars", () => {
    expect(
      pickAutoSetupTimeframe(
        [
          { symbol: "AVAXUSDT", timeframe: "1h", bars: 500 },
          { symbol: "AVAXUSDT", timeframe: "15m", bars: 2000 },
          { symbol: "LINKUSDT", timeframe: "1h", bars: 400 },
          { symbol: "LINKUSDT", timeframe: "4h", bars: 200 },
        ],
        ["AVAXUSDT", "LINKUSDT"],
      ),
    ).toBe("1h");
  });

  it("falls to the timeframe that covers more coins", () => {
    expect(
      pickAutoSetupTimeframe(
        [
          { symbol: "AVAXUSDT", timeframe: "1h", bars: 10 },
          { symbol: "AVAXUSDT", timeframe: "4h", bars: 200 },
          { symbol: "LINKUSDT", timeframe: "4h", bars: 180 },
        ],
        ["AVAXUSDT", "LINKUSDT"],
      ),
    ).toBe("4h");
  });
});
