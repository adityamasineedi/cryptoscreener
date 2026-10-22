/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchCapBookAgents, type CapBookAgentsResponse, type CapBookRow } from "../api/client";
import { CapBookAgentsPanel, PASS_LABEL } from "./CapBookAgentsPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, fetchCapBookAgents: vi.fn() };
});

function cell(
  combination_id: string,
  symbol: string,
  timeframe: string,
  direction: string,
  sample_size: number,
  pnl_usd_net: number,
): CapBookRow {
  return {
    combination_id,
    symbol,
    timeframe,
    direction,
    cap_group: "large",
    risk_usd: 20,
    sample_size,
    average_R: 0.5,
    average_R_net: 0.3,
    pnl_usd_net,
    max_drawdown_R: 2,
    cell_seconds: 1.2,
  };
}

function response(cells: number, shardARows?: CapBookRow[]): CapBookAgentsResponse {
  const base = [
    ["a", "Agent A", "large", ["BTC", "ETH"]],
    ["b", "Agent B", "large", ["TRX", "AVAX"]],
    ["c", "Agent C", "mid", ["NEAR", "ATOM"]],
    ["d", "Agent D", "mid", ["AAVE", "UNI"]],
  ] as const;
  const defaultRows = [
    cell("COMBO_02_V2", "BTCUSDT", "1h", "SHORT", 6, 12),
    cell("COMBO_01", "BTCUSDT", "1h", "LONG", 10, -40),
  ];
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
      rows: shard === "a" && cells ? (shardARows ?? defaultRows) : [],
    })),
  };
}

/** Rows that exercise the verdict column, duplicates and the D3 flag. */
function labelledRows(): CapBookRow[] {
  return [
    // First of a duplicate pair (kept as is).
    cell("COMBO_02_V2", "BTCUSDT", "1h", "SHORT", 4, 20),
    cell("COMBO_02_V2", "ETHUSDT", "1h", "SHORT", 3, 10),
    // Same per-coin trades and net as COMBO_02_V2 1h SHORT: duplicate, and D3 flagged.
    cell("COMBO_02_V2_1B", "BTCUSDT", "1h", "SHORT", 4, 20),
    cell("COMBO_02_V2_1B", "ETHUSDT", "1h", "SHORT", 3, 10),
    // Same totals (7 trades, +$30) but a different per-coin split: not a duplicate.
    cell("COMBO_02_V2_1_A", "BTCUSDT", "1h", "SHORT", 5, 25),
    cell("COMBO_02_V2_1_A", "ETHUSDT", "1h", "SHORT", 2, 5),
    // A research-killed side that lost in the window.
    cell("COMBO_01", "BTCUSDT", "1h", "SHORT", 10, -40),
    // No research entry for this side.
    cell("COMBO_04_HTF", "BTCUSDT", "1h", "LONG", 6, 3),
    // Pullback-bug copy, mapped to the research +PBFIX side; zero-trade rows are never duplicates.
    cell("COMBO_03", "BTCUSDT", "1h", "SHORT", 0, 0),
    cell("COMBO_04", "BTCUSDT", "1h", "SHORT", 0, 0),
  ];
}

