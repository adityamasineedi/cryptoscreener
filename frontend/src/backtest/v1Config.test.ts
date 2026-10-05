import { describe, expect, it } from "vitest";
import {
  assessProductionComparable,
  barsToApproxDays,
  formatBarsDuration,
  symbolRoleDisplay,
  timeframeRoleLabel,
  v1RiskForSymbol,
} from "./v1Config";

describe("v1Config lookback duration", () => {
  it("labels 5760 1h bars as ~240 days", () => {
    expect(barsToApproxDays(5760, "1h")).toBe(240);
    expect(formatBarsDuration(5760, "1h")).toContain("240 days");
  });

  it("labels 5760 15m bars as ~60 days", () => {
    expect(barsToApproxDays(5760, "15m")).toBe(60);
  });

  it("labels 5760 4h bars as ~960 days", () => {
    expect(barsToApproxDays(5760, "4h")).toBe(960);
  });
});

describe("v1Config timeframe roles", () => {
  it("labels 1h as v1 setup timeframe", () => {
    expect(timeframeRoleLabel("1h")).toBe("v1 setup timeframe");
  });
  it("labels 4h as HTF context", () => {
    expect(timeframeRoleLabel("4h")).toBe("HTF context");
  });
  it("labels 15m as research-only", () => {
    expect(timeframeRoleLabel("15m")).toBe("research-only");
  });
});

describe("v1Config risk and identity display", () => {
  it("BTC/ETH/SOL roles", () => {
    expect(symbolRoleDisplay("BTCUSDT")).toBe("BTCUSDT — v1 CORE");
    expect(symbolRoleDisplay("ETHUSDT")).toBe("ETHUSDT — v1 SECONDARY");
    expect(symbolRoleDisplay("SOLUSDT")).toBe("SOLUSDT — v1 SECONDARY");
  });

  it("v1 risk at $1000 principal", () => {
    expect(v1RiskForSymbol("BTCUSDT", 1000)).toEqual({
      riskPercent: 2,
      riskUsd: 20,
      role: "core",
    });
    expect(v1RiskForSymbol("ETHUSDT", 1000)?.riskUsd).toBe(20);
    expect(v1RiskForSymbol("SOLUSDT", 1000)?.riskUsd).toBe(20);
  });

  it("exact frozen config is production-comparable", () => {
    const r = assessProductionComparable({
      symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
      timeframes: ["1h"],
      direction: "LONG",
      researchRiskOverride: false,
      leverage: 2,
      takerFeePct: 0.04,
      makerFeePct: 0.02,
    });
    expect(r.productionComparable).toBe(true);
  });

  it("risk override / 4h setup is research-only", () => {
    expect(
      assessProductionComparable({
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        direction: "LONG",
        researchRiskOverride: true,
        leverage: 2,
        takerFeePct: 0.04,
        makerFeePct: 0.02,
      }).productionComparable
    ).toBe(false);
    expect(
      assessProductionComparable({
        symbols: ["BTCUSDT"],
        timeframes: ["4h"],
        direction: "LONG",
        researchRiskOverride: false,
        leverage: 2,
        takerFeePct: 0.04,
        makerFeePct: 0.02,
      }).reasons
    ).toContain("setup_timeframe_mismatch");
  });
});
