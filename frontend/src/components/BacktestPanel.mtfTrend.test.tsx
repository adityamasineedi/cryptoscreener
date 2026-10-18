/**
 * @vitest-environment jsdom
 *
 * 4h book 16 (MTF_4H_16_BREAKOUT252) in the Backtest tab: 4h only, LONG only,
 * BTCUSDT/ETHUSDT only, holdout-guarded like the daily trend books, funding shown.
 */
import { act, cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchResearchOhlcvCandles, startBacktestJob, type BacktestJob } from "../api/client";
import { htfRequirementLabel } from "../backtest/runGuard";
import {
  BACKTEST_STRATEGY_OPTIONS,
  DAILY_TREND_OPTGROUP_LABEL,
  FUNDING_COMPLETE_PNL_LABEL,
  FUNDING_INCOMPLETE_PNL_LABEL,
  fundingPnlLabel,
  holdoutBarsPerDay,
  holdoutTailStartDay,
  isDailyTrendStrategy,
  isHoldoutGuardedStrategy,
  isMtfTrendStrategy,
  MTF_TREND_HOLDOUT_BANNER,
  MTF_TREND_LAST_DAY,
  MTF_TREND_OPTGROUP_LABEL,
  MTF_TREND_STRATEGY_IDS,
  strategyIdsForBook,
} from "../backtest/v1Config";
import { useBacktestHandoffStore } from "../store/backtestHandoffStore";
import { useBacktestJobStore } from "../store/backtestJobStore";
import { setupUser } from "../test/user";
import { BacktestPanel } from "./BacktestPanel";

const MTF_ID = "MTF_4H_16_BREAKOUT252";
const MTF_LABEL = "4h · 252-bar breakout (book 16, BTC/ETH)";

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
    scrollToIndex: () => undefined,
  }),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchResearchOhlcvRange: vi.fn(async () => ({ status: "OK", rows: [] })),
    fetchStrategyRank: vi.fn(async () => ({ status: "OK", groups: [] })),
    fetchCapBookAgents: vi.fn(async () => ({ status: "OK", poll_interval_seconds: 3, shards: [] })),
    startBacktestJob: vi.fn(async () => ({
      status: "STARTED",
      job: { status: "running", job_id: "mtf-job", rows: [] },
    })),
    fetchBacktestJobStatus: vi.fn(async () => ({ status: "idle", job_id: null, rows: [] })),
    cancelBacktestJob: vi.fn(async () => ({ status: "idle" })),
    startRegimeBacktest: vi.fn(async () => ({ status: "STARTED" })),
    fetchRegimeBacktest: vi.fn(async () => ({ status: "idle" })),
    cancelRegimeBacktest: vi.fn(async () => ({ status: "CANCELLED" })),
    fetchResearchOhlcvCandles: vi.fn(async () => ({
      status: "OK",
      symbol: "BTCUSDT",
      timeframe: "4h",
      candles: [],
    })),
  };
});

