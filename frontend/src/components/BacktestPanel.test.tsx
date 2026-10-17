/**
 * @vitest-environment jsdom
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cancelRegimeBacktest,
  fetchRegimeBacktest,
  startBacktestJob,
  startRegimeBacktest,
  type BacktestJob,
  type RegimeBacktestStatus,
  type StrategyTradeRow,
} from "../api/client";
import {
  BACKTEST_CAP_SYMBOLS,
  BACKTEST_LARGE_SYMBOLS,
  BACKTEST_MID_SYMBOLS,
} from "../backtest/v1Config";
import { useBacktestHandoffStore } from "../store/backtestHandoffStore";
import { useBacktestJobStore } from "../store/backtestJobStore";
import { BacktestPanel } from "./BacktestPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchResearchOhlcvRange: vi.fn(async () => ({ status: "OK", rows: [] })),
    fetchCapBookAgents: vi.fn(async () => ({
      status: "OK",
      poll_interval_seconds: 3,
      shards: [
        ["a", "Agent A", "large"],
        ["b", "Agent B", "large"],
        ["c", "Agent C", "mid"],
        ["d", "Agent D", "mid"],
      ].map(([shard, agent_name, cap_group]) => ({
        shard,
        agent_name,
        cap_group,
        coins: [],
        status: "waiting",
        market_direction: null,
        principal_usd: 1000,
        elapsed_seconds: 0,
        cells_finished: 0,
        rows: [],
      })),
    })),
    startBacktestJob: vi.fn(async () => ({
      status: "STARTED",
      job: {
        status: "running",
        job_id: "test-job",
        rows: [],
        paper_trade_created: false,
        live_trade_created: false,
        telegram_sent: false,
      },
      paper_trade_created: false,
      live_trade_created: false,
      telegram_sent: false,
    })),
    fetchBacktestJobStatus: vi.fn(async () => ({
      status: "idle",
      job_id: null,
      rows: [],
    })),
    cancelBacktestJob: vi.fn(async () => ({ status: "idle" })),
    startRegimeBacktest: vi.fn(async () => ({ status: "STARTED" })),
    fetchRegimeBacktest: vi.fn(async () => ({ status: "idle" })),
    cancelRegimeBacktest: vi.fn(async () => ({ status: "CANCELLED", job: { status: "cancelled" } })),
  };
});

/** Trades the panel handed to the trade chart overlay, in render order. */
const chartTrades = vi.hoisted(() => [] as Array<{ symbol?: string; timeframe?: string }>);

vi.mock("./BacktestTradeChart", () => ({
  BacktestTradeChart: ({ trade }: { trade: { symbol?: string; timeframe?: string } }) => {
    chartTrades.push(trade);
    return null;
  },
}));

function renderPanel() {
  return render(
    <MemoryRouter>
      <BacktestPanel />
    </MemoryRouter>
  );
}

function isSelected(el: HTMLElement): boolean {
  return el.className.includes("border-terminal-accent");
}

async function selectFrozenV1(
  user: ReturnType<typeof userEvent.setup>,
  root: ReturnType<typeof within>
) {
  await user.click(root.getByRole("tab", { name: "Long trades" }));
  await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), "COMBO_02_V1");
}

async function selectOnlySymbol(
  user: ReturnType<typeof userEvent.setup>,
  root: ReturnType<typeof within>,
  symbol: "BTCUSDT" | "ETHUSDT" | "SOLUSDT"
) {
  for (const s of ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const) {
    const btn = root.getByTitle(new RegExp(`^${s}`));
    const selected = isSelected(btn);
    if (s === symbol && !selected) await user.click(btn);
    if (s !== symbol && selected) await user.click(btn);
  }
}

async function selectOnlyTimeframe(
  user: ReturnType<typeof userEvent.setup>,
  root: ReturnType<typeof within>,
  tf: "15m" | "1h" | "4h"
) {
  for (const t of ["15m", "1h", "4h"] as const) {
    const btn = root.getByTitle(new RegExp(`^${t}\\b`));
    const selected = isSelected(btn);
    if (t === tf && !selected) await user.click(btn);
    if (t !== tf && selected) await user.click(btn);
  }
}

const DEFAULT_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
const CAP_BOOK: readonly string[] = BACKTEST_CAP_SYMBOLS;

/** Symbols on the chips inside a coin container, in DOM order. */
function coinsIn(root: ReturnType<typeof within>, testId: string): string[] {
  return within(root.getByTestId(testId))
    .queryAllByRole("button")
    .map((btn) => /[A-Z0-9]+USDT/.exec(btn.getAttribute("title") || "")?.[0] ?? `?${btn.textContent}`);
}

function strategyOptions(root: ReturnType<typeof within>): HTMLOptionElement[] {
  const select = root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
  return Array.from(select.querySelectorAll("option"));
}

/** COMBO_02_V1 is the first option and the only one in the Working group. */
function expectWorkingStrategyFirst(root: ReturnType<typeof within>): string[] {
  const select = root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
  const options = strategyOptions(root);
  const values = options.map((o) => o.value);
  expect(values.length).toBeGreaterThan(1);
  expect(values[0]).toBe("COMBO_02_V1");
  expect(values.filter((v) => v === "COMBO_02_V1").length).toBe(1);
  const group = options[0].parentElement as HTMLElement;
  expect(group.tagName).toBe("OPTGROUP");
  expect(group.getAttribute("label")).toBe("Working");
  expect(Array.from(group.querySelectorAll("option")).map((o) => o.value)).toEqual(["COMBO_02_V1"]);
  expect(select.firstElementChild).toBe(group);
  expect(select.querySelectorAll('optgroup[label="Working"]').length).toBe(1);
  return values;
}

