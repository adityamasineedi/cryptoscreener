/**
 * @vitest-environment jsdom
 *
 * Backtest tab seal guard and labels (owner D202 / "D184"): the banner, the sizing fields a
 * regime run ignores, research verdict badges, the sealed calendar message, the period a run
 * used, and the sealed Profit checker.
 */
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBacktestDevMode, startBacktestJob, startRegimeBacktest } from "../api/client";
import {
  BACKTEST_TAB_BANNER,
  DEV_MODE_BANNER,
  DEV_MODE_DISABLED_HINT,
  NOT_USED_LABEL,
  PROFIT_CHECKER_SEALED_LABEL,
  SEALED_MESSAGE,
} from "../backtest/seal";
import { useBacktestHandoffStore } from "../store/backtestHandoffStore";
import { useBacktestJobStore } from "../store/backtestJobStore";
import { setupUser } from "../test/user";
import { BacktestPanel } from "./BacktestPanel";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (opts: { count: number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, index) => ({ index, start: index * 28, end: (index + 1) * 28, key: index, size: 28, lane: 0 })),
    getTotalSize: () => opts.count * 28,
    measureElement: () => undefined,
    scrollToIndex: () => undefined,
  }),
}));

const SHORT_PLAN = {
  directions: ["SHORT"],
  timeframe: "1h",
  sides_in_scope: [
    { strategy_id: "COMBO_02_V2", timeframe: "1h", direction: "SHORT" },
    { strategy_id: "COMBO_02", timeframe: "1h", direction: "SHORT" },
    { strategy_id: "COMBO_02_CLOSED_HTF", timeframe: "1h", direction: "SHORT" },
    { strategy_id: "COMBO_03_TRANSITION", timeframe: "1h", direction: "SHORT" },
    { strategy_id: "COMBO_03_TRANSITION_B", timeframe: "1h", direction: "SHORT" },
    { strategy_id: "COMBO_01", timeframe: "1h", direction: "SHORT" },
    { strategy_id: "COMBO_02_LOCAL", timeframe: "1h", direction: "SHORT" },
  ],
  fixed_config: { principal_usd: 1000, leverage: 2, taker_fee_pct: 0.05, maker_fee_pct: 0.02 },
  sealed_through: "2026-07-14",
};

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchResearchOhlcvRange: vi.fn(async () => ({ status: "OK", rows: [] })),
    fetchStrategyRank: vi.fn(async () => null),
    fetchCapBookAgents: vi.fn(async () => ({
      status: "OK",
      poll_interval_seconds: 3,
      shards: [
        {
          shard: "a",
          agent_name: "Agent A",
          cap_group: "large",
          coins: ["BTC"],
          status: "OK",
          market_direction: null,
          principal_usd: 1000,
          elapsed_seconds: 1,
          cells_finished: 2,
          rows: [
            { combination_id: "COMBO_02", symbol: "BTCUSDT", timeframe: "1h", direction: "SHORT", cap_group: "large",
              risk_usd: 20, sample_size: 6, average_R: 0.1, average_R_net: 0.1, pnl_usd_net: 12, max_drawdown_R: 1, cell_seconds: 1 },
            { combination_id: "COMBO_03_TRANSITION_B", symbol: "BTCUSDT", timeframe: "4h", direction: "SHORT", cap_group: "large",
              risk_usd: 20, sample_size: 0, average_R: null, average_R_net: null, pnl_usd_net: 0, max_drawdown_R: null, cell_seconds: 1 },
          ],
        },
      ],
      sealed: true,
      sealed_label: "Inside the sealed holdout window; already used before the seal (SEALS.md). Not evidence.",
      rerun_allowed: false,
      rerun_refused_reason: "Sealed: data after 2026-07-14 is reserved for the holdout (SEALS.md)",
    })),
    startBacktestJob: vi.fn(async () => ({ status: "STARTED", job: { status: "running", job_id: "x", rows: [] } })),
    fetchBacktestJobStatus: vi.fn(async () => ({ status: "idle", job_id: null, rows: [] })),
    cancelBacktestJob: vi.fn(async () => ({ status: "idle" })),
    startRegimeBacktest: vi.fn(async () => ({ status: "STARTED" })),
    fetchRegimeBacktest: vi.fn(async () => ({ status: "idle" })),
    fetchRegimeBacktestPlan: vi.fn(async (dirs: string[]) =>
      dirs[0] === "LONG"
        ? { ...SHORT_PLAN, directions: ["LONG"], sides_in_scope: [{ strategy_id: "COMBO_03_TRANSITION_B", timeframe: "1h", direction: "LONG" }] }
        : SHORT_PLAN,
    ),
    fetchResearchOhlcvCandles: vi.fn(async () => ({ status: "OK", candles: [] })),
    fetchBacktestDevMode: vi.fn(async () => ({ allowed: false, hint: "owner-only: set BACKTEST_DEV_MODE_ALLOWED=true in .env" })),
  };
});