/** Props the panel hands the trade chart (the chart itself needs a canvas). */
const chartProps: Array<{ holdout?: boolean }> = [];
vi.mock("./BacktestTradeChart", () => ({
  BacktestTradeChart: (props: { holdout?: boolean }) => {
    chartProps.push(props);
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

type Root = ReturnType<typeof within>;

// Compile the large panel once outside the per-test budget (same as BacktestPanel.test.tsx).
beforeAll(() => {
  const { container } = renderPanel();
  within(container).getByRole("tab", { name: "Long trades" });
  cleanup();
  vi.clearAllMocks();
});

function strategySelect(root: Root): HTMLSelectElement {
  return root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
}

function isSelected(el: HTMLElement): boolean {
  return el.className.includes("border-terminal-accent");
}

function offeredTimeframes(root: Root): string[] {
  return ["15m", "1h", "4h", "1d"].filter((tf) => root.queryByTitle(new RegExp(`^${tf} —`)) !== null);
}

function selectedTimeframes(root: Root): string[] {
  return offeredTimeframes(root).filter((tf) => isSelected(root.getByTitle(new RegExp(`^${tf} —`))));
}

/** Coins in the run, as the chips show them (BTC, ETH, …). */
function selectedCoins(root: Root): string[] {
  return within(root.getByTestId("selected-coins"))
    .getAllByRole("button")
    .map((b) => (b.textContent || "").replace(/×.*$/, "").replace(/v1 .*$/, "").trim());
}

async function openMtf(user: ReturnType<typeof setupUser>, root: Root) {
  await user.click(root.getByRole("tab", { name: "Long trades" }));
  await user.selectOptions(strategySelect(root), MTF_ID);
  await waitFor(() => expect(selectedTimeframes(root)).toEqual(["4h"]));
}

async function runAndGetBody(user: ReturnType<typeof setupUser>, root: Root) {
  vi.mocked(startBacktestJob).mockClear();
  await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
  await waitFor(() => expect(startBacktestJob).toHaveBeenCalled());
  const body = vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0];
  // The mocked job stays "running"; clear it so the next Run click is possible.
  await act(async () => {
    useBacktestJobStore.setState({ job: null, request: null, active: false, starting: false, error: null });
  });
  await waitFor(() => root.getByRole("button", { name: /^Run backtest$/i }));
  return body;
}

function resetStores() {
  useBacktestJobStore.setState({
    job: null,
    request: null,
    foreignJob: false,
    ownJobId: null,
    error: null,
    active: false,
    starting: false,
    _gen: 0,
  });
  useBacktestHandoffStore.setState({ symbols: [], revision: 0 });
}

describe("4h book 16 config and helpers", () => {
  it("one 4h option: LONG only (no engineDirections), BTC/ETH only, long book only", () => {
    expect([...MTF_TREND_STRATEGY_IDS]).toEqual([MTF_ID]);
    const opt = BACKTEST_STRATEGY_OPTIONS.find((o) => o.id === MTF_ID);
    expect(opt?.label).toBe(MTF_LABEL);
    expect(opt?.status).toBe("Research");
    expect(opt?.direct).toBe(true);
    expect(opt?.combinationId).toBe(MTF_ID);
    expect(opt?.engineTimeframes).toEqual(["4h"]);
    expect(opt?.engineDirections).toBeUndefined();
    expect(opt?.engineSymbols).toEqual(["BTCUSDT", "ETHUSDT"]);
    expect(strategyIdsForBook("long")).toContain(MTF_ID);
    expect(strategyIdsForBook("short")).not.toContain(MTF_ID);
    expect(strategyIdsForBook("hedge")).not.toContain(MTF_ID);
    // No other strategy restricts symbols.
    for (const o of BACKTEST_STRATEGY_OPTIONS) {
      if (o.id !== MTF_ID) expect(o.engineSymbols, o.id).toBeUndefined();
    }
  });

  it("holdout helpers: guarded ids, bars per day, tail start day, cap", () => {
    expect(MTF_TREND_LAST_DAY).toBe("2026-07-14");
    expect(isMtfTrendStrategy(MTF_ID)).toBe(true);
    expect(isMtfTrendStrategy("DAILY_TREND_01_SMA200")).toBe(false);
    expect(isDailyTrendStrategy(MTF_ID)).toBe(false);
    expect(isHoldoutGuardedStrategy(MTF_ID)).toBe(true);
    expect(isHoldoutGuardedStrategy("DAILY_TREND_ROTATION")).toBe(true);
    expect(isHoldoutGuardedStrategy("CANDLE12_V2")).toBe(false);
    expect(isHoldoutGuardedStrategy(null)).toBe(false);
    expect(holdoutBarsPerDay(MTF_ID)).toBe(6);
    expect(holdoutBarsPerDay("DAILY_TREND_01_SMA200")).toBe(1);

    // Daily: N bars → N days ending on the cap (unchanged daily behaviour).
    expect(holdoutTailStartDay(365, 1)).toBe("2025-07-15");
    expect(holdoutTailStartDay(1095, 1)).toBe("2023-07-16");
    expect(holdoutTailStartDay(1, 1)).toBe("2026-07-14");
    // 4h: ceil(N * 4 / 24) days.
    expect(holdoutTailStartDay(2190, 6)).toBe("2025-07-15");
    expect(holdoutTailStartDay(6570, 6)).toBe("2023-07-16");
    expect(holdoutTailStartDay(6, 6)).toBe("2026-07-14");
    expect(holdoutTailStartDay(7, 6)).toBe("2026-07-13");
    expect(holdoutTailStartDay(100, 6)).toBe("2026-06-28");
  });

  it("PnL label is exact: complete only for COMPLETE funding coverage", () => {
    expect(FUNDING_COMPLETE_PNL_LABEL).toBe("After fees, slippage and funding.");
    expect(FUNDING_INCOMPLETE_PNL_LABEL).toBe(
      "After fees, slippage and included funding; funding incomplete/unresolved.",
    );
    expect(fundingPnlLabel("COMPLETE")).toBe(FUNDING_COMPLETE_PNL_LABEL);
    for (const status of ["PARTIAL", "UNRESOLVED", "MISSING", null, undefined]) {
      expect(fundingPnlLabel(status)).toBe(FUNDING_INCOMPLETE_PNL_LABEL);
    }
  });

  it("run guard: HTF label for the 4h book", () => {
    const job = { status: "done", strategy_id: MTF_ID, direction: "LONG", rows: [] } as unknown as BacktestJob;
    expect(htfRequirementLabel(job)).toBe("none (book 16 rule on 4h bars)");
  });

  it("chart candle fetch sends holdout=true only when asked", async () => {
    const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: "OK", candles: [] })));
    vi.stubGlobal("fetch", fetchMock);
    try {
      await actual.fetchResearchOhlcvCandles({
        symbol: "BTCUSDT",
        timeframe: "4h",
        start: "2025-01-01T00:00:00Z",
        holdout: true,
      });
      await actual.fetchResearchOhlcvCandles({ symbol: "BTCUSDT", timeframe: "4h", start: "2025-01-01T00:00:00Z" });
      const urls = fetchMock.mock.calls.map((c) => String((c as unknown[])[0]));
      expect(new URL(urls[0], "http://x").searchParams.get("holdout")).toBe("true");
      expect(new URL(urls[1], "http://x").searchParams.has("holdout")).toBe(false);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("4h book 16 in the Backtest tab", () => {
  afterEach(() => cleanup());
  beforeEach(() => {
    resetStores();
    vi.mocked(startBacktestJob).mockClear();
  });

  it("is listed in its own optgroup in the long book only", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("tab", { name: "Long trades" }));
    const group = strategySelect(root).querySelector(
      `optgroup[label="${MTF_TREND_OPTGROUP_LABEL}"]`,
    ) as HTMLOptGroupElement | null;
    expect(group).not.toBeNull();
    const opts = Array.from(group!.querySelectorAll("option"));
    expect(opts.map((o) => o.value)).toEqual([MTF_ID]);
    expect(opts.map((o) => o.textContent)).toEqual([MTF_LABEL]);
    for (const label of ["Research", DAILY_TREND_OPTGROUP_LABEL]) {
      const other = strategySelect(root).querySelector(`optgroup[label="${label}"]`);
      const ids = Array.from(other?.querySelectorAll("option") ?? []).map((o) => o.value);
      expect(ids, label).not.toContain(MTF_ID);
    }
    for (const tab of ["Short trades", "Hedge trades"]) {
      await user.click(root.getByRole("tab", { name: tab }));
      expect(
        strategySelect(root).querySelector(`optgroup[label="${MTF_TREND_OPTGROUP_LABEL}"]`),
        tab,
      ).toBeNull();
    }
  });

  it("selecting it offers only 4h, LONG only, BTC/ETH only, and shows the holdout banner", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    expect(root.queryByTestId("mtf-trend-holdout-banner")).toBeNull();
    await openMtf(user, root);

    expect(offeredTimeframes(root)).toEqual(["4h"]);
    expect(root.getByText(/Setup TF:\s*4h/)).toBeTruthy();
    expect(root.getByTestId("mtf-trend-holdout-banner").textContent).toBe(MTF_TREND_HOLDOUT_BANNER);
    expect(MTF_TREND_HOLDOUT_BANNER).toContain("4h bars come from Postgres (2021-10-19 onward)");
    expect(root.queryByTestId("daily-trend-holdout-banner")).toBeNull();
    // LONG only: no direction buttons, direction chip LONG.
    expect(root.queryByTestId("engine-directions")).toBeNull();
    expect(root.getAllByText(/Direction:\s*LONG/).length).toBeGreaterThan(0);
    // The long book opens with BTC, ETH, SOL: SOL is dropped, with a note.
    expect(selectedCoins(root)).toEqual(["BTC", "ETH"]);
    expect(root.getByTestId("coin-filter-notice").textContent).toContain("SOLUSDT removed");
    expect(root.getByTestId("engine-symbols-note").textContent).toContain(
      "MTF_4H_16_BREAKOUT252 runs on BTCUSDT and ETHUSDT only",
    );

    // Day-based presets in 4h bars.
    const y1 = root.getByRole("button", { name: /^1y → 2026-07-14/ });
    expect(y1.textContent).toContain("2,190");
    expect(root.getByRole("button", { name: /^2y → 2026-07-14/ }).textContent).toContain("4,380");
    expect(root.getByRole("button", { name: /^3y → 2026-07-14/ }).textContent).toContain("6,570");
    expect(root.getByTestId("lookback-max").textContent).toContain("2026-07-14");
  });

  it("with no BTC or ETH selected it opens on both", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("tab", { name: "Long trades" }));
    const chips = within(root.getByTestId("selected-coins"));
    await user.click(chips.getByRole("button", { name: /^BTC/ }));
    await user.click(chips.getByRole("button", { name: /^ETH/ }));
    expect(selectedCoins(root)).toEqual(["SOL"]);
    await user.selectOptions(strategySelect(root), MTF_ID);
    await waitFor(() => expect(selectedCoins(root)).toEqual(["BTC", "ETH"]));
  });

  it("DB-tail mode sends end_date 2026-07-14 and a start counted back in 4h bars", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await openMtf(user, root);

    // Default Max: no start, every stored bar through the cap.
    let body = await runAndGetBody(user, root);
    expect(body?.strategy_id).toBe(MTF_ID);
    expect(body?.combination_id).toBe(MTF_ID);
    expect(body?.direction).toBe("LONG");
    expect(body?.timeframes).toEqual(["4h"]);
    expect(body?.setup_timeframe).toBe("4h");
    expect(body?.symbols).toEqual(["BTCUSDT", "ETHUSDT"]);
    expect(body?.start_date).toBeUndefined();
    expect(body?.end_date).toBe("2026-07-14");

    await user.click(root.getByRole("button", { name: /^1y → 2026-07-14/ }));
    body = await runAndGetBody(user, root);
    expect(body?.limit).toBe(2190);
    expect(body?.start_date).toBe("2025-07-15");
    expect(body?.end_date).toBe("2026-07-14");

    await user.click(root.getByRole("button", { name: /^3y → 2026-07-14/ }));
    body = await runAndGetBody(user, root);
    expect(body?.limit).toBe(6570);
    expect(body?.start_date).toBe("2023-07-16");
    expect(body?.end_date).toBe("2026-07-14");

    // Custom bars: 100 4h bars = 17 days (ceil 400 h / 24 h).
    fireEvent.change(root.getByPlaceholderText(/^\d+$/), { target: { value: "100" } });
    body = await runAndGetBody(user, root);
    expect(body?.limit).toBe(100);
    expect(body?.start_date).toBe("2026-06-28");
    expect(body?.end_date).toBe("2026-07-14");
  });

  it("calendar mode caps the end at 2026-07-14 and refuses a start inside the holdout", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await openMtf(user, root);
    await user.click(root.getByRole("button", { name: /^Calendar-range mode$/ }));

    const startInput = root.getByLabelText("Start day (UTC)") as HTMLInputElement;
    const endInput = root.getByLabelText("End day (UTC, inclusive)") as HTMLInputElement;
    expect(startInput.getAttribute("max")).toBe("2026-07-14");
    expect(endInput.getAttribute("max")).toBe("2026-07-14");

    await user.click(root.getByRole("button", { name: "2026 YTD" }));
    expect(endInput.value).toBe("2026-07-14");
    let body = await runAndGetBody(user, root);
    expect(body?.start_date).toBe("2026-01-01");
    expect(body?.end_date).toBe("2026-07-14");

    fireEvent.change(endInput, { target: { value: "2026-09-30" } });
    expect(endInput.value).toBe("2026-07-14");
    body = await runAndGetBody(user, root);
    expect(body?.end_date).toBe("2026-07-14");

    await user.click(root.getByRole("button", { name: "Last 365 days to 2026-07-14" }));
    body = await runAndGetBody(user, root);
    expect(body?.start_date).toBe("2025-07-14");
    expect(body?.end_date).toBe("2026-07-14");

    fireEvent.change(startInput, { target: { value: "2026-08-01" } });
    fireEvent.change(endInput, { target: { value: "" } });
    vi.mocked(startBacktestJob).mockClear();
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(useBacktestJobStore.getState().error).toMatch(/4h book 16 loads data through 2026-07-14/));
    expect(startBacktestJob).not.toHaveBeenCalled();
  });

  it("Run is refused while a coin other than BTC/ETH is selected", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await openMtf(user, root);
    await user.click(root.getByTitle("SOLUSDT — not in this run, click to add"));
    expect(selectedCoins(root)).toEqual(["BTC", "ETH", "SOL"]);
    vi.mocked(startBacktestJob).mockClear();
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() =>
      expect(useBacktestJobStore.getState().error).toBe(
        "MTF_4H_16_BREAKOUT252 runs on BTCUSDT and ETHUSDT only. Remove SOLUSDT to run it.",
      ),
    );
    expect(startBacktestJob).not.toHaveBeenCalled();
  });

  it("other strategies after it are unchanged: CANDLE12_V2 intraday, a daily book 1d", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await openMtf(user, root);
    await user.click(root.getByRole("button", { name: /^1y → 2026-07-14/ }));

    await user.selectOptions(strategySelect(root), "CANDLE12_V2");
    await waitFor(() => expect(selectedTimeframes(root)).toEqual(["1h"]));
    // No bleed-over from book 16: its 4h-only offer and 1d never reach an intraday engine (which
    // timeframes besides 1h it offers is the panel's own rule, not this book's).
    expect(offeredTimeframes(root)).toContain("1h");
    expect(offeredTimeframes(root)).not.toContain("1d");
    expect(root.queryByTestId("mtf-trend-holdout-banner")).toBeNull();
    expect(root.queryByTestId("engine-symbols-note")).toBeNull();
    let body = await runAndGetBody(user, root);
    expect(body?.strategy_id).toBe("CANDLE12_V2");
    expect(body?.timeframes).toEqual(["1h"]);
    expect(body?.limit).toBe(5760);
    expect(body?.start_date).toBeUndefined();
    expect(body?.end_date).toBeUndefined();

    await user.selectOptions(strategySelect(root), "DAILY_TREND_01_SMA200");
    await waitFor(() => expect(selectedTimeframes(root)).toEqual(["1d"]));
    expect(offeredTimeframes(root)).toEqual(["1d"]);
    body = await runAndGetBody(user, root);
    // The shared day preset is still 1y: 365 daily bars.
    expect(body?.limit).toBe(365);
    expect(body?.start_date).toBe("2025-07-15");
    expect(body?.end_date).toBe("2026-07-14");
  });
});

