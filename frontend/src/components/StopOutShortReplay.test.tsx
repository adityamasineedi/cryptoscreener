/**
 * @vitest-environment jsdom
 */
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { StrategyTradeRow } from "../api/client";
import { StopOutShortReplay } from "./StopOutShortReplay";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchResearchOhlcvCandles: vi.fn(async () => ({
      status: "OK",
      candles: [
        { open_time: "2026-08-30T20:00:00Z", high: 1.05, low: 0.88 },
        { open_time: "2026-08-30T21:00:00Z", high: 0.95, low: 0.75 },
      ],
    })),
  };
});

const stop: StrategyTradeRow = {
  symbol: "ETHUSDT",
  timeframe: "1h",
  direction: "LONG",
  outcome: "SL",
  entry_price: 1,
  stop_price: 0.9,
  tp1: 1.3,
  exit_time: "2026-08-30T20:00:00Z",
  signal_time: "2026-08-30T10:00:00Z",
};

describe("StopOutShortReplay", () => {
  it("is labelled uncosted research, warns that the costed short loses, and uses replay v2", async () => {
    render(<StopOutShortReplay trades={[stop]} symbol="ETHUSDT" timeframe="1h" />);
    const panel = screen.getByTestId("stop-out-replay");
    expect(panel.tagName).toBe("DETAILS");
    expect(panel.hasAttribute("open")).toBe(false);
    expect(panel.getAttribute("data-label")).toBe("UNCOSTED_RESEARCH_ONLY_DO_NOT_USE");
    expect(panel.textContent).toContain("UNCOSTED_RESEARCH_ONLY_DO_NOT_USE");
    const warning = screen.getByTestId("stop-out-replay-warning");
    expect(warning.textContent).toContain("Not a tradeable short book");
    expect(warning.textContent).toContain("Fully costed, the stop-reversal short is negative");
    await waitFor(() => expect(panel.textContent).toContain("Uncosted (v2)"));
    expect(panel.textContent).toContain("SL in 1 bar, −1R");
    expect(panel.textContent).toContain("Archived v1 replay (skipped the stop bar)");
  });
});