describe("BacktestPanel interaction paths", () => {
  afterEach(() => {
    cleanup();
  });

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

  it("1. BTC v1 risk rendering", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);
    await selectOnlySymbol(user, root, "BTCUSDT");

    expect(root.getAllByText(/Strategy:\s*COMBO_02 v1/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/Direction:\s*LONG/i).length).toBeGreaterThan(0);
    expect(root.getByText(/Setup TF:\s*1h/i)).toBeTruthy();
    expect(root.getByText(/HTF:\s*1h \+ 4h bullish alignment/i)).toBeTruthy();
    expect(root.getByText(/FROZEN V1 CONFIGURATION/i)).toBeTruthy();
    expect(root.getByText(/Production-comparable research run/i)).toBeTruthy();

    await waitFor(() => {
      const riskPct = root.getByLabelText(/Risk % of principal/i) as HTMLInputElement;
      const riskUsd = root.getByLabelText(/Risk \$ per trade/i) as HTMLInputElement;
      expect(riskPct.disabled).toBe(true);
      expect(riskUsd.disabled).toBe(true);
      expect(Number(riskPct.value)).toBeCloseTo(2);
      expect(Number(riskUsd.value)).toBe(20);
    });

    expect(root.getByText(/BTCUSDT core — 2% \/ \$20/i)).toBeTruthy();
    expect(root.getAllByText(/BTCUSDT — v1 CORE/i).length).toBeGreaterThan(0);
    expect(container.textContent).toMatch(/Production comparable:\s*YES/);
    expect(container.textContent).toMatch(/Risk source:\s*V1 production profile/);
  });

  it("2. ETH v1 risk rendering", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);
    await selectOnlySymbol(user, root, "ETHUSDT");

    await waitFor(() => {
      const riskPct = root.getByLabelText(/Risk % of principal/i) as HTMLInputElement;
      const riskUsd = root.getByLabelText(/Risk \$ per trade/i) as HTMLInputElement;
      expect(Number(riskPct.value)).toBeCloseTo(2);
      expect(Number(riskUsd.value)).toBe(20);
      expect(riskPct.disabled).toBe(true);
    });
    expect(root.getAllByText(/ETHUSDT — v1 SECONDARY/i).length).toBeGreaterThan(0);
    expect(root.getByText(/ETHUSDT secondary — 2% \/ \$20/i)).toBeTruthy();
  });

  it("3. SOL v1 risk rendering", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);
    await selectOnlySymbol(user, root, "SOLUSDT");

    await waitFor(() => {
      const riskPct = root.getByLabelText(/Risk % of principal/i) as HTMLInputElement;
      const riskUsd = root.getByLabelText(/Risk \$ per trade/i) as HTMLInputElement;
      expect(Number(riskPct.value)).toBeCloseTo(2);
      expect(Number(riskUsd.value)).toBe(20);
    });
    expect(root.getAllByText(/SOLUSDT — v1 SECONDARY/i).length).toBeGreaterThan(0);
    expect(root.getByText(/SOLUSDT secondary — 2% \/ \$20/i)).toBeTruthy();
  });

  it("4. Research override banner", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);
    await selectOnlySymbol(user, root, "BTCUSDT");

    await user.click(root.getByRole("checkbox", { name: /Research risk override/i }));
    const riskPct = root.getByLabelText(/Risk % of principal/i) as HTMLInputElement;
    expect(riskPct.disabled).toBe(false);
    await user.clear(riskPct);
    await user.type(riskPct, "2");

    expect(root.getByText(/RESEARCH-ONLY CONFIGURATION/i)).toBeTruthy();
    expect(
      root.getByText(/differs from the frozen COMBO_02 v1 profile/i)
    ).toBeTruthy();
    expect(container.textContent).toMatch(/Risk source:\s*Research override/);
    expect(container.textContent).toMatch(/Production comparable:\s*NO/);
    expect(root.queryByText(/FROZEN V1 CONFIGURATION/i)).toBeNull();
    expect(
      root.getByText(/Results are research-only and are not production-comparable/i)
    ).toBeTruthy();
  });

  it("5. frozen v1 view has no SHORT button and its status reads Paper forward-test", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);

    expect(root.queryByTitle(/SHORT research-only/i)).toBeNull();
    expect(root.queryByRole("button", { name: /^SHORT/i })).toBeNull();
    expect(root.getByRole("button", { name: /^LONG$/i })).toBeTruthy();
    expect(root.queryByText(/COMBO_04_HTF reverse of long/i)).toBeNull();
    expect(root.queryByText(/→ COMBO_04_HTF SHORT/i)).toBeNull();
    expect(root.getByTestId("backtest-status-chip").textContent).toBe("Status: Paper forward-test");
    expect(root.queryAllByText(/Production candidate \(frozen v1\)/i)).toHaveLength(0);
    expect(root.getAllByText(/Direction:\s*LONG/i).length).toBeGreaterThan(0);
  });

  it("6. frozen v1 runs COMBO_02 v1 LONG and never sends COMBO_04_HTF", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);

    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startBacktestJob).toHaveBeenCalled();
    });
    const body = vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0];
    expect(body?.direction).toBe("LONG");
    expect(body?.combination_id).toBe("COMBO_02");
    expect(body?.strategy_id).toBe("COMBO_02_V1");
  });

  it("6-direct. a direct book keeps its own SHORT button and runs its own id", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), "COMBO_02");
    const shortBtn = root.getByTitle(/SHORT research-only: COMBO_02$/i);
    await user.click(shortBtn);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startBacktestJob).toHaveBeenCalled();
    });
    const body = vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0];
    expect(body?.direction).toBe("SHORT");
    expect(body?.combination_id).toBe("COMBO_02");
    expect(body?.strategy_id).toBe("COMBO_02");
  });

  it("6a. COMBO_02_V1 is listed first under Working in the long book only", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    expect(root.getByRole("tab", { name: "Short trades" }).getAttribute("aria-selected")).toBe("true");
    const shortSelect = root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
    const shortValues = Array.from(shortSelect.options).map((o) => o.value);
    expect(shortValues[0]).toBe("PASSED_AUTO");
    expect(shortValues).not.toContain("COMBO_02_V1");
    // The COMBO_02_V2 family runs LONG+SHORT together, so it lives in the hedge book.
    expect(shortValues).not.toContain("COMBO_02_V2");
    expect(shortValues).not.toContain("COMBO_02_V2_1B");

    await user.click(root.getByRole("tab", { name: "Long trades" }));
    const longValues = expectWorkingStrategyFirst(root);
    expect(longValues[1]).toBe("PASSED_AUTO");
    expect(longValues).toContain("COMBO_03_TRANSITION_B");
    expect(longValues).toContain("COMBO_SD_STRUCTURE");
    expect(longValues).not.toContain("COMBO_02_V2");
    expect(longValues).not.toContain("COMBO_02_V2_1B");
  });

  it("6a1. COMBO_02_V1 in the long book posts COMBO_02 v1 LONG", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);

    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startBacktestJob).toHaveBeenCalledTimes(1);
    });
    const body = vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0];
    expect(body).toMatchObject({
      combination_id: "COMBO_02",
      strategy_id: "COMBO_02_V1",
      combo_version: "v1",
      direction: "LONG",
      symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    });
  });

  it("6a2. the short book does not offer COMBO_02_V1 (its COMBO_04_HTF rewrite made zero trades)", async () => {
    const { container } = renderPanel();
    const root = within(container);

    expect(root.getByRole("tab", { name: "Short trades" }).getAttribute("aria-selected")).toBe("true");
    const select = root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).not.toContain("COMBO_02_V1");
    expect(values).toContain("COMBO_02");
  });

  it("6b. V2.1-A sends research combo id and RESEARCH ONLY label", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    await user.click(root.getByRole("tab", { name: "Hedge trades" }));
    expect(root.getAllByText(/COMBO_02 V2\.1-A/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/RESEARCH ONLY/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/Status:\s*Research/i).length).toBeGreaterThan(0);

    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startBacktestJob).toHaveBeenCalled();
    });
    const body = vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0];
    expect(body?.combination_id).toBe("COMBO_02_V2_1_A");
    expect(body?.strategy_id).toBe("COMBO_02_V2_1_A");
    expect(body?.combo_version).toBe("v2.1-a-choppy-wait");
    expect(body?.research_risk_override).toBe(true);
  });

  it("6b1. passed strategies are selected automatically and run the regime book", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    expect(root.getByDisplayValue("Passed strategies (auto)")).toBeTruthy();
    expect(root.queryByRole("button", { name: /^COMBO_02_V2/ })).toBeNull();
    expect(root.getByRole("button", { name: "COMBO_01 · 1h SHORT" }).getAttribute("aria-pressed")).toBe("true");
    expect(root.getByRole("button", { name: "COMBO_03_TRANSITION_B · 1h SHORT" }).getAttribute("aria-pressed")).toBe("true");
    expect(root.queryByRole("button", { name: "COMBO_02 V2.1-A" })).toBeNull();

    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startRegimeBacktest).toHaveBeenCalled();
    });
    expect(startBacktestJob).not.toHaveBeenCalled();
    const body = vi.mocked(startRegimeBacktest).mock.calls.at(-1)?.[0];
    expect(body?.symbols).toEqual(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
  });

  it("6b1a. untouched screen keeps the passed-auto option and posts the regime book", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    const select = root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
    expect(select.value).toBe("PASSED_AUTO");
    expect(select.selectedOptions[0].textContent).toBe("Passed strategies (auto)");
    expect(root.getByTestId("passed-strategy-selection")).toBeTruthy();

    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startRegimeBacktest).toHaveBeenCalledTimes(1);
    });
    expect(startBacktestJob).not.toHaveBeenCalled();
    const body = vi.mocked(startRegimeBacktest).mock.calls.at(-1)?.[0];
    expect(body?.symbols).toEqual(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
  });

  it("6b2. passed cap-book strategy is selectable and is not rewritten to COMBO_04", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    const select = root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
    expect(root.queryByRole("option", { name: /^COMBO_02_V2/ })).toBeNull();
    expect(root.getByRole("option", { name: "COMBO_01 · 1h SHORT" })).toBeTruthy();
    expect(root.getByRole("option", { name: "COMBO_03_TRANSITION_B · 1h SHORT" })).toBeTruthy();
    await user.selectOptions(select, "COMBO_01");

    expect(root.getAllByText(/COMBO_01 · 1h SHORT/i).length).toBeGreaterThan(0);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startBacktestJob).toHaveBeenCalled();
    });
    const body = vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0];
    expect(body?.direction).toBe("SHORT");
    expect(body?.combination_id).toBe("COMBO_01");
    expect(body?.strategy_id).toBe("COMBO_01");
    expect(body?.combination_id).not.toBe("COMBO_04_HTF");
  });

  it("6c. SD structure is in the strategy menu and auto-sends COMBO_SD_C", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    await user.click(root.getByRole("tab", { name: "Long trades" }));
    const select = root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
    await user.selectOptions(select, "COMBO_SD_STRUCTURE");

    expect(root.getByLabelText("Structure variant")).toBeTruthy();
    expect(root.getByRole("option", { name: /EQH\/EQL sweep/i })).toBeTruthy();
    expect(root.getAllByText(/SD \+ CHoCH \+ BOS/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/RESEARCH ONLY/i).length).toBeGreaterThan(0);

    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startBacktestJob).toHaveBeenCalled();
    });
    const body = vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0];
    expect(body?.combination_id).toBe("COMBO_SD_C");
    expect(body?.strategy_id).toBe("COMBO_SD_C");
    expect(body?.combo_version).toBe("sd-structure");
    expect(body?.research_risk_override).toBe(true);
    expect(body?.symbols).toEqual(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
    expect(body?.direction).toBe("LONG");
  });

  it("6d. SD structure SHORT stays on the selected variant", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    await user.click(root.getByRole("tab", { name: "Long trades" }));
    const select = root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
    await user.selectOptions(select, "COMBO_SD_STRUCTURE");
    await user.selectOptions(root.getByLabelText("Structure variant"), "COMBO_SD_A");
    await user.click(root.getByTitle(/supply zone, bearish CHoCH/i));
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startBacktestJob).toHaveBeenCalled();
    });
    const body = vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0];
    expect(body?.direction).toBe("SHORT");
    expect(body?.combination_id).toBe("COMBO_SD_A");
    expect(body?.strategy_id).toBe("COMBO_SD_A");
  });

  it("7. 15m role label", () => {
    const { container } = renderPanel();
    const root = within(container);
    expect(root.getByTitle(/15m.*research-only/i)).toBeTruthy();
    expect(root.getByText("research-only")).toBeTruthy();
  });

  it("8. 1h role label", () => {
    const { container } = renderPanel();
    const root = within(container);
    expect(root.getByTitle(/1h.*v1 setup timeframe/i)).toBeTruthy();
    expect(root.getByText("v1 setup timeframe")).toBeTruthy();
  });

  it("9. 4h HTF role label", () => {
    const { container } = renderPanel();
    const root = within(container);
    expect(root.getByTitle(/4h.*HTF context/i)).toBeTruthy();
    expect(root.getByText("HTF context")).toBeTruthy();
  });

  it("10. 4h mismatch warning", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectOnlyTimeframe(user, root, "4h");

    expect(
      root.getByText(
        /4h setup is not the frozen COMBO_02 v1 1h production configuration/i
      )
    ).toBeTruthy();
    expect(root.getByText(/RESEARCH-ONLY CONFIGURATION/i)).toBeTruthy();
    expect(container.textContent).toMatch(/Production comparable:\s*NO/);
  });

  it("11. 5,760-bar duration for 15m", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectOnlyTimeframe(user, root, "15m");
    expect(container.textContent).toMatch(/Bars requested:\s*5,760/);
    expect(container.textContent).toMatch(/Approximate duration:\s*60 days at 15m/);
    expect(container.textContent).toMatch(/≈ 60 days at 15m/);
  });

  it("12. 5,760-bar duration for 1h", () => {
    const { container } = renderPanel();
    expect(container.textContent).toMatch(/Bars requested:\s*5,760/);
    expect(container.textContent).toMatch(/Approximate duration:\s*240 days at 1h/);
    expect(container.textContent).toMatch(/≈ 240 days at 1h/);
  });

  it("13. 5,760-bar duration for 4h", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectOnlyTimeframe(user, root, "4h");
    expect(container.textContent).toMatch(/Bars requested:\s*5,760/);
    expect(container.textContent).toMatch(/Approximate duration:\s*960 days at 4h/);
    expect(container.textContent).toMatch(/≈ 960 days at 4h/);
  });

  it("14. DB-tail mode label", () => {
    const { container } = renderPanel();
    const root = within(container);
    expect(root.getAllByText(/DB-tail mode/i).length).toBeGreaterThan(0);
    expect(root.getByText(/Uses the latest N available candles/i)).toBeTruthy();
    expect(
      root.getByText(/Does not represent a calendar-year filter/i)
    ).toBeTruthy();
  });

  it("15. Calendar-range mode label", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    const modeButtons = root.getAllByRole("button", { name: /Calendar-range mode/i });
    await user.click(modeButtons[0]);
    expect(root.getAllByText(/Calendar-range mode/i).length).toBeGreaterThan(0);
    expect(
      root.getByText(/Uses only candles inside the requested UTC range/i)
    ).toBeTruthy();
  });

  it("16. Pre-run summary", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);
    await selectOnlySymbol(user, root, "BTCUSDT");

    const heading = root.getByText(/Pre-run configuration summary/i);
    const summary = heading.parentElement as HTMLElement;
    const box = within(summary);
    expect(box.getByText(/Selected symbol:/i)).toBeTruthy();
    expect(box.getByText(/Symbol role:/i)).toBeTruthy();
    expect(box.getByText(/Strategy:\s*COMBO_02 v1/i)).toBeTruthy();
    expect(box.getByText(/Direction:\s*LONG/i)).toBeTruthy();
    expect(box.getByText(/Setup timeframe:/i)).toBeTruthy();
    expect(box.getByText(/HTF requirement:/i)).toBeTruthy();
    expect(box.getByText(/Effective risk:/i)).toBeTruthy();
    expect(box.getByText(/Risk source:/i)).toBeTruthy();
    expect(box.getByText(/Entry model:/i)).toBeTruthy();
    expect(box.getByText(/Fee model:/i)).toBeTruthy();
    expect(box.getByText(/Leverage:/i)).toBeTruthy();
    expect(box.getByText(/Bars\/date range:/i)).toBeTruthy();
    expect(box.getByText(/Production comparable:/i)).toBeTruthy();
    expect(summary.textContent).toMatch(/Status:\s*Paper forward-test/);
    expect(summary.textContent).not.toMatch(/as selected/);
    expect(root.getByTestId("backtest-status-chip").textContent).toMatch(
      /^Status:\s*Paper forward-test$/
    );

    await user.click(root.getByRole("checkbox", { name: /Research risk override/i }));
    expect(summary.textContent).toMatch(/Status:\s*Research/);
    expect(summary.textContent).not.toMatch(/Paper forward-test/);
    expect(summary.textContent).not.toMatch(/as selected/);
  });

  it("16a. Status chip follows production comparability, not just direction", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);
    await selectOnlySymbol(user, root, "BTCUSDT");

    const chip = root.getByTestId("backtest-status-chip");
    expect(chip.textContent).toMatch(/Paper forward-test/);
    expect(chip.className).toMatch(/emerald/);

    const leverageInput = (root.getByText("Leverage (x)").parentElement as HTMLElement).querySelector(
      "input"
    ) as HTMLInputElement;
    fireEvent.change(leverageInput, { target: { value: "3" } });

    expect(chip.textContent).toMatch(/^Status:\s*Research$/);
    expect(chip.textContent).not.toMatch(/Paper forward-test/);
    expect(chip.className).toMatch(/amber/);
    expect(root.getByText(/Direction:\s*LONG/, { selector: "span" })).toBeTruthy();
  });

  it("17. Frozen v1 banner appears after leaving the passed selection", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    expect(root.getByText(/RESEARCH-ONLY CONFIGURATION/i)).toBeTruthy();
    expect(root.getByTestId("passed-strategy-selection")).toBeTruthy();
    await selectFrozenV1(user, root);
    expect(root.getByText(/FROZEN V1 CONFIGURATION/i)).toBeTruthy();
    expect(root.getByText(/Production-comparable research run/i)).toBeTruthy();
  });

  it("18. Research-only mismatch banner", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    await selectOnlyTimeframe(user, root, "4h");
    expect(root.getByText(/RESEARCH-ONLY CONFIGURATION/i)).toBeTruthy();

    await selectOnlyTimeframe(user, root, "1h");
    await user.click(root.getByRole("checkbox", { name: /Research risk override/i }));
    expect(root.getByText(/RESEARCH-ONLY CONFIGURATION/i)).toBeTruthy();

    await user.click(root.getByRole("checkbox", { name: /Research risk override/i }));
    const lev = root.getByLabelText(/Leverage/i) as HTMLInputElement;
    await user.clear(lev);
    await user.type(lev, "5");
    expect(root.getByText(/RESEARCH-ONLY CONFIGURATION/i)).toBeTruthy();

    await user.clear(lev);
    await user.type(lev, "2");
    await user.click(root.getByTitle(/^BNBUSDT/i));
    expect(root.getByText(/RESEARCH-ONLY CONFIGURATION/i)).toBeTruthy();
  });

  it("19. Result identity metadata", () => {
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "done-1",
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        direction: "LONG",
        strategy_id: "COMBO_02_V1",
        combo_version: "v1",
        source: "V1_RESEARCH_BACKTEST",
        setup_timeframe: "1h",
        htf_timeframes: ["1h", "4h"],
        htf_alignment: "BULLISH",
        risk_source: "V1_PRODUCTION_PROFILE",
        effective_risk_percent: 0.02,
        effective_risk_amount: 20,
        production_comparable: true,
        research_only: false,
        short_status: "PAUSED",
        period_mode: "DB_TAIL",
        dataset_fingerprint: "ds-fp-test",
        configuration_fingerprint: "cfg-fp-test",
        paper_trade_created: false,
        live_trade_created: false,
        telegram_sent: false,
        limit: 5760,
        risk_usd: 20,
        principal_usd: 1000,
        leverage: 2,
        rows: [
          {
            combination_id: "COMBO_02",
            symbol: "BTCUSDT",
            timeframe: "1h",
            direction: "LONG",
            sample_size: 2,
            average_R: 0.5,
            period_start: "2025-01-01T00:00:00+00:00",
            period_end: "2025-08-29T00:00:00+00:00",
            bars_loaded: 5760,
            risk_usd: 20,
            effective_risk_percent: 0.02,
            effective_risk_amount: 20,
            risk_source: "V1_PRODUCTION_PROFILE",
            production_comparable: true,
            research_only: false,
            symbol_role: "CORE",
            dataset_fingerprint: "ds-fp-test",
            configuration_fingerprint: "cfg-fp-test",
            paper_trade_created: false,
            live_trade_created: false,
            telegram_sent: false,
            trades: [],
            equity_curve_r: [0, 0.5],
          },
        ],
      },
      active: false,
      error: null,
    });

    const { container } = renderPanel();
    const root = within(container);

    expect(root.getAllByText(/Strategy:/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/COMBO_02_V1/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/Symbol role:/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/BTCUSDT — v1 CORE/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/Effective risk:/i).length).toBeGreaterThan(0);
    expect(root.getByText(/2\.00% \/ \$20/i)).toBeTruthy();
    expect(root.getAllByText(/Risk source:/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/V1_PRODUCTION_PROFILE/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/Setup timeframe:/i).length).toBeGreaterThan(0);
    expect(root.getAllByText(/HTF requirement:/i).length).toBeGreaterThan(0);
    expect(root.getByText(/Requested range:/i)).toBeTruthy();
    expect(root.getByText(/Actual range:/i)).toBeTruthy();
    expect(container.textContent).toMatch(/2025-01-01\s*→\s*2025-08-29/);
    expect(root.getByText(/Bars used:/i)).toBeTruthy();
    expect(root.getByText(/Dataset fingerprint:/i)).toBeTruthy();
    expect(root.getAllByText(/ds-fp-test/i).length).toBeGreaterThan(0);
    expect(root.getByText(/Configuration fingerprint:/i)).toBeTruthy();
    expect(root.getAllByText(/cfg-fp-test/i).length).toBeGreaterThan(0);
    expect(
      root.getByText(/Historical research only\. Not a profitability claim/i)
    ).toBeTruthy();
  });

  it("19a. CANDLE12_V2 rows carry the V2 reporting warnings; V3 rows do not", () => {
    const engineJob = (strategyId: string) => ({
      status: "done" as const,
      job_id: `done-${strategyId}`,
      symbols: ["BTCUSDT"],
      timeframes: ["1h"],
      direction: "LONG",
      strategy_id: strategyId,
      combo_version: "engine",
      source: "V1_RESEARCH_BACKTEST",
      setup_timeframe: "1h",
      risk_source: "RESEARCH_OVERRIDE",
      production_comparable: false,
      research_only: true,
      period_mode: "DB_TAIL",
      paper_trade_created: false,
      live_trade_created: false,
      telegram_sent: false,
      limit: 5760,
      risk_usd: 20,
      principal_usd: 1000,
      leverage: 2,
      rows: [
        {
          combination_id: strategyId,
          engine_family: strategyId === "CANDLE12_V2" ? "candle12" : "candle12_v3",
          symbol: "BTCUSDT",
          timeframe: "1h",
          direction: "LONG",
          sample_size: 0,
          average_R: null,
          period_start: "2025-01-01T00:00:00+00:00",
          period_end: "2025-08-29T00:00:00+00:00",
          bars_loaded: 5760,
          risk_usd: 20,
          production_comparable: false,
          research_only: true,
          paper_trade_created: false,
          live_trade_created: false,
          telegram_sent: false,
          trades: [],
          equity_curve_r: [],
        },
      ],
    });

    useBacktestJobStore.setState({ job: engineJob("CANDLE12_V2"), active: false, error: null });
    const v2 = renderPanel();
    const warnings = within(v2.container).getByTestId("candle12-v2-warnings");
    expect(warnings.textContent).toContain("Avg R gross is net R.");
    expect(warnings.textContent).toContain("market_context uses Candle-2 close.");
    expect(warnings.textContent).toContain("HTF label is not applicable to CANDLE12_V2.");
    v2.unmount();

    useBacktestJobStore.setState({ job: engineJob("CANDLE12_V3_EXEC_FIX"), active: false, error: null });
    const v3 = renderPanel();
    expect(within(v3.container).queryByTestId("candle12-v2-warnings")).toBeNull();
    expect(within(v3.container).getAllByText(/none \(no HTF gate\)/).length).toBeGreaterThan(0);
  });

  it("20. No operational side effects", () => {
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "done-2",
        direction: "LONG",
        strategy_id: "COMBO_02_V1",
        paper_trade_created: false,
        live_trade_created: false,
        telegram_sent: false,
        rows: [
          {
            combination_id: "COMBO_02",
            symbol: "BTCUSDT",
            timeframe: "1h",
            direction: "LONG",
            sample_size: 0,
            risk_usd: 15,
            bars_loaded: 100,
            trades: [],
          },
        ],
      },
      active: false,
    });
    const { container } = renderPanel();
    const root = within(container);
    const sideEffects = root.getByText(/paper_trade_created = false/i);
    expect(sideEffects.textContent).toMatch(/paper_trade_created = false/i);
    expect(sideEffects.textContent).toMatch(/live_trade_created = false/i);
    expect(sideEffects.textContent).toMatch(/telegram_sent = false/i);
    // The payload carries no in_sample field, so the in-sample warning is not shown.
    expect(root.queryByTestId("backtest-in-sample-warning")).toBeNull();
    expect(startBacktestJob).not.toHaveBeenCalled();
  });

  it("21. A LONG v1 job is not shown as the result of a SHORT request", () => {
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "long-v1",
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        direction: "LONG",
        strategy_id: "COMBO_02_V1",
        production_comparable: true,
        research_only: false,
        principal_usd: 1000,
        rows: [
          {
            combination_id: "COMBO_02",
            symbol: "BTCUSDT",
            timeframe: "1h",
            direction: "LONG",
            sample_size: 2,
            average_R: 0.5,
            pnl_usd_net: 40,
            production_comparable: true,
            research_only: false,
            risk_usd: 20,
            bars_loaded: 100,
            trades: [],
          },
        ],
      },
      request: {
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        direction: "SHORT",
        strategy_id: "COMBO_04_HTF_SHORT",
        research_risk_override: false,
      },
      foreignJob: false,
      ownJobId: "long-v1",
      active: false,
      error: null,
    });
    const { container } = renderPanel();
    const root = within(container);

    const banner = root.getByTestId("backtest-run-mismatch");
    expect(banner.textContent).toMatch(
      /Direction: the form requested SHORT, the job ran LONG\./
    );
    expect(banner.textContent).toMatch(/marked production comparable/);
    expect(root.queryByTestId("backtest-result-header")).toBeNull();
    expect(root.queryByTestId("backtest-foreign-run")).toBeNull();
    expect(container.textContent).not.toMatch(/Net profit/);
    expect(container.textContent).not.toMatch(/Production comparable:\s*YES/);
  });

  it("22. A job this form did not start is labeled as a different run", () => {
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "other-tab-7",
        symbols: ["ETHUSDT", "SOLUSDT"],
        timeframes: ["4h"],
        direction: "LONG",
        strategy_id: "COMBO_02_V2_1_A",
        production_comparable: false,
        research_only: true,
        rows: [],
      },
      request: null,
      foreignJob: true,
      ownJobId: null,
      active: false,
      error: null,
    });
    const { container } = renderPanel();
    const root = within(container);

    const banner = root.getByTestId("backtest-foreign-run");
    expect(banner.textContent).toMatch(/different run than the form/i);
    expect(banner.textContent).toMatch(/other-tab-7/);
    expect(banner.textContent).toMatch(/COMBO_02 V2\.1-A/);
    expect(banner.textContent).toMatch(/LONG\+SHORT \(native\)/);
    expect(banner.textContent).toMatch(/ETHUSDT, SOLUSDT/);
    expect(banner.textContent).toMatch(/4h/);
    expect(root.queryByTestId("backtest-run-mismatch")).toBeNull();
    expect(root.getByTestId("backtest-result-header").textContent).toMatch(
      /Direction:\s*LONG\+SHORT \(native\)/
    );
  });

  it("23. The result header describes a SHORT research job while the form is on LONG", async () => {
    const user = userEvent.setup();
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "short-research",
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        direction: "SHORT",
        strategy_id: "COMBO_04_HTF_SHORT",
        combo_version: "v1",
        production_comparable: false,
        research_only: true,
        mismatch_reasons: ["direction_mismatch"],
        principal_usd: 1000,
        rows: [
          {
            combination_id: "COMBO_04_HTF",
            symbol: "BTCUSDT",
            timeframe: "1h",
            direction: "SHORT",
            sample_size: 0,
            production_comparable: false,
            research_only: true,
            risk_usd: 20,
            bars_loaded: 100,
            trades: [],
          },
        ],
      },
      request: null,
      foreignJob: false,
      active: false,
      error: null,
    });
    const { container } = renderPanel();
    const root = within(container);
    await selectFrozenV1(user, root);
    expect(root.getAllByText(/Direction:\s*LONG/i).length).toBeGreaterThan(0);

    expect(root.queryByTestId("backtest-run-mismatch")).toBeNull();
    const header = root.getByTestId("backtest-result-header").textContent || "";
    expect(header).toMatch(/Strategy:\s*COMBO_04_HTF_SHORT/);
    expect(header).toMatch(/Direction:\s*SHORT/);
    expect(header).toMatch(/SHORT status:\s*RESEARCH_ONLY/);
    expect(header).toMatch(/Production comparable:\s*NO/);
    expect(header).toMatch(/Reasons: direction_mismatch/);
    expect(header).toMatch(/Research-only status:\s*YES/);
    expect(header).toMatch(/HTF requirement:\s*1h \+ 4h bearish alignment/);
    expect(header).not.toMatch(/bullish/);
  });

  it("23a. The result header status and pooled line follow the job", () => {
    Element.prototype.scrollIntoView = vi.fn();
    const jobWith = (productionComparable: boolean | undefined) => ({
      status: "done",
      job_id: "header-labels",
      symbols: ["BTCUSDT", "ETHUSDT"],
      timeframes: ["1h"],
      direction: "LONG",
      strategy_id: "COMBO_02_V1",
      combo_version: "v1",
      production_comparable: productionComparable,
      principal_usd: 1000,
      rows: [
        {
          combination_id: "COMBO_02",
          symbol: "BTCUSDT",
          timeframe: "1h",
          direction: "LONG",
          sample_size: 2,
          average_R: 0.5,
          pnl_usd: 50,
          pnl_usd_net: 40,
          risk_usd: 20,
          bars_loaded: 100,
          trades: [],
          equity_curve_r: [0, 0.5],
        },
        {
          combination_id: "COMBO_02",
          symbol: "ETHUSDT",
          timeframe: "1h",
          direction: "LONG",
          sample_size: 3,
          average_R: -0.1,
          pnl_usd: -5,
          pnl_usd_net: -12.5,
          pnl_model: "CAUSAL_NEXT_OPEN",
          risk_usd: 20,
          bars_loaded: 100,
          equity_curve_r: [0, -0.1],
          trades: [
            {
              trade_id: 1,
              symbol: "ETHUSDT",
              timeframe: "1h",
              direction: "LONG",
              entry_price: 100,
              exit_price: 99,
              stop_price: 99,
              outcome: "SL",
              net_pnl_usd: -12.5,
              r_net: -0.63,
              signal_time: "2026-02-14T22:00:00+00:00",
              exit_time: "2026-02-14T23:00:00+00:00",
            },
          ],
        },
      ],
    });
    const base = { request: null, foreignJob: false, active: false, error: null };

    useBacktestJobStore.setState({ job: jobWith(true), ...base });
    const first = renderPanel();
    const header = within(first.container).getByTestId("backtest-result-header");
    expect(header.textContent).toMatch(/Status:\s*Paper forward-test/);
    expect(header.textContent).not.toMatch(/Status:\s*Research/);
    expect(header.textContent).toContain(
      "Pooled n=5 · avg R gross=0.14 · gross PnL=+$45.00 · net PnL=+$27.50"
    );

    const titled = Array.from(first.container.querySelectorAll("td[title]")).filter((td) =>
      /causal net R/.test(td.getAttribute("title") || "")
    );
    expect(titled).toHaveLength(1);
    expect(titled[0].getAttribute("title")).toBe(
      "This line is causal net R from the trades, not gross engine R."
    );
    expect(titled[0].textContent).toBe("causal net R");
    expect(titled[0].closest("tr")?.textContent).toMatch(/ETHUSDT/);
    first.unmount();

    for (const flag of [false, undefined]) {
      useBacktestJobStore.setState({ job: jobWith(flag), ...base });
      const next = renderPanel();
      const text =
        within(next.container).getByTestId("backtest-result-header").textContent || "";
      expect(text).toMatch(/Status:\s*Research(?! only)/);
      expect(text).not.toMatch(/Paper forward-test/);
      expect(text).toContain("net PnL=+$27.50");
      next.unmount();
    }
  });

  it("24. Blotter win rate and max drawdown in $ come from the cell's trades", () => {
    const trade = (trade_no: number, net_pnl_usd: number) => ({
      trade_no,
      symbol: "BTCUSDT",
      timeframe: "1h",
      direction: "LONG",
      entry_price: 100,
      stop_price: 99,
      net_pnl_usd,
      signal_time: `2026-02-0${trade_no}T00:00:00+00:00`,
      exit_time: `2026-02-0${trade_no}T04:00:00+00:00`,
    });
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "blotter-stats",
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        direction: "LONG",
        strategy_id: "COMBO_02_V1",
        principal_usd: 1000,
        rows: [
          {
            combination_id: "COMBO_02",
            symbol: "BTCUSDT",
            timeframe: "1h",
            direction: "LONG",
            sample_size: 4,
            pnl_usd_net: 5,
            win_rate: 0.9,
            max_drawdown_usd: 999,
            max_drawdown_R: 3.89,
            risk_usd: 20,
            bars_loaded: 100,
            equity_curve_r: [1, 0.5, -0.25, 0.25],
            trades: [trade(1, 20), trade(2, -10), trade(3, -15), trade(4, 10)],
          },
        ],
      },
      request: null,
      foreignJob: false,
      active: false,
      error: null,
    });
    const { container } = renderPanel();
    const root = within(container);

    expect(container.textContent).toMatch(/win rate \(net > 0\) 50%/);
    expect(container.textContent).toMatch(/max DD \$25\.00 \/ 3\.89R gross/);
    expect(container.textContent).not.toMatch(/causal R/);
    expect(container.textContent).toContain("Principal $1000.00 → total $1005.00 · net +$5.00");
    expect(root.getByTestId("equity-curve-label").textContent).toBe(
      "Equity curve (gross R, equity_curve_r)"
    );
    // $ basis: mean net $1.25 per trade over 1R = $20.
    expect(container.textContent).toMatch(/\$ basis avg R \(1R = \$20\.00\): gross — · net 0\.06/);
  });

  it("shows causal P&L as primary and labels the legacy book as optimistic", () => {
    Element.prototype.scrollIntoView = vi.fn();
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "causal-display",
        direction: "SHORT",
        strategy_id: "COMBO_02_V2",
        paper_trade_created: false,
        live_trade_created: false,
        telegram_sent: false,
        principal_usd: 1000,
        limit: 70,
        rows: [
          {
            combination_id: "COMBO_02_V2",
            symbol: "SOLUSDT",
            timeframe: "1h",
            direction: "SHORT",
            sample_size: 1,
            average_R: 0.4,
            average_R_net: -0.069,
            pnl_usd: 10,
            pnl_usd_net: -16.03,
            fees_usd: -1.25,
            win_rate: 0.3288,
            max_drawdown_usd: 337.22,
            max_drawdown_R: 19.27,
            pnl_model: "CAUSAL_NEXT_OPEN",
            risk_usd: 20,
            bars_loaded: 100,
            warmup_bars: 30,
            bars_evaluated: 70,
            optimistic_comparison: {
              model: "LEGACY_SIGNAL_CLOSE",
              label:
                "Optimistic comparison — LEGACY_SIGNAL_CLOSE fills at the signal close and does not apply next-open slippage.",
              net_pnl: 88.54,
              net_r: -0.076,
              fees: -87.2,
              winners: 24,
              trades: 73,
              win_rate: 0.3288,
              max_drawdown_usd: 295.74,
              max_drawdown_r: 17.58,
            },
            trades: [
              {
                trade_id: 7,
                symbol: "SOLUSDT",
                timeframe: "1h",
                direction: "SHORT",
                entry_price: 88.474,
                exit_price: 87.62,
                stop_price: 87.66,
                outcome: "SL",
                net_pnl_usd: -19.14,
                r_net: -1.09,
                fee_total_usd: -1.59,
                signal_time: "2026-02-14T22:00:00+00:00",
                exit_time: "2026-02-14T23:00:00+00:00",
              },
            ],
          },
        ],
      },
      active: false,
      error: null,
    });
    const { container } = renderPanel();
    const root = within(container);
    expect(root.getAllByText(/causal next-open/i).length).toBeGreaterThan(0);
    expect(container.textContent).toMatch(/\$\-16\.03/);
    expect(container.textContent).toMatch(/net R -0\.07/i);
    expect(container.textContent).toMatch(/win rate \(net > 0\) 0%/i);
    expect(container.textContent).toMatch(/max DD \$19\.14 \/ 19\.27 causal R/);
    expect(container.textContent).not.toMatch(/R gross · fees/);
    // The total is principal plus the trades' net (1000 - 19.14); net stays the row figure.
    expect(container.textContent).toContain("Principal $1000.00 → total $980.86 · net $-16.03");
    // 70 evaluated bars match the 70 requested; the 30 warm-up bars are not a mismatch.
    expect(container.textContent).toMatch(/SHORT · 70 bars \(plus 30 warm-up, /);
    expect(container.textContent).not.toMatch(/100 loaded/);
    expect(container.textContent).toMatch(/\(0 TP, 1 stop-outs\)/);
    expect(container.textContent).not.toMatch(/\d winners,/);
    const optimistic = root.getByTestId("optimistic-comparison");
    expect(optimistic.textContent).toMatch(/Optimistic comparison — LEGACY_SIGNAL_CLOSE/i);
    expect(optimistic.textContent).toMatch(/\+\$88\.54/);
    expect(optimistic.textContent).toMatch(/LEGACY_SIGNAL_CLOSE/);
    const detail = root.getByTestId("causal-trade-detail");
    expect(detail.textContent).toMatch(/Trade 7 SHORT/);
    expect(detail.textContent).toMatch(/entry 88\.4740/);
    expect(detail.textContent).toMatch(/outcome SL/);
    expect(detail.textContent).toMatch(/net \$\-19\.14/);
    expect(detail.textContent).toMatch(/R -1\.09/);
    expect(detail.textContent).toMatch(/fee \$\-1\.59/);
  });

  it("keeps long, short, hedge, and grid in separate books", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    expect(root.getByRole("tab", { name: "Short trades" }).getAttribute("aria-selected")).toBe("true");
    expect(root.queryByRole("button", { name: "COMBO_02 V2.1-A" })).toBeNull();
    expect(root.queryByRole("option", { name: "SD + CHoCH + BOS" })).toBeNull();

    await user.click(root.getByRole("tab", { name: "Long trades" }));
    expect(root.getByDisplayValue("Passed long strategies (auto)")).toBeTruthy();
    expect(root.getByRole("button", { name: "COMBO_03_TRANSITION_B · 1h LONG" })).toBeTruthy();
    expect(root.queryByRole("button", { name: "COMBO_02_V2 · 1h SHORT" })).toBeNull();
    expect(root.getAllByText(/Direction:\s*LONG/i).length).toBeGreaterThan(0);

    await user.click(root.getByRole("tab", { name: "Hedge trades" }));
    expect(root.getByRole("button", { name: "COMBO_02 V2.1-A" })).toBeTruthy();
    expect(root.getByText(/LONG and SHORT together/i)).toBeTruthy();
    expect(root.queryByRole("button", { name: "COMBO_02_V2 · 1h SHORT" })).toBeNull();

    await user.click(root.getByRole("tab", { name: "Grid trades" }));
    expect(await root.findByTestId("grid-trading")).toBeTruthy();
    expect(root.queryByLabelText("Strategy")).toBeNull();
  });

  it("loads eligible screener coins without dropping the strategy selector", async () => {
    useBacktestHandoffStore.getState().queue(["WIFUSDT", "LINKUSDT"]);
    const { container } = renderPanel();
    const root = within(container);
    await waitFor(() => {
      expect(coinsIn(root, "selected-coins")).toEqual(["LINKUSDT"]);
    });
    expect(root.getByTitle(/^LINKUSDT/)).toBeTruthy();
    expect(root.queryByTitle(/WIFUSDT/)).toBeNull();
    expect(root.getByTestId("coin-filter-notice").textContent).toBe(
      "WIFUSDT is not in the large-cap or mid-cap book."
    );
    expect(root.getByDisplayValue("Passed strategies (auto)")).toBeTruthy();
    expect(root.getAllByText(/Strategy:\s*Passed strategies \(auto\)/i).length).toBeGreaterThan(0);
    expect(root.getByTestId("auto-timeframe-note").textContent).toMatch(/Auto timeframe/i);
  });

  it("opens grid backtest on this page and returns to the strategy book", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("tab", { name: "Grid trades" }));
    expect(await root.findByTestId("grid-trading")).toBeTruthy();
    expect(root.queryByLabelText("Strategy")).toBeNull();
    await user.click(root.getByRole("tab", { name: "Short trades" }));
    expect(root.getByLabelText("Strategy")).toBeTruthy();
  });

  it("offers every large and mid cap coin on first render and nothing else", () => {
    const { container } = renderPanel();
    const root = within(container);

    const selected = coinsIn(root, "selected-coins");
    const available = coinsIn(root, "available-coins");
    expect(selected).toEqual(DEFAULT_COINS);
    expect(available).toEqual(CAP_BOOK.filter((s) => !DEFAULT_COINS.includes(s)));
    expect([...selected, ...available].sort()).toEqual([...CAP_BOOK].sort());
    expect(available.filter((s) => !CAP_BOOK.includes(s))).toEqual([]);

    expect(coinsIn(root, "available-coins-large")).toEqual(
      BACKTEST_LARGE_SYMBOLS.filter((s) => !DEFAULT_COINS.includes(s))
    );
    expect(coinsIn(root, "available-coins-mid")).toEqual([...BACKTEST_MID_SYMBOLS]);
    for (const s of available) {
      expect(root.getByTitle(`${s} — not in this run, click to add`)).toBeTruthy();
    }
    expect(root.queryByTitle(/WIFUSDT|PEPEUSDT/)).toBeNull();
    expect(root.getByTestId("coin-rule").textContent).toBe(
      "Large cap and mid cap coins only (cap book)."
    );
    expect(root.queryByTestId("coin-filter-notice")).toBeNull();
  });

  it("selects every large and mid cap coin from its chip", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    const toAdd = CAP_BOOK.filter((s) => !DEFAULT_COINS.includes(s));
    expect(toAdd.length).toBe(24);
    for (const s of toAdd) {
      await user.click(root.getByTitle(`${s} — not in this run, click to add`));
      expect(coinsIn(root, "selected-coins")).toContain(s);
    }

    expect(coinsIn(root, "selected-coins")).toEqual([...DEFAULT_COINS, ...toAdd]);
    expect([...coinsIn(root, "selected-coins")].sort()).toEqual([...CAP_BOOK].sort());
    expect(coinsIn(root, "available-coins")).toEqual([]);
    expect(root.queryByTestId("coin-filter-notice")).toBeNull();
  }, 30000);

  it("does not add a typed coin outside the cap book with the Add button", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    await user.type(root.getByPlaceholderText(/Type a large or mid cap coin/i), "WIF");
    await user.click(root.getByRole("button", { name: /^Add$/ }));

    const notice = root.getByTestId("coin-filter-notice");
    expect(notice.textContent).toBe("WIFUSDT is not in the large-cap or mid-cap book.");
    expect(notice.getAttribute("role")).toBe("status");
    expect(coinsIn(root, "selected-coins")).toEqual(DEFAULT_COINS);
    expect(coinsIn(root, "available-coins")).not.toContain("WIFUSDT");
    expect(root.queryByTitle(/WIFUSDT/)).toBeNull();
  });

  it("does not add a typed coin outside the cap book with Enter", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    await user.type(root.getByPlaceholderText(/Type a large or mid cap coin/i), "wif{Enter}");

    expect(root.getByTestId("coin-filter-notice").textContent).toBe(
      "WIFUSDT is not in the large-cap or mid-cap book."
    );
    expect(coinsIn(root, "selected-coins")).toEqual(DEFAULT_COINS);
    expect(root.queryByTitle(/WIFUSDT/)).toBeNull();
  });

  it("adds a typed mid cap coin that is not in the default selection", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    expect(coinsIn(root, "selected-coins")).not.toContain("INJUSDT");
    await user.type(root.getByPlaceholderText(/Type a large or mid cap coin/i), "inj");
    await user.click(root.getByRole("button", { name: /^Add$/ }));

    expect(coinsIn(root, "selected-coins")).toEqual([...DEFAULT_COINS, "INJUSDT"]);
    expect(coinsIn(root, "available-coins")).not.toContain("INJUSDT");
    expect(root.queryByTestId("coin-filter-notice")).toBeNull();
  });

  it("adds only the cap book coin from a mixed typed entry", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    await user.type(root.getByPlaceholderText(/Type a large or mid cap coin/i), "INJ, FOO{Enter}");

    expect(coinsIn(root, "selected-coins")).toEqual([...DEFAULT_COINS, "INJUSDT"]);
    expect(root.getByTestId("coin-filter-notice").textContent).toBe(
      "FOOUSDT is not in the large-cap or mid-cap book."
    );
    expect(root.queryByTitle(/FOOUSDT/)).toBeNull();
  });

  it("leaves the current coins selected when a screener handoff has no cap book coin", async () => {
    useBacktestHandoffStore.getState().queue(["WIFUSDT", "PEPEUSDT"]);
    const { container } = renderPanel();
    const root = within(container);

    const notice = await root.findByTestId("coin-filter-notice");
    expect(notice.textContent).toMatch(/WIFUSDT/);
    expect(notice.textContent).toMatch(/PEPEUSDT/);
    expect(notice.textContent).toMatch(/not in the large-cap or mid-cap book/);
    expect(coinsIn(root, "selected-coins")).toEqual(DEFAULT_COINS);
    expect(root.queryByTitle(/WIFUSDT|PEPEUSDT/)).toBeNull();
    expect(root.getByDisplayValue("Passed strategies (auto)")).toBeTruthy();
  });

  it("posts only cap book symbols after a rejected coin was typed", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    const input = root.getByPlaceholderText(/Type a large or mid cap coin/i);
    await user.type(input, "WIF{Enter}");
    await user.type(input, "PEPE, INJ");
    await user.click(root.getByRole("button", { name: /^Add$/ }));
    expect(root.getByTestId("coin-filter-notice").textContent).toMatch(/PEPEUSDT/);

    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startRegimeBacktest).toHaveBeenCalledTimes(1);
    });
    const body = vi.mocked(startRegimeBacktest).mock.calls.at(-1)?.[0];
    expect(body?.symbols).toEqual([...DEFAULT_COINS, "INJUSDT"]);
  });

  it("posts only the cap book coin from a mixed screener handoff", async () => {
    const user = userEvent.setup();
    useBacktestHandoffStore.getState().queue(["WIFUSDT", "LINKUSDT"]);
    const { container } = renderPanel();
    const root = within(container);
    await waitFor(() => {
      expect(coinsIn(root, "selected-coins")).toEqual(["LINKUSDT"]);
    });
    expect(root.getByTestId("coin-filter-notice").textContent).toMatch(/WIFUSDT/);

    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startRegimeBacktest).toHaveBeenCalledTimes(1);
    });
    const body = vi.mocked(startRegimeBacktest).mock.calls.at(-1)?.[0];
    expect(body?.symbols).toEqual(["LINKUSDT"]);
  });
  describe("audit 2026-10-17 fixes", () => {
    beforeEach(() => {
      chartTrades.length = 0;
      Element.prototype.scrollIntoView = vi.fn();
    });

    it("1.15 switching books restores the new book's coins, strategy and direction", async () => {
      const user = userEvent.setup();
      const { container } = renderPanel();
      const root = within(container);

      await user.click(root.getByRole("tab", { name: "Long trades" }));
      await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), "COMBO_02_V1");
      await selectOnlySymbol(user, root, "BTCUSDT");
      expect(coinsIn(root, "selected-coins")).toEqual(["BTCUSDT"]);

      await user.click(root.getByRole("tab", { name: "Short trades" }));
      expect(coinsIn(root, "selected-coins")).toEqual(DEFAULT_COINS);
      expect((root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement).value).toBe(
        "PASSED_AUTO",
      );
      expect(root.getAllByText(/Direction:\s*SHORT/).length).toBeGreaterThan(0);

      // Same for hedge, and back to long.
      await selectOnlySymbol(user, root, "SOLUSDT");
      await user.click(root.getByRole("tab", { name: "Hedge trades" }));
      expect(coinsIn(root, "selected-coins")).toEqual(DEFAULT_COINS);
      await selectOnlySymbol(user, root, "ETHUSDT");
      await user.click(root.getByRole("tab", { name: "Long trades" }));
      expect(coinsIn(root, "selected-coins")).toEqual(DEFAULT_COINS);
      expect(root.getAllByText(/Direction:\s*LONG/).length).toBeGreaterThan(0);

      // Merged with 0ff796f: the long book's Passed (auto) run goes to the regime book.
      vi.mocked(startRegimeBacktest).mockClear();
      await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
      await waitFor(() => {
        expect(startRegimeBacktest).toHaveBeenCalledTimes(1);
      });
      expect(startBacktestJob).not.toHaveBeenCalled();
      expect(vi.mocked(startRegimeBacktest).mock.calls.at(-1)?.[0]?.symbols).toEqual(DEFAULT_COINS);
    });

    it("1.15 choosing Passed (auto) again in the same book keeps the coins", async () => {
      const user = userEvent.setup();
      const { container } = renderPanel();
      const root = within(container);
      const select = root.getByRole("combobox", { name: /^Strategy/i });
      await user.selectOptions(select, "COMBO_01");
      await selectOnlySymbol(user, root, "BTCUSDT");
      await user.selectOptions(select, "PASSED_AUTO");
      expect(coinsIn(root, "selected-coins")).toEqual(["BTCUSDT"]);
    });

    it("1.42b leverage below 1 clamps to 1, zero included; an empty field is the 2x default", () => {
      const { container } = renderPanel();
      const root = within(container);
      const lev = root.getByLabelText(/Leverage/i) as HTMLInputElement;
      for (const [typed, shown] of [
        ["0", "1"],
        ["0.5", "1"],
        ["-3", "1"],
        ["5", "5"],
        ["", "2"],
      ] as const) {
        fireEvent.change(lev, { target: { value: typed } });
        expect(`${typed}->${lev.value}`).toBe(`${typed}->${shown}`);
      }
    });

    const doneJob = (extra: Record<string, unknown> = {}, rowExtra: Record<string, unknown> = {}): BacktestJob =>
      ({
        status: "done",
        job_id: "in-sample-check",
        direction: "LONG",
        strategy_id: "COMBO_02_V1",
        paper_trade_created: false,
        live_trade_created: false,
        telegram_sent: false,
        rows: [
          {
            combination_id: "COMBO_02",
            symbol: "BTCUSDT",
            timeframe: "1h",
            direction: "LONG",
            sample_size: 0,
            risk_usd: 20,
            bars_loaded: 100,
            trades: [],
            ...rowExtra,
          },
        ],
        ...extra,
      }) as BacktestJob;

    it("1.50 shows the in-sample warning only when the job or a row says in_sample", () => {
      for (const [label, job, shown] of [
        ["no field", doneJob(), false],
        ["job false", doneJob({ in_sample: false }), false],
        ["job true", doneJob({ in_sample: true }), true],
        ["row true", doneJob({}, { in_sample: true }), true],
      ] as const) {
        useBacktestJobStore.setState({ job, request: null, foreignJob: false, active: false, error: null });
        const view = renderPanel();
        const warning = within(view.container).queryByTestId("backtest-in-sample-warning");
        expect(`${label}:${warning != null}`).toBe(`${label}:${shown}`);
        if (warning) expect(warning.textContent).toMatch(/^In-sample only:/);
        view.unmount();
      }
    });

    // OSE trade dicts carry no symbol/timeframe; outcomes are target / stop / timeout.
    const oseTrade = (trade_no: number, outcome: string, net: number) =>
      ({
        trade_no,
        trade_id: `t${trade_no}`,
        direction: "LONG",
        entry_price: 100,
        stop_price: 98,
        exit_price: outcome === "target" ? 104 : outcome === "stop" ? 98 : 100.5,
        tp1: 104,
        outcome,
        net_pnl_usd: net,
        signal_time: `2026-09-0${trade_no}T10:00:00+00:00`,
        exit_time: `2026-09-0${trade_no}T14:00:00+00:00`,
      }) as unknown as StrategyTradeRow;
    const oseJob = (): BacktestJob => ({
      status: "done",
      job_id: "ose-d",
      symbols: ["ETHUSDT"],
      timeframes: ["15m"],
      direction: "LONG",
      strategy_id: "OSE_D_faster_confirmation",
      combo_version: "engine-ose",
      production_comparable: false,
      research_only: true,
      principal_usd: 1000,
      limit: 5760,
      paper_trade_created: false,
      live_trade_created: false,
      telegram_sent: false,
      rows: [
        {
          combination_id: "OSE_D_faster_confirmation",
          engine_family: "ohlcv_structure_experiment",
          engine_fill_model: "next-open",
          symbol: "ETHUSDT",
          timeframe: "15m",
          direction: "LONG",
          sample_size: 4,
          risk_usd: 20,
          bars_loaded: 5760,
          period_start: "2026-09-01T00:00:00+00:00",
          period_end: "2026-09-30T00:00:00+00:00",
          production_comparable: false,
          research_only: true,
          trades: [
            oseTrade(1, "stop", -20),
            oseTrade(2, "target", 40),
            oseTrade(3, "timeout", 2),
            oseTrade(4, "stop", -20),
          ],
        },
      ],
    });

    it("1.52 the trade chart overlay gets the row's symbol and timeframe for OSE trades", () => {
      useBacktestJobStore.setState({ job: oseJob(), request: null, foreignJob: false, active: false, error: null });
      const { container } = renderPanel();
      expect(within(container).queryByTestId("backtest-run-mismatch")).toBeNull();
      expect(chartTrades.length).toBeGreaterThan(0);
      const last = chartTrades.at(-1);
      expect(last?.symbol).toBe("ETHUSDT");
      expect(last?.timeframe).toBe("15m");
    });

    it("1.52 a trade's own symbol and timeframe still win over the row's", () => {
      const job = oseJob();
      job.rows![0].trades = [{ ...oseTrade(1, "stop", -20), symbol: "BTCUSDT", timeframe: "1h" }];
      useBacktestJobStore.setState({ job, request: null, foreignJob: false, active: false, error: null });
      renderPanel();
      expect(chartTrades.at(-1)).toMatchObject({ symbol: "BTCUSDT", timeframe: "1h" });
    });

    it("1.53 the run lead counts OSE target as TP and stop as a stop-out, timeout as neither", () => {
      useBacktestJobStore.setState({ job: oseJob(), request: null, foreignJob: false, active: false, error: null });
      const { container } = renderPanel();
      expect(container.textContent).toMatch(/The 4 trades run from .* \(1 TP, 2 stop-outs\)\./);
    });

    it("1.53 the run lead still counts TP1/TP2 and SL outcomes", () => {
      const job = doneJob({ symbols: ["BTCUSDT"], timeframes: ["1h"] });
      job.rows![0].trades = [
        { ...oseTrade(1, "TP1", 20), symbol: "BTCUSDT", timeframe: "1h" },
        { ...oseTrade(2, "TP2", 30), symbol: "BTCUSDT", timeframe: "1h" },
        { ...oseTrade(3, "SL", -20), symbol: "BTCUSDT", timeframe: "1h" },
      ];
      useBacktestJobStore.setState({ job, request: null, foreignJob: false, active: false, error: null });
      const { container } = renderPanel();
      expect(container.textContent).toMatch(/\(2 TP, 1 stop-outs\)/);
    });

    it("COMBO_02_V2 and COMBO_02_V2_1B are hedge-book runs, labelled LONG+SHORT, not long or short", async () => {
      const user = userEvent.setup();
      const { container } = renderPanel();
      const root = within(container);
      const values = () =>
        Array.from((root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement).options).map(
          (o) => o.value,
        );

      expect(values()).not.toContain("COMBO_02_V2");
      expect(values()).not.toContain("COMBO_02_V2_1B");
      await user.click(root.getByRole("tab", { name: "Long trades" }));
      expect(values()).not.toContain("COMBO_02_V2");
      expect(values()).not.toContain("COMBO_02_V2_1B");

      await user.click(root.getByRole("tab", { name: "Hedge trades" }));
      expect(values()).toEqual(
        expect.arrayContaining(["PASSED_AUTO", "COMBO_02_V2_1_A", "COMBO_02_V2", "COMBO_02_V2_1B"]),
      );
      expect(root.getByRole("option", { name: "COMBO_02_V2 · 1h LONG+SHORT" })).toBeTruthy();
      expect(root.getByRole("option", { name: "COMBO_02_V2_1B · 1h LONG+SHORT" })).toBeTruthy();
      // Only V2.1-A is the hedge book's passed run.
      expect(root.getByRole("button", { name: "COMBO_02 V2.1-A" })).toBeTruthy();
      expect(root.queryByRole("button", { name: /^COMBO_02_V2/ })).toBeNull();
    });

    for (const [id, version] of [
      ["COMBO_02_V2", "v2-combo02-adaptive-3-regime"],
      ["COMBO_02_V2_1B", "v2.1-b-choppy-15m"],
    ] as const) {
      it(`${id} on the hedge book posts its own id like V2.1-A: native LONG+SHORT, no side buttons`, async () => {
        const user = userEvent.setup();
        const { container } = renderPanel();
        const root = within(container);
        await user.click(root.getByRole("tab", { name: "Hedge trades" }));
        await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), id);

        expect(root.queryByRole("button", { name: /^LONG$/ })).toBeNull();
        expect(root.queryByTitle(/SHORT research-only/i)).toBeNull();
        expect(root.getAllByText(/Direction:\s*LONG\+SHORT \(native\)/).length).toBeGreaterThan(0);
        expect(
          root.getAllByText(new RegExp(`${id} runs native LONG\\+SHORT \\(direction filter off\\)`)).length,
        ).toBeGreaterThan(0);

        await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
        await waitFor(() => {
          expect(startBacktestJob).toHaveBeenCalledTimes(1);
        });
        expect(vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0]).toMatchObject({
          combination_id: id,
          strategy_id: id,
          combo_version: version,
          // Same as V2.1-A: LONG is the request placeholder for a native LONG+SHORT run.
          direction: "LONG",
          research_risk_override: true,
        });
      });
    }

    const v2Job = (direction: "LONG" | "SHORT"): BacktestJob => ({
      status: "done",
      job_id: `v2-${direction}`,
      symbols: ["BTCUSDT"],
      timeframes: ["1h"],
      direction,
      strategy_id: "COMBO_02_V2",
      combo_version: "v2-combo02-adaptive-3-regime",
      production_comparable: false,
      research_only: true,
      principal_usd: 1000,
      paper_trade_created: false,
      live_trade_created: false,
      telegram_sent: false,
      rows: [
        {
          combination_id: "COMBO_02_V2",
          symbol: "BTCUSDT",
          timeframe: "1h",
          direction,
          sample_size: 2,
          risk_usd: 20,
          bars_loaded: 100,
          production_comparable: false,
          research_only: true,
          trades: [
            { ...oseTrade(1, "TP1", 20), symbol: "BTCUSDT", timeframe: "1h", direction: "LONG" },
            { ...oseTrade(2, "SL", -20), symbol: "BTCUSDT", timeframe: "1h", direction: "SHORT" },
          ],
        },
      ],
    });

    it("a hedge-book COMBO_02_V2 run with LONG and SHORT trades is shown, labelled LONG+SHORT", () => {
      useBacktestJobStore.setState({
        job: v2Job("LONG"),
        request: {
          symbols: ["BTCUSDT"],
          timeframes: ["1h"],
          direction: "LONG",
          strategy_id: "COMBO_02_V2",
          research_risk_override: true,
        },
        foreignJob: false,
        ownJobId: "v2-LONG",
        active: false,
        error: null,
      });
      const { container } = renderPanel();
      const root = within(container);
      expect(root.queryByTestId("backtest-run-mismatch")).toBeNull();
      expect(root.getByTestId("backtest-trade-blotter")).toBeTruthy();
      expect(root.getAllByText(/LONG\+SHORT \(native\)/).length).toBeGreaterThan(0);
    });

    it("a COMBO_02_V2 SHORT request that returns LONG trades is still hidden by the mismatch guard", () => {
      useBacktestJobStore.setState({
        job: v2Job("SHORT"),
        request: {
          symbols: ["BTCUSDT"],
          timeframes: ["1h"],
          direction: "SHORT",
          strategy_id: "COMBO_02_V2",
          research_risk_override: true,
        },
        foreignJob: false,
        ownJobId: "v2-SHORT",
        active: false,
        error: null,
      });
      const { container } = renderPanel();
      const banner = within(container).getByTestId("backtest-run-mismatch");
      expect(banner.textContent).toMatch(/trade_no 1 is LONG in a SHORT run\./);
    });
  });
});