function strategyRow(label: string): HTMLElement {
  const [id, tf, side] = label.split(" ");
  const body = screen.getByTestId("profit-checker-rows");
  const row = within(body)
    .getAllByRole("row")
    .find((tr) => {
      const cells = tr.querySelectorAll("td");
      return cells[1]?.textContent === id && cells[2]?.textContent === tf && cells[3]?.textContent === side;
    });
  if (!row) throw new Error(`row ${label} not found`);
  return row;
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

    expect(screen.getByTestId("profit-checker").textContent).toContain(PASS_LABEL);
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

  it("shows the in-sample banner, the coin-table banner and the snapshot date", async () => {
    vi.mocked(fetchCapBookAgents).mockResolvedValue(response(1, labelledRows()));
    render(<CapBookAgentsPanel />);
    await act(async () => Promise.resolve());

    const banner = screen.getByTestId("profit-checker-banner");
    expect(banner.textContent).toContain("Short-window in-sample screen. Not evidence. See the research results page.");
    expect(banner.textContent).toContain("research/out/report/index.html");
    expect(screen.getByTestId("coin-checker-banner").textContent).toBe(
      "One 3-week window; coin results here are mostly the coin's own price move, not evidence. Do not select coins from this table.",
    );
    const note = screen.getByTestId("research-snapshot-note").textContent ?? "";
    expect(note).toContain("2026-10-22");
    expect(note).toContain("research-audit 7e92b67");
  });

  it("renames PASS to 'net > $0 (window)' in strategy rows, coin rows and summary counts", async () => {
    vi.mocked(fetchCapBookAgents).mockResolvedValue(response(1, labelledRows()));
    render(<CapBookAgentsPanel />);
    await act(async () => Promise.resolve());

    const panel = screen.getByTestId("profit-checker");
    expect(panel.textContent).not.toMatch(/\bPASS\b/);
    expect(panel.textContent).not.toMatch(/Passed/);
    expect(panel.textContent).toContain(`4 of 7 strategy sides ${PASS_LABEL}.`);
    expect(within(strategyRow("COMBO_02_V2 1h SHORT")).getByText(PASS_LABEL)).toBeTruthy();

    const coinRows = screen.getByTestId("coin-checker-rows");
    expect(within(coinRows).getByText(new RegExp(`Coins net > \\$0 \\(window\\) · 2`))).toBeTruthy();
    const btc = within(coinRows).getByText("BTC").closest("tr") as HTMLElement;
    expect(within(btc).getByText(PASS_LABEL)).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: `Cells ${PASS_LABEL}` })).toBeTruthy();
  });

  it("shows the research verdict per row, including 'not in research' and the +PBFIX mapping", async () => {
    vi.mocked(fetchCapBookAgents).mockResolvedValue(response(1, labelledRows()));
    render(<CapBookAgentsPanel />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: /Show failed strategy sides/ }));

    const v2 = within(strategyRow("COMBO_02_V2 1h SHORT")).getByTestId("research-verdict").textContent ?? "";
    expect(v2).toContain("NOT RUN");
    expect(v2).toContain("D110");

    const combo01 = within(strategyRow("COMBO_01 1h SHORT")).getByTestId("research-verdict").textContent ?? "";
    expect(combo01).toContain("KILLED");
    expect(combo01).toContain("DISCOVERY");
    expect(combo01).toContain("Gate 2, E9");

    expect(within(strategyRow("COMBO_04_HTF 1h LONG")).getByTestId("research-verdict").textContent).toBe("not in research");

    const combo03 = within(strategyRow("COMBO_03 1h SHORT")).getByTestId("research-verdict").textContent ?? "";
    expect(combo03).toContain("KILLED");
    expect(combo03).toContain("COMBO_03+PBFIX");
  });

  it("marks identical-result rows as duplicates of the first, keeps every row, and flags D3 lookahead", async () => {
    vi.mocked(fetchCapBookAgents).mockResolvedValue(response(1, labelledRows()));
    render(<CapBookAgentsPanel />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: /Show failed strategy sides/ }));

    const notes = (label: string) => within(strategyRow(label)).getByTestId("strategy-notes").textContent ?? "";
    // Every row is still listed.
    expect(within(screen.getByTestId("profit-checker-rows")).getAllByRole("row")).toHaveLength(7);

    expect(notes("COMBO_02_V2 1h SHORT")).toBe("—");
    expect(notes("COMBO_02_V2_1B 1h SHORT")).toContain("duplicate of COMBO_02_V2 1h SHORT");
    expect(notes("COMBO_02_V2_1_A 1h SHORT")).not.toContain("duplicate");
    expect(notes("COMBO_03 1h SHORT")).toBe("—");
    expect(notes("COMBO_04 1h SHORT")).toBe("—");
    expect(screen.getByTestId("profit-checker").textContent).toContain(
      "1 strategy sides repeat an earlier row's per-coin results and are marked duplicate.",
    );

    expect(notes("COMBO_02_V2_1B 1h SHORT")).toContain("lookahead in backtest (D3)");
    expect(notes("COMBO_02_V2_1_A 1h SHORT")).toContain("lookahead in backtest (D3)");
    expect(notes("COMBO_02_V2 1h SHORT")).not.toContain("lookahead");
    expect(notes("COMBO_01 1h SHORT")).not.toContain("lookahead");
  });
});