vi.mock("./BacktestTradeChart", () => ({ BacktestTradeChart: () => null }));

function renderPanel() {
  return render(
    <MemoryRouter>
      <BacktestPanel />
    </MemoryRouter>,
  );
}

beforeAll(() => {
  const { container } = renderPanel();
  within(container).getByRole("tab", { name: "Long trades" });
  cleanup();
  vi.clearAllMocks();
});

describe("Backtest tab seal guard and labels (D202)", () => {
  afterEach(() => cleanup());
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    useBacktestJobStore.setState({
      job: null,
      request: null,
      foreignJob: false,
      ownJobId: null,
      error: null,
      active: false,
      starting: false,
      _gen: 0,
    } as never);
    useBacktestHandoffStore.setState({ symbols: [], revision: 0 });
    vi.mocked(startBacktestJob).mockClear();
    vi.mocked(startRegimeBacktest).mockClear();
    vi.mocked(fetchBacktestDevMode).mockImplementation(async () => ({ allowed: false, hint: DEV_MODE_DISABLED_HINT }));
  });

  it("shows the in-sample banner at the top of the tab, on every book", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    expect(root.getByTestId("backtest-tab-banner").textContent).toBe(BACKTEST_TAB_BANNER);
    expect(BACKTEST_TAB_BANNER).toBe(
      "In-sample backtest. No random baseline or validation. Decisions come from the research results page.",
    );
    await user.click(root.getByRole("tab", { name: "Grid trades" }));
    expect(root.getByTestId("backtest-tab-banner")).toBeTruthy();
  });

  it("greys out Principal, Risk, Leverage and Fee for the regime run and shows one sizing", async () => {
    const { container } = renderPanel();
    const root = within(container);
    for (const field of ["principal", "risk-pct", "risk-usd", "leverage", "taker-fee", "maker-fee"]) {
      expect(root.getByTestId(`not-used-${field}`).textContent).toContain(NOT_USED_LABEL);
    }
    for (const name of [
      "Principal $ (starting equity)",
      "Risk % of principal (1R)",
      "Risk $ per trade (1R)",
      "Leverage (x)",
      "Taker fee % (exit + market entry)",
      "Maker fee % (LIMIT_RETEST entry)",
    ]) {
      expect((root.getByLabelText(name) as HTMLInputElement).disabled).toBe(true);
    }
    await waitFor(() => expect(root.getByTestId("run-sizing").textContent).toMatch(/taker 0\.05% \/ maker 0\.02%/));
    const sizing = root.getByTestId("run-sizing").textContent || "";
    expect(sizing).toMatch(/Regime book: principal \$1,000/);
    expect(sizing).not.toMatch(/BTCUSDT core/);
    expect(container.textContent).not.toMatch(/Effective production risk/);
  });

  it("a single strategy keeps its fields and defaults to D144 fees (0.05% / 0.02%)", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), "COMBO_01");
    expect(root.queryByTestId("not-used-principal")).toBeNull();
    expect((root.getByLabelText("Taker fee % (exit + market entry)") as HTMLInputElement).disabled).toBe(false);
    expect((root.getByLabelText("Taker fee % (exit + market entry)") as HTMLInputElement).value).toBe("0.05");
    expect((root.getByLabelText("Maker fee % (LIMIT_RETEST entry)") as HTMLInputElement).value).toBe("0.02");
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(startBacktestJob).toHaveBeenCalledTimes(1));
    const body = vi.mocked(startBacktestJob).mock.calls[0][0] as Record<string, unknown>;
    expect(body.taker_fee_pct).toBe(0.05);
    expect(body.maker_fee_pct).toBe(0.02);
  });

  it("the auto run's chips are the sides the backend says it walks, COMBO_02_V2 included, with verdicts", async () => {
    const { container } = renderPanel();
    const root = within(container);
    const chips = await waitFor(() => root.getByTestId("run-chip-COMBO_02_V2"));
    expect(chips.textContent).toMatch(/COMBO_02_V2 1h SHORT/);
    const selection = root.getByTestId("passed-strategy-selection");
    expect(within(selection).getAllByTestId(/^run-chip-/)).toHaveLength(SHORT_PLAN.sides_in_scope.length);
    const v2Badge = within(chips).getByTestId("research-verdict-badge");
    expect(v2Badge.textContent).toBe("NOT RUN");
    expect(v2Badge.getAttribute("title")).toMatch(/D110/);
    const combo01 = root.getByTestId("run-chip-COMBO_01");
    const killed = within(combo01).getByTestId("research-verdict-badge");
    expect(killed.textContent).toBe("KILLED");
    expect(killed.getAttribute("title")).toMatch(/Gate 2, E9/);
  });

  it("each strategy in the dropdown names its research verdict, with the reason on hover", () => {
    const { container } = renderPanel();
    const select = within(container).getByRole("combobox", { name: /^Strategy/i });
    const option = within(select).getByRole("option", { name: /^COMBO_01 · 1h SHORT/ });
    expect(option.textContent).toBe("COMBO_01 · 1h SHORT — KILLED");
    expect(option.getAttribute("title")).toMatch(/COMBO_01\|SHORT · KILLED at DISCOVERY: Gate 2, E9/);
    const passedAuto = within(select).getByRole("option", { name: "Passed strategies (auto)" });
    expect(passedAuto.textContent).not.toMatch(/KILLED|NOT RUN|INCONCLUSIVE/);
  });

  it("the selected strategy chip carries its verdict (book 16 is INCONCLUSIVE)", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("tab", { name: "Long trades" }));
    await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), "MTF_4H_16_BREAKOUT252");
    const badges = root.getAllByTestId("research-verdict-badge");
    expect(badges.some((b) => b.textContent === "INCONCLUSIVE" && /owner D96/.test(b.getAttribute("title") || ""))).toBe(true);
  });

  it("a calendar range ending 2026-10-01 shows the Sealed message and does not run", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: "Calendar-range mode" }));
    const dates = container.querySelectorAll('input[type="date"]');
    fireEvent.change(dates[0], { target: { value: "2026-01-01" } });
    fireEvent.change(dates[1], { target: { value: "2026-10-01" } });
    // Not silently clamped: the typed day stays.
    expect((dates[1] as HTMLInputElement).value).toBe("2026-10-01");
    expect(root.getByTestId("sealed-date-error").textContent).toBe(SEALED_MESSAGE);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    expect(startRegimeBacktest).not.toHaveBeenCalled();
    expect(container.textContent).toContain(SEALED_MESSAGE);

    // Same for a single strategy.
    await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), "COMBO_01");
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    expect(startBacktestJob).not.toHaveBeenCalled();
  });

  it("last 365 days and the 2026 YTD preset end at the seal", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: "Last 365 days to 2026-07-14" }));
    const dates = container.querySelectorAll('input[type="date"]');
    expect((dates[0] as HTMLInputElement).value).toBe("2025-07-14");
    expect((dates[1] as HTMLInputElement).value).toBe("2026-07-14");
    await user.click(root.getByRole("button", { name: "2026 YTD" }));
    expect((dates[1] as HTMLInputElement).value).toBe("2026-07-14");
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(startRegimeBacktest).toHaveBeenCalledTimes(1));
    expect(vi.mocked(startRegimeBacktest).mock.calls[0][0]).toMatchObject({ start_date: "2026-01-01", end_date: "2026-07-14" });
  });

  it("DB-tail mode says it counts back from the seal", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: "DB-tail mode" }));
    expect(root.getByTestId("db-tail-seal-note").textContent).toMatch(/back from 2026-07-14 23:59 UTC/);
    expect(root.getByTestId("regime-run-scope").textContent).toMatch(/5,760 1h bars ≈ 240 days ending 2026-07-14/);
  });

  it("the output shows the period the run used (first and last bar loaded)", () => {
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "seal-job",
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        direction: "LONG",
        strategy_id: "COMBO_01",
        combination_id: "COMBO_01",
        principal_usd: 1000,
        limit: 5760,
        production_comparable: false,
        data_window: { first_bar: "2025-11-11T20:00:00+00:00", last_bar: "2026-07-14T23:00:00+00:00", sealed_through: "2026-07-14" },
        rows: [
          {
            combination_id: "COMBO_01",
            symbol: "BTCUSDT",
            timeframe: "1h",
            direction: "LONG",
            sample_size: 0,
            bars_loaded: 5860,
            warmup_bars: 100,
            bars_evaluated: 5760,
            period_start: "2025-11-16T00:00:00+00:00",
            period_end: "2026-07-14T23:00:00+00:00",
            data_first_bar: "2025-11-11T20:00:00+00:00",
            data_last_bar: "2026-07-14T23:00:00+00:00",
            trades: [],
          },
        ],
      } as never,
    });
    const { container } = renderPanel();
    const root = within(container);
    expect(root.getByTestId("row-data-period").textContent).toBe("Data: 2025-11-11 20:00 UTC → 2026-07-14 23:00 UTC");
    const header = root.getByTestId("backtest-data-window").textContent || "";
    expect(header).toContain("2025-11-11 20:00 UTC → 2026-07-14 23:00 UTC");
    expect(header).toContain("sealed through 2026-07-14");
    expect(root.getByTestId("backtest-result-header").textContent).toMatch(/DB-tail 5760 bars ending 2026-07-14/);
  });

  it("the Profit checker carries the sealed label and its re-run is disabled with that reason", async () => {
    const { container } = renderPanel();
    const root = within(container);
    await waitFor(() => expect(root.getByTestId("profit-checker-sealed").textContent).toBe(PROFIT_CHECKER_SEALED_LABEL));
    const rerun = root.getByTestId("profit-checker-rerun") as HTMLButtonElement;
    expect(rerun.disabled).toBe(true);
    expect(rerun.textContent).toContain(SEALED_MESSAGE);
    expect(rerun.getAttribute("title")).toContain(PROFIT_CHECKER_SEALED_LABEL);
  });

  // --- owner D206 ---------------------------------------------------------------------------

  it("D206-1 $0.00 is neutral grey in the coin table, the Profit checker and the run's money columns", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    const net4h = await waitFor(() => root.getByTestId("coin-net-4h"));
    expect(net4h.textContent).toBe("$0.00");
    expect(net4h.className).toContain("text-terminal-muted");
    expect(net4h.className).not.toMatch(/text-red|text-emerald/);
    expect(root.getByTestId("coin-net-1h").className).toContain("text-emerald-400");
    await user.click(root.getByRole("button", { name: /Show failed strategy sides/ }));
    const zeroSide = root.getAllByTestId("combined-net").find((c) => c.textContent === "$0.00");
    expect(zeroSide?.className).toContain("text-terminal-muted");
    cleanup();

    useBacktestJobStore.setState({
      job: {
        status: "done", job_id: "z", symbols: ["BTCUSDT"], timeframes: ["1h"], direction: "LONG",
        strategy_id: "COMBO_01", combination_id: "COMBO_01", principal_usd: 1000, production_comparable: false,
        rows: [{ combination_id: "COMBO_01", symbol: "BTCUSDT", timeframe: "1h", direction: "LONG", sample_size: 2,
                 pnl_usd: 0, pnl_usd_net: 0, fees_usd: 0, trades: [] }],
      } as never,
    });
    const again = within(renderPanel().container);
    for (const id of ["row-gross-pnl", "row-net-pnl"]) {
      const cell = again.getByTestId(id);
      expect(cell.textContent).toMatch(/^\$0\.00/);
      expect(cell.className).toContain("text-terminal-muted");
    }
  });

  it("D206-2 the Profit checker and coin table headers are solid and sit above the rows", async () => {
    const { container } = renderPanel();
    const root = within(container);
    for (const id of ["profit-checker-head", "coin-checker-head"]) {
      const head = await waitFor(() => root.getByTestId(id));
      expect(head.className).toMatch(/\bsticky\b/);
      expect(head.className).toMatch(/\btop-0\b/);
      expect(head.className).toMatch(/\bz-20\b/);
      expect(head.className).toMatch(/\bbg-terminal-bg\b/);
      expect(head.className).not.toMatch(/bg-black\/90/);
      for (const th of Array.from(head.querySelectorAll("th"))) {
        expect(th.className).toMatch(/bg-terminal-bg/);
      }
    }
  });

  it("D206-3 Combined net and Best cell stay pinned at the right edge (column order unchanged)", async () => {
    const { container } = renderPanel();
    const root = within(container);
    const head = await waitFor(() => root.getByTestId("pinned-combined-net-head"));
    expect(head.className).toMatch(/\bsticky\b.*\bright-0\b/);
    expect(root.getByTestId("pinned-best-cell-head").className).toMatch(/\bsticky\b.*\bright-0\b/);
    for (const cell of [...root.getAllByTestId("combined-net"), ...root.getAllByTestId("best-cell")]) {
      expect(cell.className).toMatch(/\bsticky\b/);
      expect(cell.className).toMatch(/\bright-0\b/);
      expect(cell.className).toMatch(/\bbg-terminal-bg\b/);
    }
    const headers = Array.from(root.getByTestId("profit-checker-head").querySelectorAll("th")).map((th) => th.textContent);
    expect(headers.slice(6, 9)).toEqual(["Coins down", "Combined net", "Research verdict"]);
  });

  it("D206-4 the data map names what the selected run executes", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    const map = () => root.getByLabelText("Backtest data map").textContent || "";
    await waitFor(() => expect(map()).toContain("auto regime book · 1h SHORT · 7 sides"));
    expect(map()).not.toContain("COMBO_02_V2 · 1h LONG+SHORT");
    await user.click(root.getByRole("tab", { name: "Long trades" }));
    await waitFor(() => expect(map()).toContain("auto regime book · 1h LONG · 1 side"));
    await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), "MTF_4H_16_BREAKOUT252");
    expect(map()).toContain("4h · 252-bar breakout (book 16, BTC/ETH) · LONG");
    expect(map()).not.toContain("auto regime book");
  });

  // --- owner-only dev mode (D212) -----------------------------------------------------------

  it("D212 the dev-mode switch is off by default and disabled while the server does not allow it", async () => {
    const { container } = renderPanel();
    const root = within(container);
    const sw = root.getByTestId("dev-mode-switch") as HTMLInputElement;
    expect(sw.checked).toBe(false);
    await waitFor(() => expect(fetchBacktestDevMode).toHaveBeenCalled());
    expect(sw.disabled).toBe(true);
    expect(root.getByTestId("dev-mode-hint").textContent).toContain("owner-only: set BACKTEST_DEV_MODE_ALLOWED=true in .env");
    expect(root.queryByTestId("dev-mode-banner")).toBeNull();
  });

  it("D212 with the server's permission the switch turns on, shows the red banner and sends dev_mode", async () => {
    vi.mocked(fetchBacktestDevMode).mockImplementation(async () => ({ allowed: true, hint: null }));
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    const sw = root.getByTestId("dev-mode-switch") as HTMLInputElement;
    await waitFor(() => expect(sw.disabled).toBe(false));
    expect(sw.checked).toBe(false);
    expect(root.queryByTestId("dev-mode-banner")).toBeNull();
    await user.click(sw);
    expect(root.getByTestId("dev-mode-banner").textContent).toBe(DEV_MODE_BANNER);
    expect(DEV_MODE_BANNER).toBe("Uses holdout data; results are not evidence");
    await user.click(root.getByRole("button", { name: "Calendar-range mode" }));
    const dates = container.querySelectorAll('input[type="date"]');
    fireEvent.change(dates[0], { target: { value: "2026-07-01" } });
    fireEvent.change(dates[1], { target: { value: "2026-10-01" } });
    expect(root.queryByTestId("sealed-date-error")).toBeNull();
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(startRegimeBacktest).toHaveBeenCalledTimes(1));
    expect(vi.mocked(startRegimeBacktest).mock.calls[0][0]).toMatchObject({ end_date: "2026-10-01", dev_mode: true });
  });

  it("D212 a dev-mode run's output says DEV MODE (holdout data)", () => {
    useBacktestJobStore.setState({
      job: {
        status: "done", job_id: "dev", symbols: ["BTCUSDT"], timeframes: ["1h"], direction: "LONG",
        strategy_id: "COMBO_01", combination_id: "COMBO_01", principal_usd: 1000, production_comparable: false,
        dev_mode: true, run_label: "DEV MODE (holdout data)",
        rows: [{ combination_id: "COMBO_01", symbol: "BTCUSDT", timeframe: "1h", direction: "LONG", sample_size: 0, trades: [] }],
      } as never,
    });
    const root = within(renderPanel().container);
    expect(root.getByTestId("dev-mode-run-label").textContent).toBe("DEV MODE (holdout data)");
  });
});
