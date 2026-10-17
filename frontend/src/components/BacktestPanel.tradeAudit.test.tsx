/**
 * @vitest-environment jsdom
 *
 * Backtest-screen display fixes from the 2026-10-17 trade audit (B3–B12 and
 * the exit-order equity). Display only: every value comes from the job payload.
 */
import { cleanup, render, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useBacktestJobStore } from "../store/backtestJobStore";
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
  }),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchResearchOhlcvRange: vi.fn(async () => ({ status: "OK", rows: [] })),
    fetchStrategyRank: vi.fn(async () => null),
    fetchCapBookAgents: vi.fn(async () => ({ status: "OK", poll_interval_seconds: 3, shards: [] })),
    startBacktestJob: vi.fn(async () => ({ status: "STARTED", job: { status: "running", job_id: "x", rows: [] } })),
    fetchBacktestJobStatus: vi.fn(async () => ({ status: "idle", job_id: null, rows: [] })),
    cancelBacktestJob: vi.fn(async () => ({ status: "idle" })),
    fetchResearchOhlcvCandles: vi.fn(async () => ({ status: "OK", candles: [] })),
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

type T = Record<string, unknown>;

function trade(no: number, net: number | null, extra: T = {}): T {
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
    net_pnl_usd: net,
    gross_pnl_usd: net,
    fee_total_usd: -1,
    r_net: net == null ? null : net / 20,
    signal_time: `2025-01-0${no}T00:00:00+00:00`,
    exit_time: `2025-01-0${no}T04:00:00+00:00`,
    ...extra,
  };
}

function row(trades: T[], extra: T = {}): T {
  const net = trades.reduce((s, t) => s + Number(t.net_pnl_usd || 0), 0);
  return {
    combination_id: "COMBO_01",
    symbol: "BTCUSDT",
    timeframe: "1h",
    direction: "LONG",
    sample_size: trades.length,
    average_R: 0.1,
    pnl_usd: net,
    pnl_usd_net: net,
    fees_usd: -trades.length,
    risk_usd: 20,
    bars_loaded: 100,
    period_start: "2025-01-01T00:00:00+00:00",
    period_end: "2025-03-31T23:00:00+00:00",
    trades,
    ...extra,
  };
}

function job(rows: T[], extra: T = {}): T {
  return {
    status: "done",
    job_id: "audit-job",
    symbols: ["BTCUSDT"],
    timeframes: ["1h"],
    direction: "LONG",
    strategy_id: "COMBO_01",
    combination_id: "COMBO_01",
    principal_usd: 1000,
    production_comparable: false,
    rows,
    ...extra,
  };
}

function setJob(j: T) {
  useBacktestJobStore.setState({ job: j as never });
}

function blotterRows(container: HTMLElement): HTMLTableRowElement[] {
  const blotter = within(container).getByTestId("backtest-trade-blotter");
  return Array.from(blotter.querySelectorAll<HTMLTableRowElement>("tbody tr[role=button]"));
}

function cells(tr: HTMLTableRowElement): string[] {
  return Array.from(tr.querySelectorAll("td")).map((td) => (td.textContent || "").trim());
}

// Blotter columns
const COL = { no: 0, exit: 2, hold: 10, fees: 14, equity: 19 };

function matrixHeaders(container: HTMLElement): string[] {
  const tables = container.querySelectorAll("#backtest-output table");
  const matrix = tables[tables.length - 1];
  return Array.from(matrix.querySelectorAll("thead th")).map((th) => (th.textContent || "").trim());
}

