/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PaperOpportunity } from "../api/client";
import { fetchScreenerContext, type ContextRow, type ScreenerContext } from "../screenerTool/client";
import { TradeChancesTable } from "./TradeChancesTable";

vi.mock("../screenerTool/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../screenerTool/client")>();
  return { ...actual, fetchScreenerContext: vi.fn() };
});

const BADGE = "Pattern untested / no edge found (FINAL_VERDICT)";

function opp(symbol: string, entry: number, stop: number): PaperOpportunity {
  return {
    symbol,
    tier: "NEAR",
    chance: "Near entry",
    status: "NO_SETUP",
    direction: "LONG",
    timeframe: "15m",
    setup_trend: "BULLISH",
    bos: "BULLISH_BOS",
    bos_state: "CONFIRMED",
    impulse: null,
    pullback: null,
    retest: null,
    entry_price: entry,
    stop_price: stop,
    tp1_price: entry * 1.02,
    rr_pass: false,
    pass_count: 4,
    missing: [],
    already_open: false,
  };
}

function htf(trend: "UP" | "DOWN") {
  const st = (tf: string) => ({
    timeframe: tf,
    status: "OK",
    bars: 300,
    last_closed_bar: "2026-10-22T08:00:00+00:00",
    close_vs_ema50: trend,
    ema20_vs_ema50: trend,
    trend,
  });
  return { "4h": st("4h"), "1d": st("1d") };
}

function row(symbol: string, costR: number, stopPct: number, trend: "UP" | "DOWN", thin = false): ContextRow {
  return {
    symbol,
    direction: "LONG",
    cost: {
      status: "OK",
      slippage_bps: thin ? 5 : 2,
      thin_coin: thin,
      round_trip_pct: thin ? 0.2 : 0.14,
      stop_distance_pct: stopPct,
      cost_r: costR,
      cost_r_max: 0.25,
      cost_ok: costR <= 0.25,
      cost_too_high: costR > 0.25,
    },
    htf: htf(trend),
    htf_agrees: trend === "UP",
    badge: {
      setup_type: "TREND_BOS",
      setup_label: "Trend + BOS",
      verdict: "NO_EDGE_FOUND",
      text: BADGE,
      detail: "no edge after costs",
      not_a_signal: "Research display only: not a buy or sell signal.",
    },
  };
}

const CTX: ScreenerContext = {
  read_only: true,
  as_of: "2026-10-22T13:00:00+00:00",
  rows: [row("ETHUSDT", 0.07, 2.0, "UP"), row("SOLUSDT", 0.467, 0.3, "UP"), row("ARBUSDT", 0.2, 1.0, "DOWN", true)],
  btc: {
    regime: { trend: "BEARISH", as_of_bar: "2026-10-21T00:00:00+00:00", bars: 500, short_history: false, status: "OK" },
    funding: {
      status: "NO_DATA",
      text: "funding: no data",
      mean_72h: null,
      state: null,
      latest_settlement: null,
      settlements: 0,
      share_above_base: null,
      reason: "never_fetched",
    },
  },
  rules: { cost: "", cost_r_max: 0.25, thin_coins: [], htf: "", badge: BADGE, not_a_signal: "" },
};

const ROWS = [opp("SOLUSDT", 100, 99.7), opp("ETHUSDT", 2500, 2450), opp("ARBUSDT", 1, 0.99)];

function renderTable() {
  return render(<TradeChancesTable rows={ROWS} empty="none" autoOn={false} watcherOwns legacyAutoOn={false} />);
}

function symbols(): string[] {
  return screen.getAllByTestId("chance-row").map((tr) => within(tr).getAllByRole("cell")[1].textContent ?? "");
}

