/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { act } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StructureLabelPanel } from "./StructureLabelPanel";

vi.mock("../api/client", () => ({
  fetchStructureLabels: vi.fn(),
}));

import { fetchStructureLabels } from "../api/client";

const snapshot = {
  symbol: "BTCUSDT",
  rule_version: "structure-baseline-v1",
  experimental: false,
  as_of: "2026-10-10T04:15:00+00:00",
  trade_connected: false,
  baseline_notes: [],
  timeframes: {
    "4h": frame("4h"),
    "1h": frame("1h"),
    "15m": frame("15m"),
  },
};

function frame(timeframe: string) {
  return {
    timeframe,
    dataset_fingerprint: "abc123",
    range_explanation: "matched swing boundaries",
    coverage: {
      usable_bars: 100,
      start_open: "2026-08-11T00:00:00+00:00",
      end_open: "2026-10-10T00:00:00+00:00",
      end_known_at: "2026-10-10T04:00:00+00:00",
      gap_count: 0,
      warmup_bars: 8,
      scored_bars: 92,
    },
    counts: { bullish: 20, bearish: 10, transition: 60, range: 2, insufficient_data: 8 },
    denominator: 100,
    latest: {
      state: "transition",
      reason: "resistance_broken",
      explanation:
        "Transition from bearish · upward CHoCH · higher low confirmed · higher high pending · 6 bars since break.",
      known_at: "2026-10-10T04:00:00+00:00",
      state_age: 6,
      bars_since_break: 6,
      ambiguity_flags: [],
      initialization: "ready",
      data_quality: "ok",
      expiry_status: "within_limit",
      designated_age_bars: 4,
      reversal_evidence: { higher_low: "confirmed", higher_high: "pending" },
      designated_level: null,
      supporting_swing: null,
      resisting_swing: {
        swing_id: "15m:HIGH:3",
        price: 10,
        pivot_time: "2026-10-10T02:00:00+00:00",
        confirmation_open_time: "2026-10-10T02:45:00+00:00",
        known_at: "2026-10-10T03:00:00+00:00",
      },
    },
  };
}

describe("StructureLabelPanel", () => {
  beforeEach(() => {
    cleanup();
    vi.mocked(fetchStructureLabels).mockReset();
    vi.useRealTimers();
  });

  it("shows the baseline explanation and all five states", async () => {
    vi.mocked(fetchStructureLabels).mockResolvedValue(snapshot);
    render(<StructureLabelPanel />);
    expect((await screen.findAllByText(/higher high pending/)).length).toBe(3);
    expect(screen.getAllByText(/matched swing boundaries/).length).toBe(3);
    expect(screen.getAllByText(/Counts use the display denominator/).length).toBe(3);
    expect(screen.getAllByText(/insufficient_data 8/).length).toBeGreaterThan(0);
    expect(screen.getByText("Not a trade signal")).toBeTruthy();
    expect(fetchStructureLabels).toHaveBeenCalledWith("BTCUSDT", "structure-baseline-v1", expect.any(AbortSignal));
  });

  it("labels the distinct-pivot candidate as experimental", async () => {
    vi.mocked(fetchStructureLabels).mockResolvedValue({
      ...snapshot,
      rule_version: "structure-candidate-distinct-pivots-v1",
      experimental: true,
    });
    const user = userEvent.setup();
    render(<StructureLabelPanel />);
    const rules = await screen.findAllByLabelText("Structure rule");
    await user.selectOptions(rules[0], "structure-candidate-distinct-pivots-v1");
    await waitFor(() => {
      expect(fetchStructureLabels).toHaveBeenCalledWith(
        "BTCUSDT",
        "structure-candidate-distinct-pivots-v1",
        expect.any(AbortSignal),
      );
    });
    expect(screen.getByText("Experimental")).toBeTruthy();
  });

  it("shows a request error", async () => {
    vi.mocked(fetchStructureLabels).mockImplementation(() =>
      Promise.reject(new Error("API /api/research/structure-labels failed: 404")),
    );
    render(<StructureLabelPanel />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/404/);
  });

  it("shows a loading state and a timeout instead of an empty panel", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(fetchStructureLabels).mockImplementation(
        (_symbol, _rule, signal) =>
          new Promise((_resolve, reject) => {
            signal?.addEventListener("abort", () => {
              reject(new DOMException("The operation was aborted.", "AbortError"));
            });
          }),
      );
      render(<StructureLabelPanel />);
      expect(screen.getByRole("status").textContent).toMatch(/Loading structure labels/);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20000);
      });
      expect(screen.getByRole("alert").textContent).toMatch(/timed out/);
    } finally {
      vi.clearAllTimers();
      vi.useRealTimers();
    }
  });

});
