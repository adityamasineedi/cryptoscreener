/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GridTradingPanel } from "./GridTradingPanel";

vi.mock("./client", () => ({
  fetchGridStatus: vi.fn(),
  runGridBacktest: vi.fn(),
  runGridScan: vi.fn(),
  runGridValidation: vi.fn(),
}));

vi.mock("./GridChart", () => ({
  GridChart: () => <div data-testid="grid-chart" />,
}));

import { fetchGridStatus, runGridBacktest, runGridScan } from "./client";

afterEach(() => {
  cleanup();
});

const status = vi.mocked(fetchGridStatus);
const backtest = vi.mocked(runGridBacktest);
const scan = vi.mocked(runGridScan);

const recommended = {
  market: "binance_usdt_m_futures",
  rows: [
    {
      symbol: "BTCUSDT",
      timeframe: "1h",
      score: 42,
      eligible: true,
      reason: null,
      market: "binance_usdt_m_futures",
      range_low: 100,
      range_high: 110,
      stop_price: 99,
    },
  ],
};

describe("GridTradingPanel", () => {
  beforeEach(() => {
    status.mockReset();
    backtest.mockReset();
    scan.mockReset();
    scan.mockResolvedValue(recommended);
  });

  it("stays hidden when the flag is off", async () => {
    status.mockResolvedValue(null);
    render(<GridTradingPanel />);
    expect(await screen.findByText(/grid trading is off/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run backtest" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Range low")).not.toBeInTheDocument();
  });

  it("runs a futures backtest from the recommended OHLCV range", async () => {
    status.mockResolvedValue({
      enabled: true,
      market: "binance_usdt_m_futures",
      mode: "backtest",
      live: false,
      paper: false,
    });
    backtest.mockResolvedValue({
      summary: {
        market: "binance_usdt_m_futures",
        accepted: true,
        error: null,
        halted: null,
        fill_count: 2,
        touch_count: 2,
        fill_rate: 1,
        round_trips: 1,
        max_drawdown: 0.01,
        realized_pnl: 10,
        liquidated_equity: 1010,
        return: 0.01,
        buy_and_hold_return: 0.02,
        excess_return: -0.01,
        bar_count: 2,
      },
      levels: [100, 110],
      fills: [
        { time: 1, side: "buy", slot: 0, level: 100, price: 100, qty: 1, fee: 0.1 },
        { time: 2, side: "sell", slot: 0, level: 110, price: 110, qty: 1, fee: 0.1 },
      ],
      equity_curve: [
        { time: 1, equity: 1000, drawdown: 0 },
        { time: 2, equity: 1010, drawdown: 0 },
      ],
      candles: [
        { time: 1_700_000_000, open: 105, high: 106, low: 104, close: 105 },
        { time: 1_700_003_600, open: 105, high: 112, low: 99, close: 108 },
      ],
      buy_and_hold: { equity: 1020, return: 0.02 },
    });
    const user = userEvent.setup();
    render(<GridTradingPanel />);
    expect(await screen.findByRole("button", { name: "Run backtest" })).toBeInTheDocument();
    expect(await screen.findByTestId("grid-large-caps")).toBeInTheDocument();
    expect(screen.getByText("Large caps")).toBeInTheDocument();
    expect(await screen.findByText("BTC")).toBeInTheDocument();
    expect(screen.queryByLabelText("Range low")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Grid count")).not.toBeInTheDocument();
    expect(screen.getByText(/binance usdt-m futures/i)).toBeInTheDocument();
    expect(screen.getByText(/not financial advice/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run backtest" }));
    expect(scan).toHaveBeenCalled();
    expect(backtest).toHaveBeenCalledWith(
      expect.objectContaining({
        symbol: "BTCUSDT",
        range_low: 100,
        range_high: 110,
        stop_price: 99,
        grid_type: "geometric",
        grid_count: 8,
      }),
      { limit: 400 },
    );
    expect(await screen.findByText(/Accepted/)).toBeInTheDocument();
    expect(screen.getByText("buy")).toBeInTheDocument();
    expect(screen.getByTestId("grid-trade-blotter")).toBeInTheDocument();
    expect(screen.getAllByText("100.0000").length).toBeGreaterThan(0);
    expect(screen.getAllByText("110.0000").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Download fills CSV" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Equity curve" })).toBeInTheDocument();
  });

  it("sends leverage and the open-grid cap and shows the risk counts", async () => {
    status.mockResolvedValue({
      enabled: true,
      market: "binance_usdt_m_futures",
      mode: "backtest",
      live: false,
      paper: false,
    });
    backtest.mockResolvedValue({
      summary: {
        market: "binance_usdt_m_futures",
        accepted: true,
        error: null,
        halted: "take_profit",
        fill_count: 2,
        touch_count: 3,
        fill_rate: 0.67,
        round_trips: 1,
        max_drawdown: 0.02,
        realized_pnl: 20,
        leverage: 2,
        max_open_grids: 5,
        worst_case_liquidation_price: 61.25,
        stop_hits: 0,
        take_profit_exits: 1,
        liquidations: 0,
        drawdown_kills: 0,
        blocked_buys: 1,
        liquidated_equity: 1020,
        return: 0.02,
        buy_and_hold_return: 0.04,
        excess_return: -0.02,
        bar_count: 2,
      },
      levels: [100, 110],
      fills: [],
      equity_curve: [],
      candles: [],
      buy_and_hold: { equity: 1040, return: 0.04 },
    });
    const user = userEvent.setup();
    render(<GridTradingPanel />);
    await screen.findByRole("button", { name: "Run backtest" });
    await user.selectOptions(screen.getByLabelText("Leverage"), "2");
    await user.clear(screen.getByLabelText("Max open grids"));
    await user.type(screen.getByLabelText("Max open grids"), "5");
    await user.click(screen.getByRole("button", { name: "Run backtest" }));
    expect(backtest).toHaveBeenCalledWith(
      expect.objectContaining({ leverage: 2, max_open_grids: 5, exit_above_range: true }),
      { limit: 400 },
    );
    expect(await screen.findByText("Stop hits")).toBeInTheDocument();
    expect(screen.getByText("2x · 5 open max")).toBeInTheDocument();
    expect(screen.getByText("61.2500")).toBeInTheDocument();
    expect(screen.getByText("Take-profit exits")).toBeInTheDocument();
  });

  it("shows funding paid and funding coverage", async () => {
    status.mockResolvedValue({
      enabled: true,
      market: "binance_usdt_m_futures",
      mode: "backtest",
      live: false,
      paper: false,
    });
    backtest.mockResolvedValue({
      summary: {
        market: "binance_usdt_m_futures",
        accepted: true,
        error: null,
        halted: null,
        fill_count: 1,
        touch_count: 1,
        fill_rate: 1,
        round_trips: 0,
        max_drawdown: 0,
        realized_pnl: 0,
        funding_paid: 1.25,
        funding_coverage_pct: 0.8,
        liquidated_equity: 1000,
        return: 0,
        buy_and_hold_return: 0,
        excess_return: 0,
        bar_count: 2,
      },
      levels: [100, 110],
      fills: [],
      equity_curve: [
        { time: 1, equity: 1000, drawdown: 0 },
        { time: 2, equity: 998.75, drawdown: 0 },
      ],
      candles: [
        { time: 1_700_000_000, open: 105, high: 106, low: 104, close: 105 },
        { time: 1_700_003_600, open: 105, high: 106, low: 104, close: 105 },
      ],
      buy_and_hold: { equity: 1000, return: 0 },
    });
    const user = userEvent.setup();
    render(<GridTradingPanel />);
    await user.click(await screen.findByRole("button", { name: "Run backtest" }));
    expect(await screen.findByText("Funding paid")).toBeInTheDocument();
    expect(screen.getByText("1.2500")).toBeInTheDocument();
    expect(screen.getByText("Funding coverage")).toBeInTheDocument();
    expect(screen.getByText("80.00%")).toBeInTheDocument();
  });

  it("labels pre-funding PnL when funding is unavailable", async () => {
    status.mockResolvedValue({
      enabled: true,
      market: "binance_usdt_m_futures",
      mode: "backtest",
      live: false,
      paper: false,
    });
    backtest.mockResolvedValue({
      summary: {
        market: "binance_usdt_m_futures",
        accepted: true,
        error: null,
        halted: null,
        fill_count: 0,
        touch_count: 0,
        fill_rate: 0,
        round_trips: 0,
        max_drawdown: 0,
        realized_pnl: 4,
        unrealized_pnl: 1,
        net_pnl: null,
        pnl_before_funding: 5,
        funding_status: "unavailable",
        funding_paid: 0,
        warnings: ["Funding is unavailable. No settlement history covers this symbol and window."],
        liquidated_equity: 1005,
        return: 0.005,
        buy_and_hold_return: 0,
        excess_return: 0.005,
        bar_count: 2,
      },
      levels: [100, 110],
      fills: [],
      equity_curve: [
        { time: 1, equity: 1000, drawdown: 0 },
        { time: 2, equity: 1005, drawdown: 0 },
      ],
      candles: [
        { time: 1_700_000_000, open: 105, high: 106, low: 104, close: 105 },
        { time: 1_700_003_600, open: 105, high: 106, low: 104, close: 105 },
      ],
      warnings: ["Funding is unavailable. No settlement history covers this symbol and window."],
      buy_and_hold: { equity: 1000, return: 0 },
    });
    const user = userEvent.setup();
    render(<GridTradingPanel />);
    await user.click(await screen.findByRole("button", { name: "Run backtest" }));
    expect(await screen.findByText("PnL before funding")).toBeInTheDocument();
    expect(screen.getByText("5.0000")).toBeInTheDocument();
    expect(screen.getByText(/Funding is unavailable/)).toBeInTheDocument();
    expect(screen.queryByText("Net PnL")).not.toBeInTheDocument();
  });
});
