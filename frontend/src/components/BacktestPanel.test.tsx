/**
 * @vitest-environment jsdom
 */
import {
  cleanup,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startBacktestJob } from "../api/client";
import { useBacktestJobStore } from "../store/backtestJobStore";
import { BacktestPanel } from "./BacktestPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchResearchOhlcvRange: vi.fn(async () => ({ status: "OK", rows: [] })),
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
  };
});

vi.mock("./CandidateResearchPanel", () => ({
  CandidateResearchPanel: () => null,
}));
vi.mock("./ShortResearchPanel", () => ({
  ShortResearchPanel: () => null,
}));
vi.mock("./DynamicCandidatePipelinePanel", () => ({
  DynamicCandidatePipelinePanel: () => null,
}));
vi.mock("./BacktestTradeChart", () => ({
  BacktestTradeChart: () => null,
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

describe("BacktestPanel interaction paths", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    useBacktestJobStore.setState({
      job: null,
      error: null,
      active: false,
      starting: false,
      _gen: 0,
    });
    vi.mocked(startBacktestJob).mockClear();
  });

  it("1. BTC v1 risk rendering", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);
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

  it("5. SHORT disabled click", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    const shortBtn = root.getByTitle(/SHORT research is paused/i);
    expect(shortBtn.getAttribute("aria-disabled")).toBe("true");
    await user.click(shortBtn);

    expect(root.getByText(/SHORT research is paused\./i)).toBeTruthy();
    expect(
      root.getByText(
        /SHORT paper trading, production, and Telegram are disabled/i
      )
    ).toBeTruthy();
    expect(root.getAllByText(/SHORT status:\s*PAUSED/i).length).toBeGreaterThan(0);
    expect(root.getByRole("button", { name: /^LONG$/i })).toBeTruthy();
    expect(isSelected(shortBtn)).toBe(false);
  });

  it("6. SHORT sends no API request", async () => {
    const user = userEvent.setup();
    const { container } = renderPanel();
    const root = within(container);

    await user.click(root.getByTitle(/SHORT research is paused/i));
    expect(startBacktestJob).not.toHaveBeenCalled();

    await user.click(root.getByRole("button", { name: /^Run backtest$/i }));
    await waitFor(() => {
      expect(startBacktestJob).toHaveBeenCalled();
    });
    const body = vi.mocked(startBacktestJob).mock.calls[0][0];
    expect(body.direction).toBe("LONG");
    expect(body.direction).not.toBe("SHORT");
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
  });

  it("17. Frozen v1 banner", () => {
    const { container } = renderPanel();
    const root = within(container);
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

    expect(root.getByText(/Strategy identity:/i)).toBeTruthy();
    expect(root.getByText(/COMBO_02_V1/i)).toBeTruthy();
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
    expect(startBacktestJob).not.toHaveBeenCalled();
  });
});
