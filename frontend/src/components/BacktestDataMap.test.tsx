/**
 * @vitest-environment jsdom
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BacktestDataMap } from "./BacktestDataMap";

describe("BacktestDataMap", () => {
  it("shows the run relationships and does not invent live signals", () => {
    render(
      <BacktestDataMap
        strategyLabel="COMBO_02 V1"
        symbols={1}
        ohlcvBars={1200}
        runs={1}
        trades={7}
      />
    );
    expect(screen.getByText("OHLCV")).toBeTruthy();
    expect(screen.getByText("FEATURE_EVENTS")).toBeTruthy();
    expect(screen.getByText("STRATEGIES")).toBeTruthy();
    expect(screen.getByText("LIVE_SIGNALS")).toBeTruthy();
    expect(screen.getByText("RESEARCH_EXPERIMENTS")).toBeTruthy();
    expect(screen.getByText("BACKTEST_RUNS")).toBeTruthy();
    expect(screen.getByText("BACKTEST_TRADES")).toBeTruthy();
    expect(screen.getByText("produces")).toBeTruthy();
    expect(screen.getByText("generates")).toBeTruthy();
    expect(screen.getByText("evaluated_by")).toBeTruthy();
    expect(screen.getByText("tracks")).toBeTruthy();
    expect(screen.getByText("contains")).toBeTruthy();
    expect(screen.getByText("7 trades")).toBeTruthy();
    expect(screen.getByText("not written from this page")).toBeTruthy();
  });
});
