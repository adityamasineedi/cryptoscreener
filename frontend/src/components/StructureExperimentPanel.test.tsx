/**
 * @vitest-environment jsdom
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StructureExperimentPanel } from "./StructureExperimentPanel";

vi.mock("../api/client", () => ({
  fetchOhlcvStructureExperiment: vi.fn(),
}));

import { fetchOhlcvStructureExperiment } from "../api/client";

const metrics = {
  candidates: 10,
  qualified: 4,
  fills: 3,
  skips: 1,
  expirations: 2,
  invalidations: 0,
  incomplete_windows: 0,
  net_expectancy_r: -0.12,
  net_pnl_r: -0.36,
  max_drawdown_r: -0.4,
  profit_factor: 0.5,
  win_rate: 0.33,
  target_pct: 0.2,
  stop_pct: 0.4,
  timeout_pct: 0.4,
  close_back_10_pct: 0.66,
  close_back_10_denominator: 10,
  post_entry_close_back_10_pct: 0.5,
  post_entry_close_back_10_denominator: 3,
  average_holding_bars: 8,
  fees_r: 0.04,
  funding_r: 0,
};

describe("StructureExperimentPanel", () => {
  it("shows an explicit unavailable state", async () => {
    vi.mocked(fetchOhlcvStructureExperiment).mockResolvedValue({
      status: "unavailable",
      research_only: true,
      validation_status: "forward_evaluation_still_required",
      reason: "No saved report",
    });
    render(<StructureExperimentPanel />);
    expect(await screen.findByText(/Research data unavailable/i)).toBeTruthy();
    expect(screen.getByText(/No saved report/i)).toBeTruthy();
    expect(screen.queryByText(/Net E\[R\]/i)).toBeNull();
  });

  it("separates level recross from trade outcomes", async () => {
    vi.mocked(fetchOhlcvStructureExperiment).mockResolvedValue({
      status: "ready",
      research_only: true,
      validation_status: "forward_evaluation_still_required",
      assumptions: {
        funding_label: "Results exclude funding.",
        entry: "next bar open",
        hold_window: "20 bars",
        slippage_rate: 0.0002,
        fee_rate_per_side: 0.0004,
        stop_policy: "frozen opposite swing",
        retest_equality: "close equal to the level does not qualify",
        windows: "already inspected",
      },
      legacy_reference: {
        label: "Prior study left trades unresolved.",
        not_a_variant: true,
      },
      variants: ["A_corrected_baseline", "B_acceptance", "C_retest", "D_faster_confirmation"].map(
        (id) => ({
          id,
          event_count: 10,
          boundary_excluded: 1,
          median_bars_confirmation_to_break: 2,
          windows: {
            development: metrics,
            later_slice: metrics,
          },
        }),
      ),
      examples: [
        {
          event_id: "e1",
          symbol: "BTCUSDT",
          direction: "LONG",
          call_type: "bos_with_trend",
          variant: "A_corrected_baseline",
          status: "filled",
          provisional_swing: "not_tradable",
          pivot_at: "2024-01-01T00:00:00+00:00",
          confirmed_at: "2024-01-01T03:00:00+00:00",
          break_at: "2024-01-01T05:00:00+00:00",
          qualified_at: "2024-01-01T05:00:00+00:00",
          entry_at: "2024-01-01T06:00:00+00:00",
          invalidated_at: null,
          expired_at: null,
          broken_level: 100,
          stop_price: 90,
          target_price: 120,
          entry_price: 101,
          outcome: "timeout",
          ambiguous: false,
          net_r: -0.05,
          confirmation_delay_bars: 3,
          bars_from_confirmation_to_break: 2,
        },
      ],
    });
    render(<StructureExperimentPanel />);
    expect(await screen.findByText(/Research only/i)).toBeTruthy();
    expect(screen.getAllByText(/Level recross/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText("A · Next-bar baseline").length).toBe(2);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "filled" }));
    await waitFor(() => {
      expect(screen.getByText(/provisional swing is not tradable/i)).toBeTruthy();
    });
    expect(screen.getByText(/2024-01-01T06:00:00/)).toBeTruthy();
    expect(screen.getByText(/Results exclude funding/i)).toBeTruthy();
  });
});