describe("4h book 16 results: funding and holdout candles", () => {
  afterEach(() => cleanup());
  beforeEach(() => {
    resetStores();
    chartProps.length = 0;
    vi.mocked(fetchResearchOhlcvCandles).mockClear();
    Element.prototype.scrollIntoView = vi.fn();
  });

  type T = Record<string, unknown>;

  function trade(no: number, extra: T = {}): T {
    return {
      trade_no: no,
      symbol: "BTCUSDT",
      timeframe: "4h",
      direction: "LONG",
      entry_price: 60000,
      stop_price: 57000,
      exit_price: 63000,
      outcome: "EXIT_SMA200",
      gross_pnl_usd: 21,
      fee_total_usd: 0.8,
      net_pnl_before_funding_usd: 20.2,
      r_net_before_funding: 1.01,
      funding_usd: -3.21,
      funding_settlements: 42,
      funding_coverage_status: "COMPLETE",
      funding_unknown_schedule_checks: 0,
      funding_missing_records: 0,
      net_pnl_usd: 16.99,
      r_net: 0.85,
      signal_time: `2025-0${no}-01T00:00:00+00:00`,
      exit_time: `2025-0${no}-20T00:00:00+00:00`,
      ...extra,
    };
  }

  function job(trades: T[], rowExtra: T = {}, jobExtra: T = {}): T {
    const net = trades.reduce((s, t) => s + Number(t.net_pnl_usd || 0), 0);
    return {
      status: "done",
      job_id: "mtf-result",
      symbols: ["BTCUSDT"],
      timeframes: ["4h"],
      direction: "LONG",
      strategy_id: MTF_ID,
      combination_id: MTF_ID,
      principal_usd: 1000,
      production_comparable: false,
      rows: [
        {
          combination_id: MTF_ID,
          strategy_id: MTF_ID,
          symbol: "BTCUSDT",
          timeframe: "4h",
          direction: "LONG",
          sample_size: trades.length,
          average_R: 1,
          pnl_usd: net,
          pnl_usd_net: net,
          fees_usd: 0.8 * trades.length,
          risk_usd: 20,
          bars_loaded: 1000,
          period_start: "2025-01-01T00:00:00+00:00",
          period_end: "2026-07-14T20:00:00+00:00",
          trades,
          ...rowExtra,
        },
      ],
      ...jobExtra,
    };
  }

  function blotterRows(container: HTMLElement): HTMLTableRowElement[] {
    const blotter = within(container).getByTestId("backtest-trade-blotter");
    return Array.from(blotter.querySelectorAll<HTMLTableRowElement>("tbody tr[role=button]"));
  }

  it("unresolved funding: Funding column, row total and the exact incomplete label", () => {
    const trades = [
      trade(1),
      trade(2, {
        funding_usd: 1.5,
        funding_settlements: 30,
        funding_coverage_status: "UNRESOLVED",
        net_pnl_usd: 21.7,
      }),
    ];
    useBacktestJobStore.setState({
      job: job(trades, { funding_total_usd: -1.71, funding_coverage_status: "UNRESOLVED" }) as never,
    });
    const { container } = renderPanel();
    const root = within(container);
    const blotter = within(root.getByTestId("backtest-trade-blotter"));
    expect(blotter.getByRole("columnheader", { name: "Funding" })).toBeTruthy();
    const cells = blotterRows(container).map((tr) => within(tr).getByTestId("trade-funding"));
    expect(cells[0].textContent).toBe("$-3.21");
    expect(cells[0].getAttribute("title")).toContain("42 settlements");
    expect(cells[0].getAttribute("title")).toContain("net before funding +$20.20");
    expect(cells[1].textContent).toBe("+$1.50UNRESOLVED");

    const summary = root.getByTestId("funding-summary");
    expect(summary.textContent).toContain("Funding $-1.71");
    expect(summary.textContent).toContain("coverage UNRESOLVED");
    expect(root.getByTestId("funding-pnl-label").textContent).toBe(
      "After fees, slippage and included funding; funding incomplete/unresolved.",
    );
  });

  it("complete funding shows the complete label; the chart and stop replay load with holdout=true", async () => {
    const trades = [trade(1), trade(2, { outcome: "SL", exit_price: 57000, net_pnl_usd: -20.5 })];
    useBacktestJobStore.setState({
      job: job(trades, { funding_total_usd: -6.42, funding_coverage_status: "COMPLETE" }) as never,
    });
    const { container } = renderPanel();
    const root = within(container);
    expect(root.getByTestId("funding-pnl-label").textContent).toBe("After fees, slippage and funding.");
    expect(root.getByTestId("funding-summary").textContent).toContain("coverage COMPLETE");

    expect(chartProps.length).toBeGreaterThan(0);
    expect(chartProps.at(-1)?.holdout).toBe(true);
    await waitFor(() => expect(fetchResearchOhlcvCandles).toHaveBeenCalled());
    expect(vi.mocked(fetchResearchOhlcvCandles).mock.calls.at(-1)?.[0]).toMatchObject({
      symbol: "BTCUSDT",
      timeframe: "4h",
      holdout: true,
    });
  });

  it("a row with no funding fields has no Funding column and no holdout flag", async () => {
    const plain = (no: number): T => ({
      trade_no: no,
      symbol: "BTCUSDT",
      timeframe: "1h",
      direction: "LONG",
      entry_price: 100,
      stop_price: 99,
      exit_price: 99,
      outcome: "SL",
      net_pnl_usd: -20,
      gross_pnl_usd: -19,
      signal_time: `2025-01-0${no}T00:00:00+00:00`,
      exit_time: `2025-01-0${no}T04:00:00+00:00`,
    });
    useBacktestJobStore.setState({
      job: job([plain(1)], { combination_id: "COMBO_01", strategy_id: "COMBO_01", timeframe: "1h" }, {
        strategy_id: "COMBO_01",
        combination_id: "COMBO_01",
        timeframes: ["1h"],
      }) as never,
    });
    const { container } = renderPanel();
    const root = within(container);
    const blotter = within(root.getByTestId("backtest-trade-blotter"));
    expect(blotter.queryByRole("columnheader", { name: "Funding" })).toBeNull();
    expect(root.queryByTestId("funding-summary")).toBeNull();
    expect(chartProps.at(-1)?.holdout).toBe(false);
    await waitFor(() => expect(fetchResearchOhlcvCandles).toHaveBeenCalled());
    expect(vi.mocked(fetchResearchOhlcvCandles).mock.calls.at(-1)?.[0]).toMatchObject({ holdout: false });
  });
});
