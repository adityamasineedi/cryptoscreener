/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchStrategyCatalog,
  fetchStrategyRank,
  type StrategyRankResponse,
  type StrategyRankRow,
} from "../api/client";
import { StrategiesPanel } from "./StrategiesPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, fetchStrategyCatalog: vi.fn(), fetchStrategyRank: vi.fn() };
});

function row(over: Partial<StrategyRankRow>): StrategyRankRow {
  return {
    rank: 1,
    key: "day|X|LONG|1h",
    style: "day",
    family: "combo",
    strategy_id: "X",
    direction: "LONG",
    timeframe: "1h",
    in_catalog: false,
    catalog_id: null,
    name: "X",
    description_source: "rank_file",
    summary: "Not in the strategy catalog. Family: candle12.",
    gates: [],
    variant: null,
    coins: 10,
    coins_passing: 0,
    coins_net_positive: 0,
    passing_symbols: [],
    trades: 0,
    net_pnl_usd: 0,
    fees_usd: 0,
    worst_cell_net_usd: 0,
    best_cell_net_usd: 0,
    execution_models: ["LEGACY_SIGNAL_CLOSE"],
    fill_label: "Signal close, no slippage",
    risk_usd: [20],
    leverage: [2],
    sizing_note: null,
    usd_comparable: true,
    aliases: [],
    alias_of: null,
    do_not_use: false,
    do_not_use_reason: null,
    cells: [],
    ...over,
  };
}

const RANK: StrategyRankResponse = {
  status: "OK",
  source: { path: "backend/scripts/strategy_rank/ranked.json" },
  window: {
    start: "2026-02-14",
    end: "2026-10-12",
    note: "1d candles end 2026-10-08",
    sample_note: "In-sample, one window, no out-of-sample test.",
    coins: ["BTC", "ETH"],
    market: "USDT perps",
    account: { principal_usd: 1000, risk_usd: 20, leverage: 2, exceptions: "Grid is 1x." },
    fees: { taker_pct: 0.04, maker_pct: 0.02 },
  },
  caveats: ["Nothing here is production-ready on this evidence."],
  groups: [
    {
      id: "day",
      label: "Day trading",
      timeframes: "1h",
      cells: 406,
      cells_passing: 37,
      caveats: ["Shorts on 1h are negative or marginal across the ten coins."],
      rows: [
        row({
          rank: 1,
          key: "day|CANDLE12_V2|LONG|1h",
          strategy_id: "CANDLE12_V2",
          name: "CANDLE12 V2",
          coins_passing: 6,
          passing_symbols: ["ADA", "BTC"],
          net_pnl_usd: 525.68,
          trades: 194,
          fees_usd: -164.1,
          worst_cell_net_usd: -97.27,
          fill_label: "0.02% slippage included",
        }),
        row({
          rank: 2,
          key: "day|COMBO_02|LONG|1h",
          strategy_id: "COMBO_02",
          name: "COMBO_02 v1",
          in_catalog: true,
          catalog_id: "COMBO_02_V1",
          description_source: "catalog",
          summary: "Primary working long playbook.",
          gates: ["BOS confirmed"],
          coins_passing: 5,
          passing_symbols: ["BTC", "ETH"],
          net_pnl_usd: 785.82,
          cells: [
            {
              symbol: "BTCUSDT",
              coin: "BTC",
              net_pnl_usd: 119.22,
              win_rate: 0.389,
              profit_factor: 1.8,
              trades_closed: 18,
              fees_usd: -22.98,
              max_drawdown_usd: 40,
              passes: true,
              fail_reasons: [],
            },
            {
              symbol: "DOGEUSDT",
              coin: "DOGE",
              net_pnl_usd: -58.71,
              win_rate: 0.2,
              profit_factor: 0.63,
              trades_closed: 10,
              fees_usd: -9.99,
              max_drawdown_usd: 70,
              passes: false,
              fail_reasons: ["net <= 0", "trades < 15"],
            },
          ],
        }),
      ],
    },
    { id: "swing", label: "Swing", timeframes: "4h · 1d", cells: 780, cells_passing: 0, caveats: [], rows: [] },
  ],
};

