/**
 * @vitest-environment jsdom
 *
 * Backtest-screen fixes from the 2026-10-18 review: row selection across
 * timeframes, a stable chart trade, per-job detail state, the screener
 * handoff, the Risk $ field, LONG-only engines, returning to Passed (auto) and
 * a lost regime-book run.
 */
import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useBacktestHandoffStore } from "../store/backtestHandoffStore";
import { useBacktestJobStore } from "../store/backtestJobStore";
import { setupUser } from "../test/user";
import { BacktestPanel } from "./BacktestPanel";

// jsdom has no layout, so render every blotter row.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (opts: { count: number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, index) => ({
        index,
        start: index * 28,
        end: (index + 1) * 28,
        key: index,
        size: 28,
        lane: 0,
      })),
    getTotalSize: () => opts.count * 28,
    measureElement: () => undefined,
    scrollToIndex: () => undefined,
  }),
}));

const api = vi.hoisted(() => ({
  startBacktestJob: vi.fn(async () => ({ status: "STARTED", job: { status: "running", job_id: "x", rows: [] } })),
  fetchRegimeBacktest: vi.fn(async (): Promise<unknown> => ({ status: "idle" })),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchResearchOhlcvRange: vi.fn(async () => ({ status: "OK", rows: [] })),
    fetchStrategyRank: vi.fn(async () => null),
    fetchCapBookAgents: vi.fn(async () => ({ status: "OK", poll_interval_seconds: 3, shards: [] })),
    startBacktestJob: api.startBacktestJob,
    fetchBacktestJobStatus: vi.fn(async () => ({ status: "idle", job_id: null, rows: [] })),
    cancelBacktestJob: vi.fn(async () => ({ status: "idle" })),
    fetchResearchOhlcvCandles: vi.fn(async () => ({ status: "OK", candles: [] })),
    fetchRegimeBacktest: api.fetchRegimeBacktest,
  };
});

const chartTrades = vi.hoisted(() => [] as unknown[]);
vi.mock("./BacktestTradeChart", () => ({
  BacktestTradeChart: ({ trade }: { trade: unknown }) => {
    chartTrades.push(trade);
    return null;
  },
}));

function renderPanel() {
  return render(
    <MemoryRouter>
      <BacktestPanel />
    </MemoryRouter>,
  );
}

// Compile the panel once outside the per-test budget (see tradeAudit test).
beforeAll(() => {
  const { container } = renderPanel();
  within(container).getByRole("tab", { name: "Long trades" });
  cleanup();
  vi.clearAllMocks();
});

type T = Record<string, unknown>;

function trade(no: number, extra: T = {}): T {
  return {
    trade_no: no,
    symbol: "BTCUSDT",
    timeframe: "1h",
    direction: "LONG",
    entry_price: 100,
    stop_price: 99,
    tp1: 102,
    exit_price: 101,
    outcome: "TP1",
    net_pnl_usd: 5,
    gross_pnl_usd: 6,
    fee_total_usd: -1,
    signal_time: `2025-01-0${no}T00:00:00+00:00`,
    exit_time: `2025-01-0${no}T04:00:00+00:00`,
    ...extra,
  };
}

function row(timeframe: string, trades: T[]): T {
  return {
    combination_id: "COMBO_01",
    symbol: "BTCUSDT",
    timeframe,
    direction: "LONG",
    sample_size: trades.length,
    average_R: 0.1,
    pnl_usd: 6 * trades.length,
    pnl_usd_net: 5 * trades.length,
    fees_usd: -trades.length,
    risk_usd: 20,
    bars_loaded: 100,
    period_start: "2025-01-01T00:00:00+00:00",
    period_end: "2025-03-31T23:00:00+00:00",
    trades: trades.map((t) => ({ ...t, timeframe })),
  };
}

function job(jobId: string, rows: T[], timeframes: string[] = ["1h"]): T {
  return {
    status: "done",
    job_id: jobId,
    symbols: ["BTCUSDT"],
    timeframes,
    direction: "LONG",
    strategy_id: "COMBO_01",
    combination_id: "COMBO_01",
    principal_usd: 1000,
    production_comparable: false,
    total_cells: rows.length,
    done_cells: rows.length,
    rows,
  };
}

const strategySelect = (container: HTMLElement) =>
  within(container).getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;

const tfButton = (container: HTMLElement, tf: string) =>
  within(container).getByTitle(new RegExp(`^${tf} —`));

const selectedTfs = (container: HTMLElement) =>
  ["5m", "15m", "1h", "4h", "1d"].filter((tf) => {
    const el = within(container).queryByTitle(new RegExp(`^${tf} —`));
    return el != null && el.className.includes("border-terminal-accent");
  });

const output = (container: HTMLElement) =>
  within(container.querySelector("#backtest-output") as HTMLElement);

/** The blotter heading names the selected cell. */
const blotterHeading = (container: HTMLElement) =>
  Array.from(container.querySelectorAll("#backtest-output h2"))
    .map((h) => h.textContent || "")
    .find((t) => /blotter/i.test(t)) ?? "";

