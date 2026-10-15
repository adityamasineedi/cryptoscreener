/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJob, fetchJobs, fetchLabConfig, startRun, type Job, type LabConfig } from "./client";
import { RegimeLabPanel } from "./RegimeLabPanel";

vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return { ...actual, fetchLabConfig: vi.fn(), fetchJob: vi.fn(), fetchJobs: vi.fn(), startRun: vi.fn() };
});
vi.mock("./EquityChart", () => ({ EquityChart: () => <div data-testid="equity-stub" /> }));

const OFF = { regime_method: "threshold", chop_filter: false, heat_cap: 0, wf_selection: "best" };
const REC = { regime_method: "vote", chop_filter: true, heat_cap: 0.04, wf_selection: "plateau" };

const CONFIG: LabConfig = {
  regimes: { UPTREND: "close > 200 SMA and ADX > 20" },
  routing: { donchian: { UPTREND: ["LONG"] } },
  strategies: [{ id: "donchian", label: "Donchian breakout (trend)" }],
  tiers: {
    large: {
      name: "large", label: "Large caps", stop_atr_mult: 1.75, slippage: 0.0005, risk_pct: 0.005,
      strategies: ["donchian"], allow_short: true, stop_grid: [1.5, 2], rank_min: 1, rank_max: 5,
    },
    mid: {
      name: "mid", label: "Mid caps", stop_atr_mult: 2.75, slippage: 0.0015, risk_pct: 0.005,
      strategies: ["donchian"], allow_short: true, stop_grid: [2.5, 3], rank_min: 6, rank_max: 20,
    },
    small: {
      name: "small", label: "Small caps", stop_atr_mult: 3.5, slippage: 0.003, risk_pct: 0.0025,
      strategies: ["donchian"], allow_short: false, stop_grid: [3, 4], rank_min: 21, rank_max: 40,
    },
  },
  timeframes: ["1h", "4h", "1d"],
  defaults: {
    initial_equity: 10000, fee_rate: 0.0004, max_leverage: 2, btc_gate: true, train_months: 12, test_months: 3,
    params: { donchian_shorts: true, donchian_entry_mode: "breakout", rsi_exit_mode: "sma", retest_bars: 6 },
  },
  regime_methods: { threshold: "", hysteresis: "", vote: "" },
  safeguards: { off: OFF, recommended: REC },
  holdout_start: "2026-07-15T00:00:00+00:00",
  walk_forward_grid_size: 16,
  not_modeled: ["perpetual funding payments"],
};

const DONE: Job = {
  job_id: "abc",
  status: "done",
  progress: 1,
  stage: "done",
  error: null,
  request: {} as Job["request"],
  result: {
    summary: {
      start_equity: 10000, end_equity: 10500, net_pnl: 500, return_pct: 5, cagr_pct: 2, max_drawdown_pct: 12,
      live_pause_drawdown_pct: 18, max_drawdown_usd: 900, max_losing_streak: 6, quick_stops: 3, pnl_ex_top5: -200, sharpe: 0.4, trades: 42, win_rate: 0.45, profit_factor: 1.2, avg_r: 0.1,
      fees: 30, slippage: 40, exposure_pct: 50, days: 900, pnl_per_30d: 16.7,
    },
    counters: { blocked_chop_filter: 7 },
    monte_carlo: null,
    kill_switch_at: "2025-01-02T00:00:00+00:00",
    breakdowns: { strategy_regime_side: [{ key: "donchian · UPTREND · LONG", trades: 42, net_pnl: 500, win_rate: 0.45, avg_r: 0.1, profit_factor: 1.2, fees: 30 }] },
    regime_share: {},
    equity_curve: [],
    trades: [],
    trade_count: 42,
    universe: { symbols: ["BTCUSDT"], ranking: [], coverage: [] },
    window: { start: "2023-06-01T00:00:00+00:00", end: "2026-07-15T00:00:00+00:00", holdout_start: "2026-07-15T00:00:00+00:00" },
    effective: {
      risk_pct: 0.005, stop_atr_mult: 1.75, slippage: 0.0005, fee_rate: 0.0004, allow_short: true,
      strategies: ["donchian"], btc_gate: true, safeguards: { regime_method: "vote", daily_regime: false },
    },
  },
};

describe("RegimeLabPanel", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(fetchLabConfig).mockResolvedValue(CONFIG);
    vi.mocked(fetchJobs).mockResolvedValue({ jobs: [] });
    vi.mocked(startRun).mockResolvedValue({ job_id: "abc" });
    vi.mocked(fetchJob).mockResolvedValue(DONE);
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("sends the chosen safeguard preset and shows the finished run", async () => {
    render(<RegimeLabPanel />);
    await screen.findByText("Recommended");
    fireEvent.click(screen.getByText("Recommended"));
    fireEvent.click(screen.getByText("Run backtest"));
    await waitFor(() => expect(startRun).toHaveBeenCalled());
    const req = vi.mocked(startRun).mock.calls[0][0];
    expect(req.safeguard_preset).toBe("recommended");
    expect(req.safeguards).toEqual({});
    expect(req.include_holdout).toBe(false);
    await screen.findByText("donchian · UPTREND · LONG");
    expect(screen.getByText(/Kill switch tripped/)).toBeTruthy();
    expect(screen.getByText("blocked chop filter")).toBeTruthy();
  });

  it("sends strategy-rule flags, BTC on 1d and per-symbol slippage", async () => {
    render(<RegimeLabPanel />);
    const shorts = await screen.findByLabelText(/^Donchian shorts$/);
    fireEvent.click(shorts);
    fireEvent.click(screen.getByLabelText(/Trade BTC on 1d bars/));
    fireEvent.change(screen.getByLabelText(/Per-symbol slippage/), { target: { value: "BTCUSDT 0.05 XRPUSDT 0.15" } });
    fireEvent.click(screen.getByText("Run backtest"));
    await waitFor(() => expect(startRun).toHaveBeenCalled());
    const req = vi.mocked(startRun).mock.calls[0][0];
    expect(req.params).toEqual({ donchian_shorts: false });
    expect(req.symbol_timeframes).toEqual({ BTCUSDT: "1d" });
    expect(req.symbol_slippage?.BTCUSDT).toBeCloseTo(0.0005);
    expect(req.symbol_slippage?.XRPUSDT).toBeCloseTo(0.0015);
  });

  it("records a single safeguard change as an override", async () => {
    render(<RegimeLabPanel />);
    const box = await screen.findByLabelText(/Flat when ER low/);
    fireEvent.click(box);
    fireEvent.click(screen.getByText("Run backtest"));
    await waitFor(() => expect(startRun).toHaveBeenCalled());
    expect(vi.mocked(startRun).mock.calls[0][0].safeguards).toEqual({ chop_filter: true });
  });
});