describe("Backtest screen trade-audit display fixes", () => {
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
    });
  });

  it("B3 blotter Entry time is the next-open fill bar; the signal bar stays visible", () => {
    // Job 53c3cab8b696 BTCUSDT trade 1: signal 03:00, fill 04:00, exit 09:00, holding_bars 5.
    const t = trade(1, 28.13, {
      trade_no: undefined,
      signal_time: "2025-01-02T03:00:00+00:00",
      fill_time: "2025-01-02T04:00:00+00:00",
      fill_index: 128,
      execution_model: "CAUSAL_NEXT_OPEN",
      exit_time: "2025-01-02T09:00:00+00:00",
      holding_bars: 5,
      entry_price: 95047.5,
    });
    setJob(job([row([t], { pnl_model: "CAUSAL_NEXT_OPEN" })]));
    const { container } = renderPanel();
    const [tr] = blotterRows(container);
    const entry = within(tr).getByTestId("trade-entry-time");
    expect(within(entry).getByTestId("trade-fill-time").textContent).toBe("2025-01-02 04:00:00 UTC");
    expect(within(entry).getByTestId("trade-signal-time").textContent).toBe(
      "Signal 2025-01-02 03:00:00 UTC",
    );
    expect(entry.getAttribute("title")).toContain("Signal bar 2025-01-02 03:00:00 UTC");
    expect(cells(tr)[COL.hold]).toBe("5");
    // The row stays selected (key is unchanged by the fill time).
    expect(tr.className).toContain("ring-terminal-accent");
  });

  it("B3 falls back to the signal time when a trade has no fill_time", () => {
    setJob(job([row([trade(1, 20)])]));
    const { container } = renderPanel();
    const [tr] = blotterRows(container);
    const entry = within(tr).getByTestId("trade-entry-time");
    expect(entry.textContent).toBe("2025-01-01 00:00:00 UTC");
    expect(within(entry).queryByTestId("trade-signal-time")).toBeNull();
  });

  it("B4 a cancelled job is labelled as a partial result, not a finished run", () => {
    setJob(
      job([row([trade(1, 20)])], {
        status: "cancelled",
        symbols: ["BTCUSDT", "ETHUSDT"],
        total_cells: 2,
        done_cells: 1,
      }),
    );
    const { container } = renderPanel();
    const banner = within(container).getByTestId("backtest-partial-run");
    expect(banner.textContent).toContain("Cancelled — partial result: 1 of 2 cells");
    const header = within(container).getByTestId("backtest-result-header").textContent || "";
    expect(header).toContain("Partial (run did not finish) · Pooled n=1");
  });

  it("B4 a stalled job with rows is labelled partial; a finished job is not", () => {
    setJob(
      job([row([trade(1, 20)])], {
        status: "stalled",
        symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        total_cells: 3,
        done_cells: 1,
      }),
    );
    const first = renderPanel();
    expect(within(first.container).getByTestId("backtest-partial-run").textContent).toContain(
      "Stalled — partial result: 1 of 3 cells",
    );
    first.unmount();

    setJob(job([row([trade(1, 20)])], { total_cells: 1, done_cells: 1 }));
    const done = renderPanel();
    expect(within(done.container).queryByTestId("backtest-partial-run")).toBeNull();
    expect(done.container.textContent).not.toMatch(/Partial \(run did not finish\)/);
  });

  it("B5 coin stepper steps through the job's coins, not the form's", async () => {
    const user = userEvent.setup();
    const btc = row([trade(1, 20)]);
    const eth = row([trade(1, -5, { symbol: "ETHUSDT" })], { symbol: "ETHUSDT" });
    // Form opens with BTC, ETH, SOL; the job ran BTC and ETH only.
    setJob(job([btc, eth], { symbols: ["BTCUSDT", "ETHUSDT"], total_cells: 2, done_cells: 2 }));
    const { container } = renderPanel();
    const root = within(container);
    const out = within(container.querySelector("#backtest-output") as HTMLElement);
    const heading = () => container.querySelector("#backtest-output h2")!.textContent;
    const formStepper = () => root.getByTestId("coin-stepper").textContent || "";

    expect(out.getByText(/BTCUSDT · 1h · coin 1 of 2/)).toBeTruthy();
    expect(formStepper()).toContain("Coin 1 of 2: BTC");

    await user.click(out.getByRole("button", { name: "Next coin" }));
    expect(heading()).toBe("Backtest output · ETH");
    expect(out.getByText(/ETHUSDT · 1h · coin 2 of 2/)).toBeTruthy();
    expect(formStepper()).toContain("Coin 2 of 2: ETH");

    // Wraps to BTC instead of stepping to SOL, which the job did not run.
    await user.click(out.getByRole("button", { name: "Next coin" }));
    expect(heading()).toBe("Backtest output · BTC");
    expect(formStepper()).toContain("Coin 1 of 2: BTC");
  });

  it("B6 an unknown fee is shown as —, not -$0.00", () => {
    setJob(job([row([trade(1, 20, { fee_total_usd: null }), trade(2, 10)])]));
    const { container } = renderPanel();
    const rows = blotterRows(container).map(cells);
    expect(rows[0][COL.fees]).toBe("—");
    expect(rows[1][COL.fees]).toBe("-$1.00");
  });

  it("B7 engine rows label Max DD R and Equity R as net R, COMBO rows as gross", () => {
    setJob(
      job(
        [
          row([trade(1, 20)], {
            combination_id: "OSE_A_corrected_baseline",
            engine_family: "ohlcv_structure_experiment",
            average_R: null,
            max_drawdown_R: 1.5,
            equity_curve_r: [1],
            equity_curve_usd_net: [],
          }),
        ],
        { strategy_id: "OSE_A_corrected_baseline" },
      ),
    );
    const engine = renderPanel();
    let headers = matrixHeaders(engine.container);
    expect(headers).toContain("Max DD R (net)");
    expect(headers).toContain("Equity R (net)");
    expect(headers).not.toContain("Max DD R (gross)");
    expect(engine.container.textContent).toMatch(/max DD \$0\.00 \/ 1\.50R net/);
    expect(engine.container.textContent).not.toMatch(/R gross · fees/);
    expect(within(engine.container).getByTestId("equity-curve-label").textContent).toBe(
      "Equity curve (net R, equity_curve_r)",
    );
    engine.unmount();

    setJob(job([row([trade(1, 20)], { max_drawdown_R: 0.5, equity_curve_r: [1] })]));
    const combo = renderPanel();
    headers = matrixHeaders(combo.container);
    expect(headers).toContain("Max DD R (gross)");
    expect(headers).toContain("Equity R (gross)");
    expect(combo.container.textContent).toMatch(/0\.50R gross · fees/);
    combo.unmount();

    setJob(job([row([trade(1, 20)], { pnl_model: "CAUSAL_NEXT_OPEN", max_drawdown_R: 0.5 })]));
    const causal = renderPanel();
    headers = matrixHeaders(causal.container);
    expect(headers).toContain("Max DD R (causal net)");
    expect(headers).toContain("Equity R (causal net)");
    expect(causal.container.textContent).toMatch(/0\.50 causal R · fees/);
  });

  it("B7 keeps the frozen CANDLE12_V2 warnings", () => {
    setJob(
      job(
        [
          row([trade(1, 20)], {
            combination_id: "CANDLE12_V2",
            engine_family: "candle12",
            max_drawdown_R: 0.5,
          }),
        ],
        { strategy_id: "CANDLE12_V2" },
      ),
    );
    const { container } = renderPanel();
    const warnings = within(container).getByTestId("candle12-v2-warnings").textContent || "";
    expect(warnings).toContain("Avg R gross is net R.");
    expect(warnings).toContain("market_context uses Candle-2 close.");
    expect(warnings).toContain("HTF label is not applicable to CANDLE12_V2.");
    expect(matrixHeaders(container)).toContain("Max DD R (net)");
  });

  it("B8 pooled n counts trades when average_R is null (OSE rows)", () => {
    const ose = (symbol: string, n: number) =>
      row(
        Array.from({ length: n }, (_, i) => trade(i + 1, 1, { symbol })),
        { symbol, average_R: null, engine_family: "ohlcv_structure_experiment" },
      );
    setJob(
      job([ose("BTCUSDT", 2), ose("ETHUSDT", 3)], {
        symbols: ["BTCUSDT", "ETHUSDT"],
        strategy_id: "OSE_A_corrected_baseline",
      }),
    );
    const { container } = renderPanel();
    const header = within(container).getByTestId("backtest-result-header").textContent || "";
    expect(header).toContain("Pooled n=5 · avg R gross=— · gross PnL=+$5.00");
  });

  it("B9 calendar runs show the bars evaluated from the row, not the 20,000 API cap", () => {
    setJob(
      job([row([trade(1, 20)], { bars_loaded: 2260, bars_evaluated: 2160, warmup_bars: 100 })], {
        limit: 20000,
        start_date: "2025-01-01",
        end_date: "2025-03-31",
      }),
    );
    const { container } = renderPanel();
    const text = container.textContent || "";
    expect(text).toContain("LONG · 2,160 bars evaluated (plus 100 warm-up, 1 Jan 2025 → 31 Mar 2025)");
    expect(text).not.toMatch(/20,000 bars/);
  });

  it("B9 a DB-tail run still compares evaluated bars with the request", () => {
    setJob(job([row([trade(1, 20)], { bars_loaded: 1100, bars_evaluated: 1000, warmup_bars: 100 })], { limit: 1200 }));
    const { container } = renderPanel();
    expect(container.textContent).toContain("1,200 bars (1,000 evaluated, plus 100 warm-up, ");
  });

  it("B10 the trade span names both years when the trades cross a year end", () => {
    const t1 = trade(1, 20, { signal_time: "2024-12-28T00:00:00+00:00", exit_time: "2024-12-29T00:00:00+00:00" });
    const t2 = trade(2, -10, { signal_time: "2025-01-02T00:00:00+00:00", exit_time: "2025-01-03T00:00:00+00:00" });
    setJob(job([row([t1, t2])]));
    const first = renderPanel();
    expect(first.container.textContent).toContain("The 2 trades run from 28 Dec 2024 through 3 Jan 2025");
    first.unmount();

    // Same year: the start keeps the short form.
    setJob(job([row([trade(1, 20), trade(2, 5)])]));
    const second = renderPanel();
    expect(second.container.textContent).toContain("The 2 trades run from 1 Jan through 2 Jan 2025");
  });

  it("B11 the data map names the job's strategy and coin count, not the form's", async () => {
    const user = userEvent.setup();
    setJob(job([row([trade(1, 20), trade(2, 5)])]));
    const { container } = renderPanel();
    const root = within(container);
    await user.click(root.getByRole("tab", { name: "Long trades" }));
    await user.selectOptions(root.getByRole("combobox", { name: /^Strategy/i }), "CANDLE12_V2");
    const map = root.getByLabelText("Backtest data map").textContent || "";
    expect(map).toContain("COMBO_01 · LONG");
    expect(map).not.toContain("CANDLE12");
    // The job ran 1 coin; the form holds 3.
    expect(map).toContain("1 symbol ·");
  });

  it("equity column and max DD $ follow exit order when trades overlap", () => {
    // A opens first and closes last; B opens and closes inside A.
    const a = trade(1, -30, { signal_time: "2025-01-01T00:00:00+00:00", exit_time: "2025-01-05T00:00:00+00:00" });
    const b = trade(2, 20, { signal_time: "2025-01-02T00:00:00+00:00", exit_time: "2025-01-03T00:00:00+00:00" });
    setJob(job([row([a, b])]));
    const { container } = renderPanel();
    const rows = blotterRows(container).map(cells);
    // Rows stay in list order; equity is realized at each exit.
    expect(rows.map((r) => r[COL.no])).toEqual(["1", "2"]);
    expect(Object.fromEntries(rows.map((r) => [r[COL.no], r[COL.equity]]))).toEqual({
      "2": "$1020.00",
      "1": "$990.00",
    });
    expect(container.textContent).toContain("Principal $1000.00 → total $990.00");
  });

  it("max DD $ accumulates in exit order", () => {
    // List order A, B, C; exits B (Jan 3), C (Jan 4), A (Jan 5).
    const a = trade(1, -10, { signal_time: "2025-01-01T00:00:00+00:00", exit_time: "2025-01-05T00:00:00+00:00" });
    const b = trade(2, -10, { signal_time: "2025-01-02T00:00:00+00:00", exit_time: "2025-01-03T00:00:00+00:00" });
    const c = trade(3, 30, { signal_time: "2025-01-02T06:00:00+00:00", exit_time: "2025-01-04T00:00:00+00:00" });
    setJob(job([row([a, b, c])]));
    const { container } = renderPanel();
    // Exit order -10, +30, -10: worst drop $10. List order -10, -10, +30 would say $20.
    expect(container.textContent).toMatch(/max DD \$10\.00 \//);
    const rows = blotterRows(container).map(cells);
    expect(rows.map((r) => r[COL.equity])).toEqual(["$1010.00", "$990.00", "$1020.00"]);
  });

  it("equity ties on exit time keep list order", () => {
    const a = trade(1, 10, { exit_time: "2025-01-05T00:00:00+00:00" });
    const b = trade(2, -4, { exit_time: "2025-01-05T00:00:00+00:00" });
    setJob(job([row([a, b])]));
    const { container } = renderPanel();
    const rows = blotterRows(container).map(cells);
    expect(rows.map((r) => r[COL.equity])).toEqual(["$1010.00", "$1006.00"]);
  });
});
