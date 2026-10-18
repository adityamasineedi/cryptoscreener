/**
 * @vitest-environment jsdom
 */
import { act, cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { startBacktestJob, type BacktestJob } from "../api/client";
import {
  checkJobInternallyConsistent,
  directionLabel,
  htfRequirementLabel,
} from "../backtest/runGuard";
import {
  BACKTEST_STRATEGY_OPTIONS,
  DAILY_TREND_HOLDOUT_BANNER,
  DAILY_TREND_LAST_DAY,
  DAILY_TREND_OPTGROUP_LABEL,
  DAILY_TREND_STRATEGY_IDS,
  strategyIdsForBook,
} from "../backtest/v1Config";
import { useBacktestHandoffStore } from "../store/backtestHandoffStore";
import { useBacktestJobStore } from "../store/backtestJobStore";
import { setupUser } from "../test/user";
import { BacktestPanel } from "./BacktestPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchResearchOhlcvRange: vi.fn(async () => ({ status: "OK", rows: [] })),
    fetchStrategyRank: vi.fn(async () => ({ status: "OK", groups: [] })),
    fetchCapBookAgents: vi.fn(async () => ({ status: "OK", poll_interval_seconds: 3, shards: [] })),
    startBacktestJob: vi.fn(async () => ({
      status: "STARTED",
      job: { status: "running", job_id: "daily-job", rows: [] },
    })),
    fetchBacktestJobStatus: vi.fn(async () => ({ status: "idle", job_id: null, rows: [] })),
    cancelBacktestJob: vi.fn(async () => ({ status: "idle" })),
    startRegimeBacktest: vi.fn(async () => ({ status: "STARTED" })),
    fetchRegimeBacktest: vi.fn(async () => ({ status: "idle" })),
    cancelRegimeBacktest: vi.fn(async () => ({ status: "CANCELLED" })),
  };
});

vi.mock("./BacktestTradeChart", () => ({ BacktestTradeChart: () => null }));

const DAILY_LABELS: Record<string, string> = {
  DAILY_TREND_01_SMA200: "Daily · SMA200 (book 1)",
  DAILY_TREND_02_EMA200: "Daily · EMA200 (book 2)",
  DAILY_TREND_03_SMA50: "Daily · SMA50 (book 3)",
  DAILY_TREND_04_MA_SLOPE: "Daily · MA slope (book 4)",
  DAILY_TREND_05_GOLDEN_CROSS: "Daily · Golden/death cross (book 5)",
  DAILY_TREND_06_WEINSTEIN: "Daily · Weinstein stage (book 6)",
  DAILY_TREND_09_DONCHIAN20_WEINSTEIN: "Daily · Donchian 20 + Weinstein (book 9)",
  DAILY_TREND_11_ADX_DMI: "Daily · ADX/DMI 14 (book 11)",
  DAILY_TREND_12_ICHIMOKU: "Daily · Ichimoku (book 12)",
  DAILY_TREND_14_ROC90: "Daily · ROC 90 (book 14)",
  DAILY_TREND_15_ROC30: "Daily · ROC 30 (book 15)",
  DAILY_TREND_19_COMBINED: "Daily · Combined 4+7 (book 19)",
  DAILY_TREND_ROTATION: "Daily · Rotation top 3 of 12 (walk-forward)",
  DAILY_TREND_STATIC12: "Daily · Static 12 (rotation benchmark)",
};

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

/** Timeframe buttons on offer, in DOM order. */
function offeredTimeframes(root: Root): string[] {
  return ["5m", "15m", "1h", "4h", "1d"].filter((tf) => root.queryByTitle(new RegExp(`^${tf} —`)) !== null);
}

