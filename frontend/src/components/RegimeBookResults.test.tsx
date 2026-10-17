/** @vitest-environment jsdom */
import { render, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RegimeBookResults, regimeFillBookText, regimeScopeText } from "./RegimeBookResults";

vi.mock("../api/client", () => ({
  fetchRegimeBacktest: vi.fn(async () => ({
    status: "done",
    pnl_model: "CAUSAL_NEXT_OPEN",
    directions: ["LONG"],
    sides_in_scope: [{ strategy_id: "COMBO_03_TRANSITION_B", timeframe: "1h", direction: "LONG" }],
    summary: { trades: 0, by_strategy: [] },
  })),
}));

describe("RegimeBookResults", () => {
  it("names the long-only scope and the next-open fill book", async () => {
    const { container } = render(<RegimeBookResults active />);
    const root = within(container);
    await waitFor(() => {
      expect(root.getByTestId("regime-backtest-scope")).toBeTruthy();
    });
    expect(root.getByTestId("regime-backtest-scope").textContent).toMatch(/^Long book only\./);
    expect(root.getByTestId("regime-backtest-scope").textContent).not.toMatch(/Bearish runs the passed shorts/);
    expect(root.getByTestId("regime-backtest-fill-book").textContent).toMatch(/CAUSAL_NEXT_OPEN/);
    expect(root.getByTestId("regime-backtest-sides").textContent).toBe(
      "Sides in scope: COMBO_03_TRANSITION_B LONG",
    );
  });

  it("describes each scope and fill book", () => {
    expect(regimeScopeText({ status: "done", directions: ["SHORT"] })).toMatch(/^Short book only\./);
    expect(regimeScopeText({ status: "done", directions: ["LONG", "SHORT"] })).toMatch(
      /Bearish runs the passed shorts\. Bullish runs COMBO_03_TRANSITION_B long/,
    );
    expect(regimeFillBookText({ status: "done", pnl_model: "LEGACY_SIGNAL_CLOSE" })).toMatch(/signal close/);
    expect(regimeFillBookText({ status: "done" })).toBe("Fill book: not reported");
  });
});
