/** @vitest-environment jsdom */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchBook16Paper, type Book16PaperResponse } from "../api/client";
import { Book16PaperPanel } from "./Book16PaperPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, fetchBook16Paper: vi.fn() };
});

const trade = {
  trade_id: "BTCUSDT|2026-11-02 04:00:00+00:00",
  symbol: "BTCUSDT",
  side: 1,
  signal_time: "2026-11-02 00:00:00+00:00",
  entry_time: "2026-11-02 04:00:00+00:00",
  entry_price: 70000,
  exit_time: "2026-11-20 08:00:00+00:00",
  exit_price: 73000,
  exit_reason: "signal",
  qty: 0.03,
  notional: 2100,
  stop_price: 67000,
  risk_fraction: 0.005,
  price_pnl: 90,
  fees: 1.7,
  funding: -3.25,
  funding_coverage_status: "COMPLETE",
  net_pnl: 85.05,
};

function snapshot(over: Partial<Book16PaperResponse> = {}): Book16PaperResponse {
  return {
    strategy_id: "MTF_4H_16_BREAKOUT252",
    mirrors: "BOOK16_4H_FWD-1",
    symbols: ["BTCUSDT", "ETHUSDT"],
    timeframe: "4h",
    mode: "VIRTUAL_PAPER",
    live_orders: false,
    start: "2026-10-19T00:00:00+00:00",
    account_equity_start: 10000,
    risk_fraction: 0.005,
    status: "COLLECTING",
    error: null,
    updated_utc: "2026-11-21T00:02:00+00:00",
    loop_enabled: true,
    books: {
      BTCUSDT: { closed: [trade], pending_closed: [], open: null, equity: 10085.05, last_bar: "2026-11-20T20:00:00+00:00", forward_bars: 198, skipped_lev_cap: 0 },
      ETHUSDT: {
        closed: [],
        pending_closed: [],
        open: { ...trade, trade_id: "ETHUSDT|x", symbol: "ETHUSDT", exit_time: undefined, exit_price: undefined, exit_reason: undefined, net_pnl: undefined, entry_price: 3000, mark_price: 3100, unrealized_usd: 40.5 },
        equity: 10000,
        last_bar: "2026-11-20T20:00:00+00:00",
        forward_bars: 198,
        skipped_lev_cap: 0,
      },
    },
    ...over,
  };
}

describe("Book16PaperPanel", () => {
  afterEach(() => {
    cleanup();
    vi.mocked(fetchBook16Paper).mockReset();
  });

  it("shows status, both coins, the open position and closed trades with funding", async () => {
    vi.mocked(fetchBook16Paper).mockResolvedValue(snapshot());
    render(<Book16PaperPanel />);
    expect(await screen.findByTestId("book16-paper-status")).toHaveTextContent("COLLECTING");
    expect(screen.getByTestId("book16-paper")).toHaveTextContent("BOOK16_4H_FWD-1");
    expect(screen.getByTestId("book16-paper-net")).toHaveTextContent("+$85.05");
    expect(screen.getByTestId("book16-paper-open-ETHUSDT")).toHaveTextContent("+$40.50");
    expect(within(screen.getByTestId("book16-paper-BTCUSDT")).getByText(/Flat/)).toBeInTheDocument();
    const table = screen.getByTestId("book16-paper-closed");
    expect(table).toHaveTextContent("close < SMA200");
    expect(table).toHaveTextContent("−$3.25");
  });

  it("waiting and error states", async () => {
    vi.mocked(fetchBook16Paper).mockResolvedValue(
      snapshot({ status: "WAITING_FOR_FIRST_FORWARD_BAR", books: { BTCUSDT: { closed: [], pending_closed: [], open: null, equity: 10000, last_bar: null, forward_bars: 0, skipped_lev_cap: 0 }, ETHUSDT: { closed: [], pending_closed: [], open: null, equity: 10000, last_bar: null, forward_bars: 0, skipped_lev_cap: 0 } } }),
    );
    render(<Book16PaperPanel />);
    expect(await screen.findByText(/No closed trades yet/)).toBeInTheDocument();
    cleanup();
    vi.mocked(fetchBook16Paper).mockResolvedValue(snapshot({ status: "INTEGRITY", error: "INTEGRITY: booked trade x would change" }));
    render(<Book16PaperPanel />);
    expect(await screen.findByTestId("book16-paper-status")).toHaveTextContent("INTEGRITY");
    expect(screen.getByText(/would change/)).toBeInTheDocument();
  });
});