const CATALOG = {
  primary_working: "COMBO_02_V1",
  strategies: [
    {
      id: "COMBO_02_V1",
      name: "COMBO_02 v1 — HTF-gated LONG",
      status: "WORKING",
      summary: "Primary working long playbook.",
      gates: ["BOS confirmed"],
      horizon: "swing",
    },
  ],
};

function renderPanel() {
  return render(
    <MemoryRouter>
      <StrategiesPanel />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchStrategyCatalog).mockResolvedValue(CATALOG);
});

afterEach(cleanup);

describe("StrategiesPanel ranked board", () => {
  it("shows the board first with window, rank, direction, coins and net", async () => {
    vi.mocked(fetchStrategyRank).mockResolvedValue(RANK);
    renderPanel();

    const board = await screen.findByTestId("strategy-rank-board");
    const catalog = await screen.findByTestId("strategy-group-swing");
    expect(board.compareDocumentPosition(catalog) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const win = within(board).getByTestId("strategy-rank-window");
    expect(win.textContent).toContain("2026-02-14 to 2026-10-12");
    expect(win.textContent).toContain("In-sample, one window, no out-of-sample test.");

    expect(within(board).getByTestId("strategy-rank-dates").textContent).toBe(
      "Tested 2026-02-14 to 2026-10-12"
    );

    const first = within(board).getByTestId("rank-row-day|CANDLE12_V2|LONG|1h");
    expect(first.textContent).toContain("Long");
    expect(first.textContent).toContain("6 of 10");
    expect(first.textContent).toContain("ADA BTC");
    expect(first.textContent).toContain("+525.68");
    expect(first.textContent).toContain("Not in catalog");
    expect(first.textContent).toContain("0.02% slippage included");
  });

  it("opens the catalog pane and coin results for a ranked catalog strategy", async () => {
    vi.mocked(fetchStrategyRank).mockResolvedValue(RANK);
    renderPanel();

    fireEvent.click(await screen.findByTestId("rank-row-day|COMBO_02|LONG|1h"));
    const detail = await screen.findByTestId("strategy-rank-detail");
    expect(detail.textContent).toContain("BTC");
    expect(detail.textContent).toContain("38.9%");
    expect(detail.textContent).toContain("Tested: 2026-02-14 to 2026-10-12");
    expect(detail.textContent).toContain("Fail: net <= 0, trades < 15");
    expect(screen.getByText("Gates / checklist")).toBeTruthy();

    // An id outside the catalog still shows its coin table, without a stale catalog pane.
    fireEvent.click(screen.getByTestId("rank-row-day|CANDLE12_V2|LONG|1h"));
    await waitFor(() => expect(screen.queryByText("Gates / checklist")).toBeNull());
    expect(screen.getByTestId("strategy-rank-detail").textContent).toContain(
      "not in the strategy catalog"
    );
  });

  it("shows the owner D200 verdict on the COMBO_02_V1 catalog card", async () => {
    vi.mocked(fetchStrategyRank).mockResolvedValue(RANK);
    renderPanel();
    const card = await screen.findByTestId("strategy-card-COMBO_02_V1");
    expect(within(card).getByTestId("catalog-verdict-COMBO_02_V1|LONG").textContent).toBe(
      "Research: KILLED (OOS_FAIL archived; COMBO_02 LONG killed in DISCOVERY)"
    );
  });

  it("shows an error for a missing rank file and keeps the catalog", async () => {
    vi.mocked(fetchStrategyRank).mockResolvedValue({
      status: "MISSING",
      error: "backend/scripts/strategy_rank/ranked.json not found. Run merge_rank.py to write it.",
      groups: [],
    });
    renderPanel();

    const err = await screen.findByTestId("strategy-rank-error");
    expect(err.textContent).toContain("ranked.json not found");
    expect(await screen.findByTestId("strategy-card-COMBO_02_V1")).toBeTruthy();
    expect(screen.queryByTestId("strategy-rank-window")).toBeNull();
  });
});