describe("BacktestPanel regime-book run", () => {
  type T = Record<string, unknown>;

  function singleJob(): T {
    const trade = {
      trade_no: 1,
      symbol: "BTCUSDT",
      timeframe: "1h",
      direction: "LONG",
      entry_price: 100,
      stop_price: 99,
      tp1: 102,
      exit_price: 101,
      outcome: "TP1",
      net_pnl_usd: 20,
      gross_pnl_usd: 21,
      fee_total_usd: -1,
      r_net: 1,
      signal_time: "2025-01-01T00:00:00+00:00",
      exit_time: "2025-01-01T04:00:00+00:00",
    };
    return {
      status: "done",
      job_id: "single-job",
      symbols: ["BTCUSDT"],
      timeframes: ["1h"],
      direction: "LONG",
      strategy_id: "COMBO_01",
      combination_id: "COMBO_01",
      principal_usd: 1000,
      production_comparable: false,
      research_only: true,
      rows: [
        {
          combination_id: "COMBO_01",
          symbol: "BTCUSDT",
          timeframe: "1h",
          direction: "LONG",
          sample_size: 1,
          pnl_usd: 21,
          pnl_usd_net: 20,
          fees_usd: -1,
          risk_usd: 20,
          bars_loaded: 100,
          period_start: "2025-01-01T00:00:00+00:00",
          period_end: "2025-03-31T23:00:00+00:00",
          trades: [trade],
        },
      ],
    };
  }

  function regimeJob(extra: Partial<RegimeBacktestStatus> = {}): RegimeBacktestStatus {
    return {
      status: "running",
      job_id: "regime-1",
      phase: "running",
      current: "ETHUSDT 2/3 · COMBO_01 SHORT",
      symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
      total_cells: 3,
      done_cells: 1,
      pct: 37,
      progress_percent: 37,
      walks_in_symbol: 4,
      started_at: new Date().toISOString(),
      window: {
        requested: { mode: "lookback", timeframe: "1h", start_date: null, end_date: null, limit: 5760 },
        evaluated: null,
        cap: { max_eval_bars: 20000, applied: false, symbols: [] },
      },
      ...extra,
    };
  }

  function doneRegimeJob(): RegimeBacktestStatus {
    return regimeJob({
      status: "done",
      phase: "done",
      current: "",
      done_cells: 3,
      pct: 100,
      progress_percent: 100,
      window: {
        requested: { mode: "dates", timeframe: "1h", start_date: "2025-01-01", end_date: "2025-03-31", limit: null },
        evaluated: {
          period_start: "2025-01-01T00:00:00+00:00",
          period_end: "2025-03-31T23:00:00+00:00",
          bars_loaded: 6720,
          warmup_bars: 240,
          bars_evaluated: 6480,
        },
        cap: { max_eval_bars: 20000, applied: false, symbols: [] },
      },
      fixed_config: { principal_usd: 1000, leverage: 2, taker_fee_pct: 0.04, maker_fee_pct: 0.02 },
      summary: {
        trades: 4,
        wins: 1,
        losses: 3,
        pnl_usd_net: -12.5,
        final_equity_usd: 987.5,
        max_drawdown_pct: 2,
        by_strategy: [],
      },
    });
  }

  afterEach(() => {
    cleanup();
    vi.mocked(startRegimeBacktest).mockImplementation(async () => ({ status: "STARTED" }));
    vi.mocked(fetchRegimeBacktest).mockImplementation(async () => ({ status: "idle" }));
    vi.mocked(cancelRegimeBacktest).mockImplementation(async () => ({
      status: "CANCELLED",
      job: { status: "cancelled" },
    }));
  });

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
    });
    useBacktestHandoffStore.setState({ symbols: [], revision: 0 });
    vi.mocked(startBacktestJob).mockClear();
    vi.mocked(startRegimeBacktest).mockClear();
    vi.mocked(fetchRegimeBacktest).mockClear();
    vi.mocked(cancelRegimeBacktest).mockClear();
  });

  it("R4 lookback run sends 1h, the requested bar count, and nothing the book ignores", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(startRegimeBacktest).toHaveBeenCalledTimes(1));
    const body = vi.mocked(startRegimeBacktest).mock.calls[0][0] as Record<string, unknown>;
    expect(body).toEqual({ symbols: DEFAULT_COINS, timeframe: "1h", limit: 5760 });
  });

  it("R4 calendar run sends the selected UTC days and no bar limit", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: "Calendar-range mode" }));
    const dates = container.querySelectorAll('input[type="date"]');
    fireEvent.change(dates[0], { target: { value: "2025-01-01" } });
    fireEvent.change(dates[1], { target: { value: "2025-03-31" } });
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(startRegimeBacktest).toHaveBeenCalledTimes(1));
    const body = vi.mocked(startRegimeBacktest).mock.calls[0][0] as Record<string, unknown>;
    expect(body).toEqual({
      symbols: DEFAULT_COINS,
      timeframe: "1h",
      start_date: "2025-01-01",
      end_date: "2025-03-31",
    });
  });

  it("R4 a timeframe other than 1h blocks the regime run with a reason", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await selectOnlyTimeframe(user, root, "15m");
    expect(root.getByTestId("regime-run-timeframe-block").textContent).toMatch(/1h bars only/);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    expect(startRegimeBacktest).not.toHaveBeenCalled();
    expect(container.textContent).toMatch(/The regime book runs on 1h bars only/);
  });

  it("R4 the screen says which form fields the regime book does not use", () => {
    const { container } = renderPanel();
    const scope = within(container).getByTestId("regime-run-scope");
    expect(scope.textContent).toMatch(/Not used by this run: the Principal, Risk, Leverage and Fee fields/);
    expect(scope.textContent).toMatch(/principal \$1,000/);
    expect(scope.textContent).toMatch(/latest 5,760 1h bars ≈ 240 days/);
  });

  it("R4 a refused regime run shows the server's reason and unlocks Run", async () => {
    vi.mocked(startRegimeBacktest).mockImplementation(async () => ({
      status: "REJECTED",
      error: "The regime book runs on 1h bars only; timeframe 4h is not supported.",
    }));
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(container.textContent).toMatch(/timeframe 4h is not supported/));
    expect((root.getByRole("button", { name: /^Run backtest$/i }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("R5 a regime run replaces the previous single-strategy output", async () => {
    useBacktestJobStore.setState({
      job: singleJob() as never,
      request: { symbols: ["BTCUSDT"], timeframes: ["1h"], direction: "LONG", strategy_id: "COMBO_01" } as never,
      ownJobId: "single-job",
    });
    vi.mocked(startRegimeBacktest).mockImplementation(async () => ({ status: "STARTED", job: doneRegimeJob() }));
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    expect(root.queryByTestId("backtest-trade-blotter")).not.toBeNull();
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(root.getByTestId("regime-backtest")).toBeTruthy());
    expect(root.queryByTestId("backtest-trade-blotter")).toBeNull();
    expect(useBacktestJobStore.getState().job).toBeNull();
    const output = container.querySelector("#backtest-output") as HTMLElement;
    expect(within(output).getByTestId("regime-backtest").textContent).toMatch(/current run/);
  });

  it("R6 a running regime job drives Running %, progress, and Cancel", async () => {
    vi.mocked(startRegimeBacktest).mockImplementation(async () => ({ status: "STARTED", job: regimeJob() }));
    vi.mocked(fetchRegimeBacktest).mockImplementation(async () => regimeJob());
    vi.mocked(cancelRegimeBacktest).mockImplementation(async () => ({
      status: "CANCELLED",
      job: regimeJob({ status: "cancelled", phase: "cancelled" }),
    }));
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    const runButton = await root.findByRole("button", { name: "37%" });
    expect((runButton as HTMLButtonElement).disabled).toBe(true);
    const progress = root.getByTestId("backtest-progress");
    expect(progress.textContent).toMatch(/Regime book · 37% · 1\/3 coins/);
    expect(progress.textContent).toMatch(/ETHUSDT 2\/3/);
    vi.mocked(fetchRegimeBacktest).mockImplementation(async () =>
      regimeJob({ status: "cancelled", phase: "cancelled" }),
    );
    await user.click(root.getByRole("button", { name: "Cancel" }));
    expect(cancelRegimeBacktest).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect((root.getByRole("button", { name: /^Run backtest$/i }) as HTMLButtonElement).disabled).toBe(false),
    );
    expect(root.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(root.getByTestId("regime-backtest-status").textContent).toMatch(/cancelled/);
  });

  it("R6 a regime run still going after a reload shows as the current run", async () => {
    vi.mocked(fetchRegimeBacktest).mockImplementation(async () => regimeJob());
    const { container } = renderPanel();
    const root = within(container);
    await root.findByRole("button", { name: "37%" });
    expect(root.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(root.getByTestId("regime-backtest")).toBeTruthy();
  });

  it("R7 choosing a single strategy, then running it, hides the regime panel", async () => {
    vi.mocked(startRegimeBacktest).mockImplementation(async () => ({ status: "STARTED", job: doneRegimeJob() }));
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(root.getByTestId("regime-backtest")).toBeTruthy());
    await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), "COMBO_01");
    expect(root.queryByTestId("regime-backtest")).toBeNull();
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(startBacktestJob).toHaveBeenCalledTimes(1));
    expect(root.queryByTestId("regime-backtest")).toBeNull();
  });

  it("R7 switching the trade book hides the regime panel", async () => {
    vi.mocked(startRegimeBacktest).mockImplementation(async () => ({ status: "STARTED", job: doneRegimeJob() }));
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => expect(root.getByTestId("regime-backtest")).toBeTruthy());
    await user.click(root.getByRole("tab", { name: "Long trades" }));
    expect(root.queryByTestId("regime-backtest")).toBeNull();
  });

  it("R8 the regime results show the requested and evaluated period", async () => {
    vi.mocked(startRegimeBacktest).mockImplementation(async () => ({ status: "STARTED", job: doneRegimeJob() }));
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    const period = await root.findByTestId("regime-backtest-period");
    expect(period.textContent).toMatch(/Requested: Calendar 2025-01-01 → 2025-03-31/);
    expect(period.textContent).toMatch(/Evaluated: 2025-01-01 00:00 → 2025-03-31 23:00 UTC/);
    expect(period.textContent).toMatch(/6,480 1h bars evaluated · 240 warm-up bars not traded/);
  });
});