describe("Backtest screen review fixes (2026-10-18)", () => {
  afterEach(() => cleanup());
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    chartTrades.length = 0;
    useBacktestHandoffStore.setState({ symbols: [], revision: 0 });
    api.fetchRegimeBacktest.mockReset();
    api.fetchRegimeBacktest.mockResolvedValue({ status: "idle" });
    useBacktestJobStore.setState({
      job: null,
      request: null,
      foreignJob: false,
      ownJobId: null,
      error: null,
      busyJobId: null,
      active: false,
      starting: false,
      _gen: 0,
    });
  });

  it("clicking the second timeframe of a coin keeps that timeframe selected", () => {
    useBacktestJobStore.setState({
      job: job("j1", [row("15m", [trade(1)]), row("1h", [trade(1)])], ["15m", "1h"]) as never,
    });
    const { container } = renderPanel();
    expect(blotterHeading(container)).toMatch(/15m/);

    const cell = output(container)
      .getAllByText("1h", { selector: "td" })
      .find((td) => td.closest("tr")?.className.includes("cursor-pointer"));
    fireEvent.click(cell!.closest("tr")!);

    expect(blotterHeading(container)).toMatch(/1h/);
    expect(blotterHeading(container)).not.toMatch(/15m/);
  });

  it("an unrelated form edit does not hand the chart a new trade object", () => {
    useBacktestJobStore.setState({ job: job("j1", [row("1h", [trade(1)])]) as never });
    const { container } = renderPanel();
    const before = chartTrades.length;
    expect(before).toBeGreaterThan(0);

    fireEvent.change(within(container).getByLabelText(/Leverage/i), { target: { value: "3" } });

    const after = chartTrades.slice(before);
    for (const t of after) expect(t).toBe(chartTrades[before - 1]);
  });

  it("a new run on the same cell opens its first trade on the chart", () => {
    useBacktestJobStore.setState({ job: job("j1", [row("1h", [])]) as never });
    const { container } = renderPanel();
    expect(chartTrades.length).toBe(0);

    act(() => {
      useBacktestJobStore.setState({ job: job("j2", [row("1h", [trade(1), trade(2)])]) as never });
    });

    expect(chartTrades.length).toBeGreaterThan(0);
    expect((chartTrades.at(-1) as T).trade_no).toBe(1);
    expect(output(container).queryByText(/No closed trades/)).toBeNull();
  });

  it("screener coins are applied once, not again on the next visit", () => {
    useBacktestHandoffStore.getState().queue(["ETHUSDT"]);
    const first = renderPanel();
    expect(within(first.container).getByText(/1 screener coin loaded/)).toBeTruthy();
    expect(useBacktestHandoffStore.getState().symbols).toEqual([]);
    cleanup();

    const second = renderPanel();
    expect(within(second.container).queryByText(/screener coin loaded/)).toBeNull();
  });

  it("Risk $ can be cleared while typing and keeps Risk % in step", () => {
    const { container } = renderPanel();
    const root = within(container);
    const riskUsd = root.getByLabelText(/Risk \$ per trade/i) as HTMLInputElement;

    fireEvent.change(riskUsd, { target: { value: "" } });
    expect(riskUsd.value).toBe("");
    fireEvent.change(riskUsd, { target: { value: "50" } });
    expect(riskUsd.value).toBe("50");
    const principal = Number((root.getByLabelText(/Principal \$/i) as HTMLInputElement).value);
    const riskPct = Number((root.getByLabelText(/Risk %/i) as HTMLInputElement).value);
    expect(riskPct).toBeCloseTo((50 / principal) * 100, 3);
  });

  it("a LONG-only engine offers LONG only", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    await user.click(within(container).getByRole("tab", { name: "Long trades" }));
    await user.selectOptions(strategySelect(container), "LARGE_CAP_SWEEP_CHOCH");
    const dirs = within(within(container).getByTestId("engine-directions"));
    expect(dirs.getByRole("button", { name: "Direction LONG" })).toBeTruthy();
    expect(dirs.queryByRole("button", { name: "Direction SHORT" })).toBeNull();
  });

  it("returning to Passed (auto) in the same book resets the timeframe to 1h", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    await user.click(within(container).getByRole("tab", { name: "Long trades" }));
    await user.selectOptions(strategySelect(container), "CANDLE12_V2");
    await user.click(tfButton(container, "15m"));
    if (selectedTfs(container).includes("1h")) await user.click(tfButton(container, "1h"));
    expect(selectedTfs(container)).toEqual(["15m"]);

    await user.selectOptions(strategySelect(container), "PASSED_AUTO");
    expect(selectedTfs(container)).toEqual(["1h"]);
  });

  it("says so when the regime-book status keeps failing", async () => {
    vi.useFakeTimers();
    try {
      api.fetchRegimeBacktest.mockResolvedValueOnce({
        status: "running",
        job_id: "regime-1",
        phase: "running",
        current: "BTCUSDT 1/1",
        symbols: ["BTCUSDT"],
        total_cells: 1,
        done_cells: 0,
        pct: 10,
        progress_percent: 10,
        started_at: new Date().toISOString(),
      });
      api.fetchRegimeBacktest.mockRejectedValue(new Error("Failed to fetch"));
      const { container } = renderPanel();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500);
      });
      expect(within(container).queryByTestId("regime-poll-error")).toBeNull();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      expect(within(container).getByTestId("regime-poll-error").textContent).toMatch(
        /Lost contact with the regime-book run/,
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