describe("Trade chances: cost, HTF, BTC and research columns", () => {
  beforeEach(() => {
    vi.mocked(fetchScreenerContext).mockResolvedValue(CTX);
  });
  afterEach(() => {
    cleanup();
    vi.mocked(fetchScreenerContext).mockReset();
  });

  it("shows stop distance, cost as R, COST TOO HIGH in grey, HTF arrows, the badge and the BTC line", async () => {
    renderTable();
    await waitFor(() => expect(screen.getAllByTestId("chance-cost")[0]).toHaveTextContent("R"));
    const req = vi.mocked(fetchScreenerContext).mock.calls[0][0];
    expect(req.map((r) => [r.symbol, r.entry_price, r.stop_price])).toEqual([
      ["SOLUSDT", 100, 99.7],
      ["ETHUSDT", 2500, 2450],
      ["ARBUSDT", 1, 0.99],
    ]);
    const [sol, eth, arb] = screen.getAllByTestId("chance-row");
    expect(sol).toHaveAttribute("data-cost-too-high", "1");
    expect(sol.className).toContain("opacity-50");
    expect(within(sol).getByTestId("chance-cost")).toHaveTextContent("0.47 R");
    expect(within(sol).getByText("COST TOO HIGH")).toBeInTheDocument();
    expect(within(sol).getByTestId("chance-stop-dist")).toHaveTextContent("0.30%");
    expect(eth).toHaveAttribute("data-cost-too-high", "0");
    expect(within(eth).getByTestId("chance-cost")).toHaveTextContent("0.07 R");
    expect(within(eth).getByTestId("htf-4h")).toHaveTextContent("4h ↑↑");
    expect(within(arb).getByTestId("chance-cost")).toHaveTextContent("0.20 R · 5bp");
    expect(within(arb).getByTestId("htf-1d")).toHaveTextContent("1d ↓↓");
    expect(screen.getAllByText(BADGE)).toHaveLength(3);
    expect(screen.queryByText(/\bbuy\b(?! or sell)/i)).toBeNull();
    const btc = screen.getByTestId("btc-context");
    expect(btc).toHaveTextContent("BTC regime: BEARISH");
    expect(within(btc).getByTestId("btc-funding")).toHaveTextContent("funding: no data");
  });

  it("filters by cost OK, HTF agrees and coin", async () => {
    renderTable();
    await waitFor(() => expect(screen.getAllByTestId("chance-cost")[0]).toHaveTextContent("R"));
    fireEvent.click(screen.getByLabelText(/Cost OK only/));
    expect(symbols()).toEqual(["ETHUSDT", "ARBUSDT"]);
    fireEvent.click(screen.getByLabelText(/HTF agrees only/));
    expect(symbols()).toEqual(["ETHUSDT"]);
    fireEvent.click(screen.getByLabelText(/Cost OK only/));
    fireEvent.click(screen.getByLabelText(/HTF agrees only/));
    fireEvent.change(screen.getByLabelText("Filter coin"), { target: { value: "arb" } });
    expect(symbols()).toEqual(["ARBUSDT"]);
    fireEvent.change(screen.getByLabelText("Filter coin"), { target: { value: "zzz" } });
    expect(screen.getByText("No rows match the filters.")).toBeInTheDocument();
  });

  it("sorts by cost and coin", async () => {
    renderTable();
    await waitFor(() => expect(screen.getAllByTestId("chance-cost")[0]).toHaveTextContent("R"));
    expect(symbols()).toEqual(["SOLUSDT", "ETHUSDT", "ARBUSDT"]);
    fireEvent.change(screen.getByLabelText("Sort chances"), { target: { value: "cost" } });
    expect(symbols()).toEqual(["ETHUSDT", "ARBUSDT", "SOLUSDT"]);
    fireEvent.change(screen.getByLabelText("Sort chances"), { target: { value: "coin" } });
    expect(symbols()).toEqual(["ARBUSDT", "ETHUSDT", "SOLUSDT"]);
  });

  it("keeps the table when the context call fails", async () => {
    vi.mocked(fetchScreenerContext).mockRejectedValue(new Error("API down"));
    renderTable();
    expect(await screen.findByText(/Screener context unavailable: API down/)).toBeInTheDocument();
    expect(screen.getAllByTestId("chance-row")).toHaveLength(3);
    expect(screen.getAllByTestId("opportunity-action")).toHaveLength(3);
  });
});