describe("BacktestPanel VectorBT execution toggle", () => {
  afterEach(() => {
    cleanup();
  });

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

  async function runWith(vectorbt: boolean) {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("tab", { name: "Long trades" }));
    const select = root.getByRole("combobox", { name: /^Strategy/i }) as HTMLSelectElement;
    await user.selectOptions(select, "COMBO_02_V1");
    const before = select.value;
    const toggle = root.getByRole("checkbox", { name: "VectorBT execution" }) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    if (vectorbt) await user.click(toggle);
    expect(toggle.checked).toBe(vectorbt);
    // Toggling never changes the selected strategy.
    expect(select.value).toBe(before);
    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startBacktestJob).toHaveBeenCalledTimes(1);
    });
    const body = vi.mocked(startBacktestJob).mock.calls.at(-1)?.[0];
    cleanup();
    vi.mocked(startBacktestJob).mockClear();
    useBacktestJobStore.setState({ job: null, request: null, active: false, starting: false });
    return body;
  }

  it("is off by default and sends vectorbt_execution false", async () => {
    const body = await runWith(false);
    expect(body?.vectorbt_execution).toBe(false);
  });

  it("sends vectorbt_execution true and leaves the strategy identity unchanged", async () => {
    const off = await runWith(false);
    const on = await runWith(true);
    expect(on?.vectorbt_execution).toBe(true);
    for (const key of ["combination_id", "strategy_id", "combo_version", "direction"] as const) {
      expect(on?.[key]).toBe(off?.[key]);
    }
    expect(on?.strategy_id).toBe("COMBO_02_V1");
    const { vectorbt_execution: _a, ...restOn } = on ?? ({} as NonNullable<typeof on>);
    const { vectorbt_execution: _b, ...restOff } = off ?? ({} as NonNullable<typeof off>);
    expect(restOn).toEqual(restOff);
  });

  it("shows which engine ran each row and why a row stayed on the bar loop", () => {
    const base = {
      combination_id: "CANDLE12_V2",
      direction: "LONG",
      sample_size: 3,
      average_R: 0.1,
      trades: [],
      equity_curve_r: [0, 0.1],
    };
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "vbt-1",
        symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        timeframes: ["1h"],
        direction: "LONG",
        strategy_id: "CANDLE12_V2",
        vectorbt_execution: true,
        vectorbt_mismatch_total: 2,
        rows: [
          {
            ...base,
            symbol: "BTCUSDT",
            timeframe: "1h",
            execution_engine: "vectorbt",
            vectorbt_mismatches: 0,
            vectorbt_parity: { verdict: "PASS", mismatches: 0, first_mismatch_field: null },
          },
          {
            ...base,
            symbol: "ETHUSDT",
            timeframe: "1h",
            execution_engine: "bar_loop",
            vectorbt_mismatches: 2,
            vectorbt_parity: { verdict: "FAIL", mismatches: 2, first_mismatch_field: "exit_price" },
          },
          {
            ...base,
            symbol: "SOLUSDT",
            timeframe: "1h",
            execution_engine: "bar_loop",
            vectorbt_mismatches: 0,
            vectorbt_parity: { verdict: "UNAVAILABLE", reason: "vectorbt is not installed" },
          },
        ],
      },
      active: false,
      error: null,
    });
    const { container } = renderPanel();
    const labels = within(container)
      .getAllByTestId("execution-engine")
      .map((el) => el.textContent);
    expect(labels).toEqual([
      "vectorbt",
      "bar loop · 2 mismatches (exit_price)",
      "bar loop · unavailable",
    ]);
  });

  it("shows no engine label on rows from a run without the flag", () => {
    useBacktestJobStore.setState({
      job: {
        status: "done",
        job_id: "plain-1",
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        direction: "LONG",
        rows: [
          {
            combination_id: "COMBO_02",
            symbol: "BTCUSDT",
            timeframe: "1h",
            direction: "LONG",
            sample_size: 1,
            trades: [],
          },
        ],
      },
      active: false,
      error: null,
    });
    const { container } = renderPanel();
    expect(within(container).queryAllByTestId("execution-engine")).toEqual([]);
  });
});