function selectedTimeframes(root: Root): string[] {
  return offeredTimeframes(root).filter((tf) => isSelected(root.getByTitle(new RegExp(`^${tf} —`))));
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

describe("daily trend books in the Backtest tab", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
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
    vi.mocked(startBacktestJob).mockClear();
  });

  it("config: 12 daily books, 1d only, LONG/SHORT/BOTH, in the long and short books", () => {
    expect([...DAILY_TREND_STRATEGY_IDS]).toEqual(Object.keys(DAILY_LABELS));
    for (const id of DAILY_TREND_STRATEGY_IDS) {
      const opt = BACKTEST_STRATEGY_OPTIONS.find((o) => o.id === id);
      expect(opt?.label).toBe(DAILY_LABELS[id]);
      expect(opt?.direct).toBe(true);
      expect(opt?.engineTimeframes).toEqual(["1d"]);
      expect(opt?.engineDirections).toEqual(["LONG", "SHORT", "BOTH"]);
      expect(strategyIdsForBook("long")).toContain(id);
      expect(strategyIdsForBook("short")).toContain(id);
      expect(strategyIdsForBook("hedge")).not.toContain(id);
    }
    // Existing engine strategies never list 1d. Only the LONG-only multicap engines
    // list their directions (backend ENGINE_STRATEGIES "directions": ("LONG",)).
    for (const opt of BACKTEST_STRATEGY_OPTIONS) {
      if (DAILY_TREND_STRATEGY_IDS.includes(opt.id as (typeof DAILY_TREND_STRATEGY_IDS)[number])) continue;
      expect(opt.engineDirections, opt.id).toEqual(
        opt.comboVersion === "engine-multicap" ? ["LONG"] : undefined,
      );
      expect(opt.engineTimeframes ?? [], opt.id).not.toContain("1d");
    }
  });

  it("lists the books in their own optgroup in the long and short books, not in hedge", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    for (const tab of ["Short trades", "Long trades"]) {
      await user.click(root.getByRole("tab", { name: tab }));
      const group = strategySelect(root).querySelector(
        `optgroup[label="${DAILY_TREND_OPTGROUP_LABEL}"]`,
      ) as HTMLOptGroupElement | null;
      expect(group, tab).not.toBeNull();
      const opts = Array.from(group!.querySelectorAll("option"));
      expect(opts.map((o) => o.value)).toEqual([...DAILY_TREND_STRATEGY_IDS]);
      expect(opts.map((o) => o.textContent)).toEqual(Object.values(DAILY_LABELS));
      const research = strategySelect(root).querySelector('optgroup[label="Research"]');
      const researchIds = Array.from(research?.querySelectorAll("option") ?? []).map((o) => o.value);
      expect(researchIds.filter((v) => v.startsWith("DAILY_TREND_"))).toEqual([]);
    }
    await user.click(root.getByRole("tab", { name: "Hedge trades" }));
    expect(
      strategySelect(root).querySelector(`optgroup[label="${DAILY_TREND_OPTGROUP_LABEL}"]`),
    ).toBeNull();
  });

  it("selecting a book sets 1d, offers only 1d, shows the holdout banner and LONG/SHORT/BOTH", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    // Default screen: no 1d, no banner.
    expect(offeredTimeframes(root)).toEqual(["15m", "1h", "4h"]);
    expect(root.queryByTestId("daily-trend-holdout-banner")).toBeNull();

    await user.selectOptions(strategySelect(root), "DAILY_TREND_01_SMA200");
    await waitFor(() => expect(selectedTimeframes(root)).toEqual(["1d"]));
    expect(offeredTimeframes(root)).toEqual(["1d"]);
    expect(root.getByText(/Setup TF:\s*1d/)).toBeTruthy();
    expect(root.getByTestId("daily-trend-holdout-banner").textContent).toBe(DAILY_TREND_HOLDOUT_BANNER);

    const dirs = within(root.getByTestId("engine-directions"));
    const names = dirs.getAllByRole("button").map((b) => b.getAttribute("aria-label"));
    expect(names).toEqual(["Direction LONG", "Direction SHORT", "Direction BOTH"]);
    // Short book opens the book on SHORT.
    expect(dirs.getByRole("button", { name: "Direction SHORT" }).getAttribute("aria-pressed")).toBe("true");

    // Day-based presets replace the intraday ones.
    expect(root.getByRole("button", { name: /^1y → 2026-07-14/ })).toBeTruthy();
    expect(root.getByRole("button", { name: /^2y → 2026-07-14/ })).toBeTruthy();
    expect(root.getByRole("button", { name: /^3y → 2026-07-14/ })).toBeTruthy();
    expect(root.getByTestId("lookback-max").textContent).toContain("2026-07-14");
    expect(root.queryByRole("button", { name: /days at 1h/ })).toBeNull();
  });

  it("long book opens LONG; BOTH is sent as the full book", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("tab", { name: "Long trades" }));
    await user.selectOptions(strategySelect(root), "DAILY_TREND_19_COMBINED");
    const dirs = within(root.getByTestId("engine-directions"));
    expect(dirs.getByRole("button", { name: "Direction LONG" }).getAttribute("aria-pressed")).toBe("true");

    let body = await runAndGetBody(user, root);
    expect(body?.direction).toBe("LONG");

    await user.click(dirs.getByRole("button", { name: "Direction BOTH" }));
    body = await runAndGetBody(user, root);
    expect(body?.direction).toBe("BOTH");
    expect(body?.strategy_id).toBe("DAILY_TREND_19_COMBINED");
    expect(body?.combination_id).toBe("DAILY_TREND_19_COMBINED");
    expect(body?.combo_version).toBe("engine-daily-trend");
    expect(body?.timeframes).toEqual(["1d"]);
    expect(body?.setup_timeframe).toBe("1d");

    await user.click(dirs.getByRole("button", { name: "Direction SHORT" }));
    body = await runAndGetBody(user, root);
    expect(body?.direction).toBe("SHORT");
  });

  it("DB-tail mode always sends end_date 2026-07-14 (inclusive last bar)", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.selectOptions(strategySelect(root), "DAILY_TREND_06_WEINSTEIN");

    // Default preset at 1d is Max: every stored bar through the cap.
    let body = await runAndGetBody(user, root);
    expect(body?.end_date).toBe(DAILY_TREND_LAST_DAY);
    expect(body?.start_date).toBeUndefined();

    await user.click(root.getByRole("button", { name: /^1y → 2026-07-14/ }));
    body = await runAndGetBody(user, root);
    expect(body?.limit).toBe(365);
    expect(body?.start_date).toBe("2025-07-15");
    expect(body?.end_date).toBe("2026-07-14");

    await user.click(root.getByRole("button", { name: /^3y → 2026-07-14/ }));
    body = await runAndGetBody(user, root);
    expect(body?.limit).toBe(1095);
    expect(body?.start_date).toBe("2023-07-16");
    expect(body?.end_date).toBe("2026-07-14");
    expect(String(body?.end_date) <= DAILY_TREND_LAST_DAY).toBe(true);
  });

  it("calendar mode caps the end at 2026-07-14: max attribute, 2026 YTD, typed dates", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.selectOptions(strategySelect(root), "DAILY_TREND_11_ADX_DMI");
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

    await user.click(root.getByRole("button", { name: "2025" }));
    body = await runAndGetBody(user, root);
    expect(body?.start_date).toBe("2025-01-01");
    expect(body?.end_date).toBe("2025-12-31");

    fireEvent.change(endInput, { target: { value: "2026-09-30" } });
    expect(endInput.value).toBe("2026-07-14");
    body = await runAndGetBody(user, root);
    expect(body?.end_date).toBe("2026-07-14");

    // Start only: the end still defaults to the cap, never "today".
    fireEvent.change(endInput, { target: { value: "" } });
    fireEvent.change(startInput, { target: { value: "2024-01-01" } });
    body = await runAndGetBody(user, root);
    expect(body?.start_date).toBe("2024-01-01");
    expect(body?.end_date).toBe("2026-07-14");

    // A start inside the holdout is refused before any request.
    fireEvent.change(startInput, { target: { value: "2026-08-01" } });
    vi.mocked(startBacktestJob).mockClear();
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(useBacktestJobStore.getState().error).toMatch(/2026-07-14/));
    expect(startBacktestJob).not.toHaveBeenCalled();
  });

  it("existing engine strategies still force LONG, never offer 1d, and send no cap", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("tab", { name: "Long trades" }));
    // Visit a daily book with BOTH first: nothing may leak into CANDLE12_V2.
    await user.selectOptions(strategySelect(root), "DAILY_TREND_02_EMA200");
    await user.click(within(root.getByTestId("engine-directions")).getByRole("button", { name: "Direction BOTH" }));
    await user.selectOptions(strategySelect(root), "CANDLE12_V2");

    await waitFor(() => expect(selectedTimeframes(root)).toEqual(["1h"]));
    expect(offeredTimeframes(root)).toEqual(["5m", "15m", "1h"]);
    expect(root.queryByTestId("engine-directions")).toBeNull();
    expect(root.queryByTestId("daily-trend-holdout-banner")).toBeNull();
    expect(root.getByRole("button", { name: /^≈ 240 days at 1h/ })).toBeTruthy();

    const body = await runAndGetBody(user, root);
    expect(body?.direction).toBe("LONG");
    expect(body?.strategy_id).toBe("CANDLE12_V2");
    expect(body?.timeframes).toEqual(["1h"]);
    expect(body?.limit).toBe(5760);
    expect(body?.start_date).toBeUndefined();
    expect(body?.end_date).toBeUndefined();
  });

  it("a short-book direct strategy after a daily book returns to 1h SHORT", async () => {
    const user = setupUser();
    const { container } = renderPanel();
    const root = within(container);
    await user.selectOptions(strategySelect(root), "DAILY_TREND_14_ROC90");
    await user.click(within(root.getByTestId("engine-directions")).getByRole("button", { name: "Direction BOTH" }));
    await user.selectOptions(strategySelect(root), "COMBO_02");
    await waitFor(() => expect(selectedTimeframes(root)).toEqual(["1h"]));
    const body = await runAndGetBody(user, root);
    expect(body?.direction).toBe("SHORT");
    expect(body?.timeframes).toEqual(["1h"]);
    expect(body?.end_date).toBeUndefined();
  });
});

