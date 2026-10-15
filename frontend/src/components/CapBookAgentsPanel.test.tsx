/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchCapBookAgents, type CapBookAgentsResponse } from "../api/client";
import { CapBookAgentsPanel } from "./CapBookAgentsPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, fetchCapBookAgents: vi.fn() };
});

function response(cells: number): CapBookAgentsResponse {
  const base = [
    ["a", "Agent A", "large", ["BTC", "ETH"]],
    ["b", "Agent B", "large", ["TRX", "AVAX"]],
    ["c", "Agent C", "mid", ["NEAR", "ATOM"]],
    ["d", "Agent D", "mid", ["AAVE", "UNI"]],
  ] as const;
  return {
    status: "OK",
    poll_interval_seconds: 3,
    timestamp: "2026-10-10T12:00:00Z",
    shards: base.map(([shard, agent_name, cap_group, coins]) => ({
      shard,
      agent_name,
      cap_group,
      coins: [...coins],
      status: shard === "d" ? "waiting" : "RUNNING",
      market_direction: "NEUTRAL",
      principal_usd: 1000,
      elapsed_seconds: cells * 2,
      cells_finished: shard === "a" ? cells : 0,
      rows: shard === "a" && cells
        ? [
            {
              combination_id: "COMBO_02_V2",
              symbol: "BTCUSDT",
              timeframe: "1h",
              direction: "SHORT",
              cap_group: "large",
              risk_usd: 20,
              sample_size: 6,
              average_R: 0.5,
              average_R_net: 0.3,
              pnl_usd_net: 12,
              max_drawdown_R: 2,
              cell_seconds: 1.2,
            },
            {
              combination_id: "COMBO_01",
              symbol: "BTCUSDT",
              timeframe: "1h",
              direction: "LONG",
              cap_group: "large",
              risk_usd: 20,
              sample_size: 10,
              average_R: -0.4,
              average_R_net: -0.5,
              pnl_usd_net: -40,
              max_drawdown_R: 3,
              cell_seconds: 1.2,
            },
          ]
        : [],
    })),
  };
}

describe("CapBookAgentsPanel", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("renders four shards and applies row filters/highlight", async () => {
    vi.mocked(fetchCapBookAgents).mockResolvedValue(response(1));
    render(<CapBookAgentsPanel />);
    await act(async () => Promise.resolve());

    expect(screen.getByTestId("profit-checker").textContent).toContain("PASS");
    expect(within(screen.getByTestId("coin-checker-rows")).getByText(/Loss coins/)).toBeTruthy();
    expect(within(screen.getByTestId("coin-checker-rows")).getByText(/Failed coins/)).toBeTruthy();
    expect(within(screen.getByTestId("coin-checker-rows")).getByText("BTC")).toBeTruthy();
    expect(screen.getByText(/Agent A/)).toBeTruthy();
    expect(screen.getByText(/Agent D/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Show cell table/ }));
    const body = screen.getByTestId("cap-book-rows");
    expect(within(body).getByText("COMBO_02_V2").closest("tr")?.className).toContain("bg-emerald");
    expect(screen.queryByText("COMBO_01")).toBeNull();

    fireEvent.change(screen.getByLabelText("Filter by coin"), { target: { value: "BTC" } });
    expect(within(body).getByText("COMBO_02_V2")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter by side"), { target: { value: "LONG" } });
    expect(within(body).getByText(/No completed cells match/)).toBeTruthy();
  });

  it("polls after three seconds and updates the finished-cell count", async () => {
    vi.mocked(fetchCapBookAgents)
      .mockResolvedValueOnce(response(1))
      .mockResolvedValueOnce(response(2));
    render(<CapBookAgentsPanel />);
    await act(async () => Promise.resolve());
    expect(screen.getByTestId("shard-a-cells").textContent).toBe("1");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(fetchCapBookAgents).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("shard-a-cells").textContent).toBe("2");
  });
});
