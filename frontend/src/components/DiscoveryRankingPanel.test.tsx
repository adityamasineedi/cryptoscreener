/** @vitest-environment jsdom */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchDiscoveryRunRanking, fetchLatestDiscoveryRun } from "../api/client";
import { DiscoveryRankingPanel } from "./DiscoveryRankingPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchLatestDiscoveryRun: vi.fn(),
    fetchDiscoveryRunRanking: vi.fn(),
  };
});

function mockTrades(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => body,
    }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockTrades({ trades: [] });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DiscoveryRankingPanel", () => {
  it("renders a research_passed row as experimental and not approved", async () => {
    vi.mocked(fetchLatestDiscoveryRun).mockResolvedValue({ run_id: "fixture-run" });
    vi.mocked(fetchDiscoveryRunRanking).mockResolvedValue({
      run_id: "fixture-run",
      disclaimer: "Research ranking only. A higher rank is not a profitability claim.",
      rows: [
        {
          rank: 1,
          symbol: "BTCUSDT",
          combination_id: "COMBO_01",
          direction: "LONG",
          sample_size: 12,
          walk_forward_windows: 2,
          oos_profit_factors: [1.1, null],
          research_passed: true,
          promotion_status: "EXPERIMENTAL",
          sharpe: null,
          sortino: null,
          profit: null,
          operator_approved: false,
          production_approved: false,
          paper_eligible: false,
          live_eligible: false,
          telegram_eligible: false,
        },
      ],
    });

    render(<DiscoveryRankingPanel />);

    expect(await screen.findByText("EXPERIMENTAL")).toBeTruthy();
    expect(screen.getByText("not approved")).toBeTruthy();
    expect(screen.getByTestId("discovery-research-passed")).toHaveTextContent("research_passed: true");
    expect(screen.getByText("BTCUSDT")).toBeTruthy();
    expect(screen.getByText("COMBO_01")).toBeTruthy();
    expect(screen.getByText("LONG")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByText("1.1, —")).toBeTruthy();
    expect(screen.getByTestId("discovery-ranking-disclaimer").textContent).toMatch(/not a profitability claim/);
    expect(screen.queryByText("Sharpe")).toBeNull();
    expect(screen.queryByText("Sortino")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Profit" })).toBeNull();
    expect(screen.queryByRole("button", { name: /paper|live|telegram/i })).toBeNull();
    expect(screen.getByTestId("discovery-pattern-summary")).toHaveTextContent("no pattern summary");
    expect(screen.getByTestId("discovery-pattern-summary").textContent).not.toMatch(/\d/);
    expect(screen.getByTestId("discovery-embargo-summary")).toHaveTextContent("no embargo summary");
    expect(screen.getByTestId("discovery-embargo-summary").textContent).not.toMatch(/\d/);
    expect(fetchDiscoveryRunRanking).toHaveBeenCalledWith("fixture-run", expect.any(Object));
  });

  it("shows each symbol cluster count and the experimental flag", async () => {
    vi.mocked(fetchLatestDiscoveryRun).mockResolvedValue({ run_id: "fixture-run" });
    vi.mocked(fetchDiscoveryRunRanking).mockResolvedValue({
      run_id: "fixture-run",
      disclaimer: "Research ranking only. A higher rank is not a profitability claim.",
      patterns: {
        experimental: true,
        symbols: [
          { symbol: "BTCUSDT", cluster_count: 3, experimental: true },
          { symbol: "ETHUSDT", cluster_count: 2, experimental: true },
        ],
      },
      rows: [
        {
          rank: 1,
          symbol: "BTCUSDT",
          combination_id: "COMBO_01",
          direction: "LONG",
          sample_size: 12,
          walk_forward_windows: 2,
          oos_profit_factors: [1.1],
          research_passed: true,
          sharpe: null,
          sortino: null,
          profit: null,
          production_approved: false,
        },
      ],
    } as Awaited<ReturnType<typeof fetchDiscoveryRunRanking>>);

    render(<DiscoveryRankingPanel />);

    const summary = await screen.findByTestId("discovery-pattern-summary");
    expect(summary.textContent).toContain("BTCUSDT");
    expect(summary.textContent).toContain("ETHUSDT");
    expect(summary.textContent).toContain("3");
    expect(summary.textContent).toContain("2");
    expect(summary.textContent).toContain("EXPERIMENTAL");
    expect(summary.textContent).not.toContain("no pattern summary");
    expect(screen.getByTestId("discovery-embargo-summary")).toHaveTextContent("no embargo summary");
    expect(screen.getAllByText("EXPERIMENTAL").length).toBeGreaterThan(0);
    expect(screen.getByText("not approved")).toBeTruthy();
    expect(screen.queryByText("Sharpe")).toBeNull();
    expect(screen.queryByText("Sortino")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Profit" })).toBeNull();
    expect(screen.queryByRole("button", { name: /paper|live|telegram/i })).toBeNull();
  });

  it("shows not a strategy from by_symbol and does not invent a cluster count", async () => {
    vi.mocked(fetchLatestDiscoveryRun).mockResolvedValue({ run_id: "fixture-run" });
    vi.mocked(fetchDiscoveryRunRanking).mockResolvedValue({
      run_id: "fixture-run",
      disclaimer: "Research ranking only. A higher rank is not a profitability claim.",
      patterns: {
        by_symbol: {
          SOLUSDT: {
            is_strategy: false,
            clusters: [{ cluster_id: 1 }, { cluster_id: 2 }, { cluster_id: 4 }],
          },
          ETHUSDT: {
            is_strategy: false,
          },
        },
      },
      rows: [
        {
          rank: 1,
          symbol: "SOLUSDT",
          combination_id: "COMBO_01",
          direction: "SHORT",
          research_passed: false,
          sharpe: null,
          sortino: null,
          profit: null,
          production_approved: false,
        },
      ],
    } as Awaited<ReturnType<typeof fetchDiscoveryRunRanking>>);

    render(<DiscoveryRankingPanel />);

    const summary = await screen.findByTestId("discovery-pattern-summary");
    expect(summary.textContent).toContain("SOLUSDT");
    expect(summary.textContent).toContain("ETHUSDT");
    expect(summary.textContent).toContain("not a strategy");
    expect(summary.textContent).toMatch(/SOLUSDT\s*· clusters\s*—/);
    expect(summary.textContent).toMatch(/ETHUSDT\s*· clusters\s*—/);
    expect(summary.textContent).not.toMatch(/clusters\s*[0-9]/);
    expect(summary.textContent).not.toContain("no pattern summary");
    expect(screen.getByTestId("discovery-embargo-summary")).toHaveTextContent("no embargo summary");
    expect(screen.getByText("not approved")).toBeTruthy();
    expect(screen.getByTestId("discovery-research-passed")).toHaveTextContent("research_passed: false");
    expect(screen.getAllByText("EXPERIMENTAL").length).toBeGreaterThan(0);
    expect(screen.queryByText("Sharpe")).toBeNull();
    expect(screen.queryByText("Sortino")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Profit" })).toBeNull();
    expect(screen.queryByRole("button", { name: /paper|live|telegram/i })).toBeNull();
  });

  it("shows no discovery run and no numbers when no run is stored", async () => {
    vi.mocked(fetchLatestDiscoveryRun).mockResolvedValue({ run_id: null });

    const { container } = render(<DiscoveryRankingPanel />);

    expect(await screen.findByTestId("discovery-ranking-empty")).toHaveTextContent("no discovery run");
    await waitFor(() => {
      expect(fetchDiscoveryRunRanking).not.toHaveBeenCalled();
    });
    expect(container.textContent).not.toMatch(/\d/);
    expect(screen.queryByText("Sharpe")).toBeNull();
    expect(screen.queryByText("Sortino")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Profit" })).toBeNull();
    expect(screen.queryByText("no pattern summary")).toBeNull();
    expect(screen.queryByText("no embargo summary")).toBeNull();
    expect(screen.queryByText(/cluster/i)).toBeNull();
    expect(screen.queryByText("no trades")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows one stored trade as experimental and leaves null fields blank", async () => {
    vi.mocked(fetchLatestDiscoveryRun).mockResolvedValue({ run_id: "fixture-run" });
    vi.mocked(fetchDiscoveryRunRanking).mockResolvedValue({
      run_id: "fixture-run",
      rows: [
        {
          rank: 1,
          symbol: "BTCUSDT",
          combination_id: "COMBO_01",
          direction: "LONG",
          sample_size: 4,
          walk_forward_windows: 2,
          oos_profit_factors: [1.1],
          research_passed: true,
          promotion_status: "EXPERIMENTAL",
        },
      ],
    });
    mockTrades({
      trades: [
        {
          symbol: "ETHUSDT",
          combination_id: "COMBO_01",
          direction: "SHORT",
          entry_time: "2025-01-01T10:00:00+00:00",
          entry: 1.5,
          stop: null,
          target: null,
          outcome: "SL",
          r: null,
        },
      ],
    });

    render(<DiscoveryRankingPanel />);

    expect(await screen.findByText("2025-01-01T10:00:00+00:00")).toBeTruthy();
    const trades = screen.getByTestId("discovery-trades");
    expect(trades.textContent).toContain("ETHUSDT");
    expect(trades.textContent).toContain("COMBO_01");
    expect(trades.textContent).toContain("SHORT");
    expect(trades.textContent).toContain("2025-01-01T10:00:00+00:00");
    expect(screen.getByTestId("discovery-trade-entry")).toHaveTextContent("1.5");
    expect(screen.getByTestId("discovery-trade-stop").textContent).toBe("");
    expect(screen.getByTestId("discovery-trade-target").textContent).toBe("");
    expect(screen.getByTestId("discovery-trade-outcome")).toHaveTextContent("SL");
    expect(screen.getByTestId("discovery-trade-r").textContent).toBe("");
    expect(trades.textContent).toContain("EXPERIMENTAL");
    expect(trades.textContent).toContain("not approved");
    expect(trades.textContent).not.toMatch(/\b0\b/);
    expect(screen.queryByRole("button", { name: /paper|live|telegram/i })).toBeNull();
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toMatch(/\/api\/research\/market-structure-discovery-v1\/fixture-run\/trades$/);
    expect(init).toEqual(expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("shows no trades when the trades list is empty", async () => {
    vi.mocked(fetchLatestDiscoveryRun).mockResolvedValue({ run_id: "fixture-run" });
    vi.mocked(fetchDiscoveryRunRanking).mockResolvedValue({
      run_id: "fixture-run",
      rows: [
        {
          rank: 1,
          symbol: "BTCUSDT",
          combination_id: "COMBO_01",
          direction: "LONG",
          sample_size: 4,
          walk_forward_windows: 2,
          oos_profit_factors: [1.1],
          research_passed: false,
        },
      ],
    });
    mockTrades([]);

    render(<DiscoveryRankingPanel />);

    expect(await screen.findByText("no trades")).toBeTruthy();
    expect(screen.getByTestId("discovery-trades")).toHaveTextContent("no trades");
    expect(screen.queryByRole("columnheader", { name: "Entry time" })).toBeNull();
    expect(screen.queryByRole("button", { name: /paper|live|telegram/i })).toBeNull();
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toMatch(/\/api\/research\/market-structure-discovery-v1\/fixture-run\/trades$/);
    expect(init).toEqual(expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("shows excluded training-bar and embargo bar counts when an embargo object is present", async () => {
    vi.mocked(fetchLatestDiscoveryRun).mockResolvedValue({ run_id: "fixture-run" });
    vi.mocked(fetchDiscoveryRunRanking).mockResolvedValue({
      run_id: "fixture-run",
      disclaimer: "Research ranking only. A higher rank is not a profitability claim.",
      embargo: {
        excluded_train_indices: [8],
        embargo_indices: [9, 10],
        train_indices: [0, 1, 2, 3, 4, 5, 6, 7],
        test_indices: [11],
        research_only: true,
      },
      rows: [
        {
          rank: 1,
          symbol: "BTCUSDT",
          combination_id: "COMBO_01",
          direction: "LONG",
          sample_size: 12,
          walk_forward_windows: 2,
          oos_profit_factors: [1.1],
          research_passed: true,
          promotion_status: "EXPERIMENTAL",
          sharpe: null,
          sortino: null,
          profit: null,
          operator_approved: false,
          production_approved: false,
          paper_eligible: false,
          live_eligible: false,
          telegram_eligible: false,
        },
      ],
    } as Awaited<ReturnType<typeof fetchDiscoveryRunRanking>>);

    render(<DiscoveryRankingPanel />);

    const summary = await screen.findByTestId("discovery-embargo-summary");
    expect(summary.textContent).toContain("excluded training-bar count: 1");
    expect(summary.textContent).toContain("embargo bar count: 2");
    expect(summary.textContent).not.toContain("no embargo summary");
    expect(summary.textContent).not.toContain("11");
    expect(screen.queryByTestId("discovery-embargo-disclaimer")).toBeNull();
    expect(screen.getAllByText("EXPERIMENTAL").length).toBeGreaterThan(0);
    expect(screen.getAllByText("not approved").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /paper|live|telegram/i })).toBeNull();
  });

  it("shows the embargo disclaimer string when the embargo object includes one", async () => {
    vi.mocked(fetchLatestDiscoveryRun).mockResolvedValue({ run_id: "fixture-run" });
    vi.mocked(fetchDiscoveryRunRanking).mockResolvedValue({
      run_id: "fixture-run",
      disclaimer: "Research ranking only. A higher rank is not a profitability claim.",
      embargo: {
        excluded_train_indices: [8],
        embargo_indices: [9, 10],
        train_indices: [0, 1, 2, 3, 4, 5, 6, 7],
        test_indices: [11],
        research_only: true,
        disclaimer:
          "Research-only label purge and embargo. Removing overlapping training labels does not establish profitability, a strategy, or an approval to trade.",
      },
      rows: [
        {
          rank: 1,
          symbol: "BTCUSDT",
          combination_id: "COMBO_01",
          direction: "LONG",
          sample_size: 12,
          walk_forward_windows: 2,
          oos_profit_factors: [1.1],
          research_passed: true,
          promotion_status: "EXPERIMENTAL",
          sharpe: null,
          sortino: null,
          profit: null,
          operator_approved: false,
          production_approved: false,
          paper_eligible: false,
          live_eligible: false,
          telegram_eligible: false,
        },
      ],
    } as Awaited<ReturnType<typeof fetchDiscoveryRunRanking>>);

    render(<DiscoveryRankingPanel />);

    const summary = await screen.findByTestId("discovery-embargo-summary");
    expect(screen.getByTestId("discovery-embargo-disclaimer")).toHaveTextContent(
      "Research-only label purge and embargo. Removing overlapping training labels does not establish profitability, a strategy, or an approval to trade.",
    );
    expect(summary.textContent).toContain("excluded training-bar count: 1");
    expect(summary.textContent).toContain("embargo bar count: 2");
    expect(summary.textContent).not.toContain("11");
    expect(summary.textContent).not.toMatch(/train_indices|test_indices|unresolved/);
    expect(screen.getAllByText("EXPERIMENTAL").length).toBeGreaterThan(0);
    expect(screen.getAllByText("not approved").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /paper|live|telegram/i })).toBeNull();
  });
});