describe("run guard for daily trend jobs", () => {
  const trade = (n: number, direction: "LONG" | "SHORT") => ({ trade_no: n, direction });

  it("a BOTH full-book job may hold LONG and SHORT trades", () => {
    const job = {
      status: "done",
      strategy_id: "DAILY_TREND_01_SMA200",
      direction: "BOTH",
      rows: [{ symbol: "BTCUSDT", timeframe: "1d", trades: [trade(1, "LONG"), trade(2, "SHORT")] }],
    } as unknown as BacktestJob;
    expect(checkJobInternallyConsistent(job)).toEqual([]);
    expect(directionLabel(job)).toBe("LONG+SHORT (BOTH)");
    expect(htfRequirementLabel(job)).toBe("none (daily trend rule on 1d bars)");
  });

  it("a SHORT daily job still flags a LONG trade", () => {
    const job = {
      status: "done",
      strategy_id: "DAILY_TREND_01_SMA200",
      direction: "SHORT",
      rows: [{ symbol: "BTCUSDT", timeframe: "1d", trades: [trade(1, "LONG")] }],
    } as unknown as BacktestJob;
    expect(checkJobInternallyConsistent(job)).toEqual(["BTCUSDT 1d: trade_no 1 is LONG in a SHORT run."]);
  });
});
