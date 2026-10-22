/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  closeTrade,
  fetchMyTrades,
  fetchPrice,
  logTrade,
  type ManualTrade,
  type MyTradesView,
  type TradeStats,
} from "../screenerTool/client";
import { MyTradesPage, previewLevels } from "./MyTradesPage";

vi.mock("../screenerTool/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../screenerTool/client")>();
  return {
    ...actual,
    fetchMyTrades: vi.fn(),
    fetchPrice: vi.fn(),
    logTrade: vi.fn(),
    closeTrade: vi.fn(),
    voidTrade: vi.fn(),
  };
});

const EMPTY: TradeStats = {
  trades: 0,
  wins: 0,
  losses: 0,
  win_rate: null,
  avg_r: null,
  expectancy_r: null,
  expectancy_usd: null,
  total_r: 0,
  net_pnl_usd: 0,
  profit_factor: null,
  profit_factor_r: null,
  max_drawdown_r: 0,
  max_drawdown_usd: 0,
  longest_losing_streak: 0,
};

function trade(over: Partial<ManualTrade>): ManualTrade {
  return {
    row_id: 1,
    trade_id: "t1",
    version: 1,
    valid_from: "2026-10-22T12:00:00+00:00",
    action: "OPEN",
    voided: false,
    status: "OPEN",
    symbol: "ETHUSDT",
    side: "LONG",
    timeframe: "1h",
    setup_tag: "breakout",
    entry_price: 2500,
    stop_price: 2450,
    targets: [2600],
    targets_r: [2],
    quantity: 0.5,
    risk_usd: 25,
    size_input: "risk_usd",
    opened_at: "2026-10-22T10:00:00+00:00",
    notes: null,
    screenshot_path: null,
    exit_price: null,
    exit_at: null,
    exit_reason: null,
    gross_pnl_usd: null,
    fees_usd: null,
    slippage_usd: null,
    costs_usd: null,
    net_pnl_usd: null,
    gross_r: null,
    r_multiple: null,
    stop_distance_pct: 2,
    edit_note: null,
    ...over,
  };
}

function view(over: Partial<MyTradesView> = {}): MyTradesView {
  return {
    trades: [],
    counts: { open: 0, closed: 0, void: 0 },
    stats: {
      overall: EMPTY,
      by_setup: [],
      by_coin: [],
      by_month: [],
      honesty: {
        trades: 0,
        min_trades: 30,
        judgeable: false,
        text: "too few trades to judge",
        expectancy_r: null,
        ci_low: null,
        ci_high: null,
        lower_bound_above_zero: null,
        resamples: 10000,
        seed: 20261022,
        confidence: 0.9,
      },
    },
    cost_model: { name: "D144", taker_fee_rate: 0.0005, slippage_rate: 0.0002, thin_coins: [], formula: "" },
    storage: { table: "manual_trades", key: ["trade_id", "version"], append_only_guard: true },
    disclaimer: "Paper journal and display only: no orders, no exchange keys. Not a buy or sell signal.",
    ...over,
  };
}

describe("My Trades page", () => {
  beforeEach(() => {
    vi.mocked(fetchMyTrades).mockResolvedValue(view());
    vi.mocked(fetchPrice).mockResolvedValue({ symbol: "ETHUSDT", price: 2512.5 });
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("previews risk, qty, cost and target R from entry and stop", () => {
    const p = previewLevels({ symbol: "ETHUSDT", side: "LONG", entry: 100, stop: 98, targets: [104], sizeMode: "risk_usd", size: 20 });
    expect(p.ok).toBe(true);
    expect(p.qty).toBeCloseTo(10);
    expect(p.stopPct).toBeCloseTo(2);
    expect(p.costR).toBeCloseTo(0.07);
    expect(p.targetsR?.[0]).toBeCloseTo(2);
    const thin = previewLevels({ symbol: "ARBUSDT", side: "SHORT", entry: 1, stop: 1.01, targets: [], sizeMode: "quantity", size: 100 });
    expect(thin.risk).toBeCloseTo(1);
    expect(thin.costR).toBeCloseTo(0.2);
    expect(previewLevels({ symbol: "X", side: "LONG", entry: 100, stop: 101, targets: [], sizeMode: "risk_usd", size: 1 }).ok).toBe(false);
  });

  it("logs a trade with the current price pre-filled", async () => {
    vi.mocked(logTrade).mockResolvedValue({ trade: trade({}) });
    render(<MyTradesPage />);
    fireEvent.change(screen.getByLabelText("Coin"), { target: { value: "ethusdt" } });
    fireEvent.focusOut(screen.getByLabelText("Coin"));
    await waitFor(() => expect(screen.getByLabelText("Entry")).toHaveValue("2512.5"));
    expect(fetchPrice).toHaveBeenCalledWith("ETHUSDT");
    fireEvent.change(screen.getByLabelText("Entry"), { target: { value: "2500" } });
    fireEvent.change(screen.getByLabelText("Stop"), { target: { value: "2450" } });
    fireEvent.change(screen.getByLabelText("Targets"), { target: { value: "2600, 2700" } });
    fireEvent.change(screen.getByLabelText("Size"), { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText("Setup tag"), { target: { value: "breakout" } });
    expect(screen.getByTestId("log-preview")).toHaveTextContent("Risk $25.00");
    expect(screen.getByTestId("log-preview")).toHaveTextContent("targets 2.00 R, 4.00 R");
    fireEvent.click(screen.getByRole("button", { name: "Log trade" }));
    await waitFor(() => expect(logTrade).toHaveBeenCalledTimes(1));
    expect(vi.mocked(logTrade).mock.calls[0][0]).toEqual({
      symbol: "ETHUSDT",
      side: "LONG",
      entry_price: 2500,
      stop_price: 2450,
      targets: [2600, 2700],
      risk_usd: 25,
      setup_tag: "breakout",
      timeframe: undefined,
      notes: undefined,
      screenshot_path: undefined,
    });
    expect(await screen.findByText(/Logged ETHUSDT LONG/)).toBeInTheDocument();
    expect(fetchMyTrades).toHaveBeenCalledTimes(2);
  });

  it("logs an exit for an open trade", async () => {
    vi.mocked(fetchMyTrades).mockResolvedValue(view({ trades: [trade({})], counts: { open: 1, closed: 0, void: 0 } }));
    vi.mocked(closeTrade).mockResolvedValue({ trade: trade({ status: "CLOSED", version: 2 }) });
    render(<MyTradesPage />);
    const form = await screen.findByTestId("exit-form-t1");
    fireEvent.change(within(form).getByLabelText("Exit price ETHUSDT"), { target: { value: "2550" } });
    fireEvent.change(within(form).getByLabelText("Exit time ETHUSDT"), { target: { value: "2026-10-22T15:00" } });
    fireEvent.change(within(form).getByLabelText("Exit reason ETHUSDT"), { target: { value: "target" } });
    fireEvent.click(within(form).getByRole("button", { name: "Log exit" }));
    await waitFor(() => expect(closeTrade).toHaveBeenCalledTimes(1));
    const [id, body] = vi.mocked(closeTrade).mock.calls[0];
    expect(id).toBe("t1");
    expect(body.exit_price).toBe(2550);
    expect(body.exit_reason).toBe("target");
    expect(body.exit_at).toBe(new Date("2026-10-22T15:00").toISOString());
    expect(await screen.findByText("Exit logged for ETHUSDT.")).toBeInTheDocument();
  });

  it("shows closed trades, stats per group and the too-few honesty line", async () => {
    const closed = trade({
      status: "CLOSED",
      version: 2,
      action: "CLOSE",
      exit_price: 2550,
      exit_reason: "target",
      gross_pnl_usd: 25,
      costs_usd: 1.76,
      net_pnl_usd: 23.24,
      r_multiple: 0.9296,
    });
    const s: TradeStats = {
      ...EMPTY,
      trades: 1,
      wins: 1,
      win_rate: 1,
      avg_r: 0.9296,
      expectancy_r: 0.9296,
      expectancy_usd: 23.24,
      total_r: 0.9296,
      net_pnl_usd: 23.24,
    };
    vi.mocked(fetchMyTrades).mockResolvedValue(
      view({
        trades: [closed],
        counts: { open: 0, closed: 1, void: 0 },
        stats: {
          ...view().stats,
          overall: s,
          by_setup: [{ ...s, key: "breakout" }],
          by_coin: [{ ...s, key: "ETHUSDT" }],
          by_month: [{ ...s, key: "2026-10" }],
          honesty: { ...view().stats.honesty, trades: 1 },
        },
      }),
    );
    render(<MyTradesPage />);
    const row = await screen.findByTestId("closed-trade-row");
    expect(within(row).getByTestId("closed-r")).toHaveTextContent("+0.93 R");
    expect(row).toHaveTextContent("+23.24");
    const overall = screen.getByTestId("stats-overall");
    expect(within(overall).getAllByTestId("stats-row")[0]).toHaveTextContent("Overall1100.0%+0.93 R");
    expect(within(overall).getByText("breakout")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Per coin" }));
    expect(within(overall).getByText("ETHUSDT")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Per month" }));
    expect(within(overall).getByText("2026-10")).toBeInTheDocument();
    const honesty = screen.getByTestId("honesty-panel");
    expect(honesty).toHaveTextContent("too few trades to judge");
    expect(honesty).toHaveTextContent("1 of 30 closed trades");
  });

  it("shows the bootstrap interval from 30 trades", async () => {
    vi.mocked(fetchMyTrades).mockResolvedValue(
      view({
        stats: {
          ...view().stats,
          honesty: {
            ...view().stats.honesty,
            trades: 30,
            judgeable: true,
            text: "Expectancy +0.230 R per trade, 90% CI -0.130 to +0.617 R: the lower bound is not above 0 after costs.",
            expectancy_r: 0.23,
            ci_low: -0.13,
            ci_high: 0.617,
            lower_bound_above_zero: false,
          },
        },
      }),
    );
    render(<MyTradesPage />);
    const honesty = await screen.findByTestId("honesty-panel");
    await waitFor(() => expect(honesty).toHaveTextContent("90% CI -0.130 to +0.617 R"));
    expect(honesty).toHaveTextContent("not above 0 after costs");
    expect(honesty).toHaveTextContent("10,000 resamples, seed 20261022");
  });

  it("offers CSV export of current versions and the full history, and states the cost formula", async () => {
    render(<MyTradesPage />);
    const exp = await screen.findByTestId("export");
    expect(within(exp).getByText("Export CSV (current versions)")).toHaveAttribute("href", "/api/my-trades/export.csv");
    expect(within(exp).getByText("Export CSV (full edit history)")).toHaveAttribute(
      "href",
      "/api/my-trades/export.csv?history=1",
    );
    expect(screen.getByTestId("my-trades-formula")).toHaveTextContent("0.05% taker + 0.02% slippage");
    expect(await screen.findByText(/Stored in table manual_trades/)).toBeInTheDocument();
  });
});
