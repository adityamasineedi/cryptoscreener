/** @vitest-environment jsdom */
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchPassedRegime, type PassedRegimeResponse } from "../api/client";
import { formatIst, formatUtc } from "../paper/zoneTime";
import { PassedRegimePanel } from "./PassedRegimePanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, fetchPassedRegime: vi.fn() };
});

const LONG_ROWS = [
  { strategy_id: "COMBO_03_TRANSITION_B", timeframe: "1h", direction: "LONG" },
  { strategy_id: "COMBO_LONG_ONLY", timeframe: "4h", direction: "LONG" },
];
const SHORT_ROWS = [
  { strategy_id: "COMBO_03_TRANSITION_B", timeframe: "1h", direction: "SHORT" },
  { strategy_id: "COMBO_SHORT_ONLY", timeframe: "4h", direction: "SHORT" },
];

function response(overrides: Partial<PassedRegimeResponse> = {}): PassedRegimeResponse {
  return {
    enabled: true,
    trend: "BULLISH",
    daily_read_ok: true,
    daily_read_error: "",
    regime_bar: "2026-10-14T08:00:00+00:00",
    regime_read_at: "2026-10-14T09:00:02+00:00",
    as_of_daily_bar: "2026-10-13T00:00:00+00:00",
    daily_bars: 120,
    min_daily_bars: 40,
    short_history: false,
    allowed: LONG_ROWS,
    skipped: SHORT_ROWS,
    allowed_strategy_ids: LONG_ROWS.map((row) => row.strategy_id),
    skipped_strategy_ids: SHORT_ROWS.map((row) => row.strategy_id),
    latest_signals: [],
    last_evaluated_bar: null,
    last_skip: null,
    opened_count: 0,
    watcher_loop_enabled: true,
    timestamp: "2026-10-14T09:00:05+00:00",
    ...overrides,
  };
}

async function renderWith(payload: PassedRegimeResponse) {
  vi.mocked(fetchPassedRegime).mockResolvedValue(payload);
  render(<PassedRegimePanel />);
  await act(async () => Promise.resolve());
}

function rowTexts(testId: string): string[] {
  return within(screen.getByTestId(testId))
    .getAllByRole("row")
    .map((row) => row.textContent ?? "");
}

describe("PassedRegimePanel", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("shows a loading state before the first answer", () => {
    vi.mocked(fetchPassedRegime).mockReturnValue(new Promise(() => {}));
    render(<PassedRegimePanel />);
    expect(screen.getByTestId("passed-regime-loading")).toBeTruthy();
  });

  it("renders a BULLISH book exactly as the endpoint returned it", async () => {
    await renderWith(response());

    expect(screen.getByTestId("passed-regime-trend").textContent).toBe("BULLISH");
    expect(screen.getByTestId("passed-regime-as-of").textContent).toBe("2026-10-13T00:00:00+00:00");
    expect(screen.getByTestId("passed-regime-daily-bars").textContent).toBe("120");
    expect(rowTexts("passed-regime-allowed")).toEqual([
      "COMBO_03_TRANSITION_B1hLONG",
      "COMBO_LONG_ONLY4hLONG",
    ]);
    expect(rowTexts("passed-regime-skipped")).toEqual([
      "COMBO_03_TRANSITION_B1hSHORT",
      "COMBO_SHORT_ONLY4hSHORT",
    ]);
    expect(screen.queryByTestId("passed-regime-neutral-note")).toBeNull();
    expect(screen.queryByTestId("passed-regime-short-history")).toBeNull();
  });

  it("renders a BEARISH book", async () => {
    await renderWith(response({ trend: "BEARISH", allowed: SHORT_ROWS, skipped: LONG_ROWS }));

    expect(screen.getByTestId("passed-regime-trend").textContent).toBe("BEARISH");
    expect(rowTexts("passed-regime-allowed")).toEqual([
      "COMBO_03_TRANSITION_B1hSHORT",
      "COMBO_SHORT_ONLY4hSHORT",
    ]);
    expect(rowTexts("passed-regime-skipped")).toEqual([
      "COMBO_03_TRANSITION_B1hLONG",
      "COMBO_LONG_ONLY4hLONG",
    ]);
  });

  it("does not filter the book itself: rows the trend would not pick still render", async () => {
    await renderWith(response({ trend: "BULLISH", allowed: SHORT_ROWS, skipped: [] }));
    expect(rowTexts("passed-regime-allowed")).toEqual([
      "COMBO_03_TRANSITION_B1hSHORT",
      "COMBO_SHORT_ONLY4hSHORT",
    ]);
    expect(screen.getByTestId("passed-regime-skipped").textContent).toContain("No sides are skipped.");
  });

  it("says plainly that NEUTRAL opens nothing", async () => {
    await renderWith(
      response({ trend: "NEUTRAL", allowed: [], skipped: [...LONG_ROWS, ...SHORT_ROWS] }),
    );

    expect(screen.getByTestId("passed-regime-trend").textContent).toBe("NEUTRAL");
    expect(screen.getByTestId("passed-regime-neutral-note").textContent).toBe(
      "No new signals. Open positions keep their own stops and targets.",
    );
    expect(screen.getByTestId("passed-regime-allowed").textContent).toContain(
      "No sides may open new trades.",
    );
    expect(rowTexts("passed-regime-skipped")).toHaveLength(4);
    expect(screen.queryByTestId("passed-regime-short-history")).toBeNull();
  });

  it("explains a neutral trend caused by short daily history, with the backend threshold", async () => {
    await renderWith(
      response({
        trend: "NEUTRAL",
        daily_bars: 12,
        min_daily_bars: 35,
        short_history: true,
        allowed: [],
        skipped: [],
      }),
    );
    expect(screen.getByTestId("passed-regime-short-history").textContent).toContain(
      "The trend is neutral because BTC daily history is short: 12 closed bars, 35 needed.",
    );
    expect(screen.queryByTestId("passed-regime-read-failed")).toBeNull();
  });

  it("shows a failed daily read as a failure, not as a trend", async () => {
    await renderWith(
      response({
        trend: "NEUTRAL",
        daily_read_ok: false,
        daily_read_error: "daily_read_failed",
        as_of_daily_bar: "",
        daily_bars: 0,
        allowed: [],
        skipped: [...LONG_ROWS, ...SHORT_ROWS],
      }),
    );
    expect(screen.getByTestId("passed-regime-trend").textContent).toBe("DAILY READ FAILED");
    expect(screen.getByTestId("passed-regime-read-failed").textContent).toContain(
      "The BTC daily read failed (daily_read_failed). This is not a trend.",
    );
    expect(screen.queryByTestId("passed-regime-neutral-note")).toBeNull();
    expect(screen.queryByTestId("passed-regime-short-history")).toBeNull();
    expect(screen.getByTestId("passed-regime-bar").textContent).toBe("2026-10-14T08:00:00+00:00");
  });

  it("lists the latest signals with outcome and reason", async () => {
    await renderWith(
      response({
        opened_count: 1,
        last_skip: "ETHUSDT:symbol_has_open_position",
        last_evaluated_bar: {
          symbol: "ETHUSDT",
          bar: "2026-10-14T08:00:00+00:00",
          trend: "BULLISH",
          as_of_daily_bar: "2026-10-13T00:00:00+00:00",
          sides_evaluated: 2,
        },
        latest_signals: [
          {
            strategy_id: "COMBO_03_TRANSITION_B",
            direction: "LONG",
            timeframe: "1h",
            symbol: "ETHUSDT",
            setup_bar_time: "2026-10-14T08:00:00+00:00",
            trend: "BULLISH",
            as_of_daily_bar: "2026-10-13T00:00:00+00:00",
            outcome: "not_opened",
            reason: "symbol_has_open_position",
            recorded_at: "2026-10-14T09:00:04+00:00",
          },
          {
            strategy_id: "COMBO_LONG_ONLY",
            direction: "LONG",
            timeframe: "4h",
            symbol: "BTCUSDT",
            setup_bar_time: "2026-10-14T04:00:00+00:00",
            trend: "BULLISH",
            as_of_daily_bar: "2026-10-13T00:00:00+00:00",
            outcome: "opened",
            reason: "",
            position_id: "p-1",
            recorded_at: "2026-10-14T05:00:04+00:00",
          },
        ],
      }),
    );

    const rows = within(screen.getByTestId("passed-regime-signals")).getAllByRole("row");
    expect(rows).toHaveLength(2);
    const first = rows[0].textContent ?? "";
    expect(first).toContain("ETHUSDT");
    expect(first).toContain("COMBO_03_TRANSITION_B");
    expect(first).toContain("LONG");
    expect(first).toContain("BULLISH");
    expect(first).toContain("not_opened");
    expect(first).toContain("symbol_has_open_position");
    expect(first).toContain(formatIst("2026-10-14T08:00:00+00:00"));
    expect(rows[0].querySelector("td")?.getAttribute("title")).toBe(formatUtc("2026-10-14T08:00:00+00:00"));
    const second = rows[1].textContent ?? "";
    expect(second).toContain("BTCUSDT");
    expect(second).toContain("opened");
    expect(second.endsWith("—")).toBe(true);
    expect(screen.queryByText("No signals yet")).toBeNull();

    expect(screen.getByTestId("passed-regime-last-bar").textContent).toBe(
      "ETHUSDT 2026-10-14T08:00:00+00:00 · BULLISH · 2 sides",
    );
    expect(screen.getByTestId("passed-regime-last-skip").textContent).toBe(
      "ETHUSDT:symbol_has_open_position",
    );
  });

  it("shows a closed book with its trend, allowed ids and recorded signals", async () => {
    await renderWith(
      response({
        trend: "BEARISH",
        allowed: SHORT_ROWS,
        skipped: LONG_ROWS,
        allowed_strategy_ids: SHORT_ROWS.map((row) => row.strategy_id),
        book_open: false,
        book_note: "New passed-regime entries are off because no side passed its regime window.",
        last_skip: "BTCUSDT:regime_book_closed",
        latest_signals: [
          {
            strategy_id: "COMBO_SHORT_ONLY",
            direction: "SHORT",
            timeframe: "1h",
            symbol: "BTCUSDT",
            setup_bar_time: "2026-10-14T08:00:00+00:00",
            trend: "BEARISH",
            as_of_daily_bar: "2026-10-13T00:00:00+00:00",
            outcome: "not_opened",
            reason: "regime_book_closed",
            recorded_at: "2026-10-14T09:00:04+00:00",
          },
        ],
      }),
    );

    expect(screen.getByTestId("passed-regime-book-closed").textContent).toBe(
      "Book closed. New passed-regime entries are off because no side passed its regime window. " +
        "Signals below are recorded, not opened.",
    );
    expect(screen.getByTestId("passed-regime-trend").textContent).toBe("BEARISH");
    expect(screen.getByText("Allowed book (closed) · 2")).toBeTruthy();
    expect(rowTexts("passed-regime-allowed")).toEqual([
      "COMBO_03_TRANSITION_B1hSHORT",
      "COMBO_SHORT_ONLY4hSHORT",
    ]);
    const rows = within(screen.getByTestId("passed-regime-signals")).getAllByRole("row");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("not_opened");
    expect(rows[0].textContent).toContain("regime_book_closed");
  });

  it("shows no closed-book line while the book is open", async () => {
    await renderWith(response({ book_open: true, book_note: "" }));
    expect(screen.queryByTestId("passed-regime-book-closed")).toBeNull();
    expect(screen.getByText("Allowed book · 2")).toBeTruthy();
  });

  it("shows the empty state and the watcher flags", async () => {
    await renderWith(response({ enabled: false, watcher_loop_enabled: false }));

    expect(within(screen.getByTestId("passed-regime-signals")).getByText("No signals yet")).toBeTruthy();
    expect(screen.getByTestId("passed-regime-enabled").textContent).toBe("disabled");
    expect(screen.getByTestId("passed-regime-loop").textContent).toBe("off");
    expect(screen.getByTestId("passed-regime-last-bar").textContent).toBe("none");
    expect(screen.getByTestId("passed-regime-last-skip").textContent).toBe("none");
  });

  it("shows the error when the first read fails", async () => {
    vi.mocked(fetchPassedRegime).mockRejectedValue(new Error("HTTP 503"));
    render(<PassedRegimePanel />);
    await act(async () => Promise.resolve());

    expect(screen.getByTestId("passed-regime-error").textContent).toBe("HTTP 503");
    expect(screen.queryByTestId("passed-regime-loading")).toBeNull();
    expect(screen.queryByTestId("passed-regime-trend")).toBeNull();
  });

  it("keeps the last snapshot when a later poll fails, then recovers", async () => {
    vi.mocked(fetchPassedRegime)
      .mockResolvedValueOnce(response())
      .mockRejectedValueOnce(new Error("HTTP 500"))
      .mockResolvedValueOnce(response({ trend: "BEARISH", allowed: SHORT_ROWS, skipped: LONG_ROWS }));
    render(<PassedRegimePanel />);
    await act(async () => Promise.resolve());
    expect(screen.getByTestId("passed-regime-trend").textContent).toBe("BULLISH");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchPassedRegime).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("passed-regime-error").textContent).toBe(
      "HTTP 500 · showing last snapshot",
    );
    expect(screen.getByTestId("passed-regime-trend").textContent).toBe("BULLISH");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchPassedRegime).toHaveBeenCalledTimes(3);
    expect(screen.queryByTestId("passed-regime-error")).toBeNull();
    expect(screen.getByTestId("passed-regime-trend").textContent).toBe("BEARISH");
  });

  it("renders no buttons, inputs, links or forms in any state", async () => {
    const controls = () =>
      screen
        .getByTestId("passed-regime")
        .querySelectorAll("button, input, select, textarea, a, form, [role='button'], [onclick]");

    vi.mocked(fetchPassedRegime).mockRejectedValue(new Error("HTTP 503"));
    render(<PassedRegimePanel />);
    await act(async () => Promise.resolve());
    expect(controls()).toHaveLength(0);
    cleanup();

    for (const payload of [
      response(),
      response({ trend: "BEARISH", allowed: SHORT_ROWS, skipped: LONG_ROWS }),
      response({ trend: "NEUTRAL", allowed: [], skipped: [], enabled: false }),
    ]) {
      await renderWith(payload);
      expect(controls()).toHaveLength(0);
      expect(screen.queryAllByRole("button")).toHaveLength(0);
      cleanup();
    }
  });
  it("lists the per-coin allowlist: allowed and blocked sides for each coin with an entry", async () => {
    await renderWith(
      response({
        symbol_allowlist: {
          version: 1,
          source: "passed_symbol_allowlist.json",
          symbols: {
            BTCUSDT: {
              allowed_sides: [{ strategy_id: "COMBO_01", timeframe: "1h", direction: "SHORT" }],
              blocked_sides: [
                { strategy_id: "COMBO_02", timeframe: "1h", direction: "SHORT" },
                { strategy_id: "COMBO_03_TRANSITION_B", timeframe: "1h", direction: "LONG" },
              ],
              tested_window: { start: "2025-01-01", end: "2025-12-31" },
              decided_at: "2026-10-17T00:00:00+00:00",
            },
          },
        },
      }),
    );
    const block = screen.getByTestId("passed-regime-allowlist-BTCUSDT");
    expect(block.textContent).toContain("Allowed sides on BTC: COMBO_01 SHORT");
    expect(block.textContent).toContain(
      "Blocked on BTC (failed BTC test): COMBO_02 SHORT, COMBO_03_TRANSITION_B LONG",
    );
    expect(block.textContent).toContain("tested 2025-01-01 → 2025-12-31, decided 2026-10-17");
  });

  it("shows nothing for the allowlist when no coin has an entry", async () => {
    await renderWith(response({ symbol_allowlist: { version: 1, source: "x", symbols: {} } }));
    expect(screen.queryByTestId("passed-regime-allowlist")).toBeNull();
  });
});

const HEARTBEAT = {
  last_eval_at: "2026-10-17T12:05:00+00:00",
  last_eval_symbol: "ETHUSDT",
  last_eval_bar: "2026-10-17T11:00:00+00:00",
  last_eval_lag_seconds: 300,
  seconds_since_last_eval: 120,
  stale: false,
  last_catchup_at: "2026-10-17T12:06:00+00:00",
  pending_bars: 0,
  pending_by_symbol: {},
  watermarks: { ETHUSDT: "2026-10-17T11:00:00+00:00" },
  state_file: "data/paper/passed_paper_watermark.json",
  max_catchup_bars: 24,
  max_open_lag_seconds: 900,
};

const MISSED_LATE = {
  strategy_id: "COMBO_02_V2",
  direction: "SHORT",
  timeframe: "1h",
  symbol: "ARBUSDT",
  setup_bar_time: "2026-10-17T09:00:00+00:00",
  trend: "BEARISH",
  as_of_daily_bar: "2026-10-16T00:00:00+00:00",
  outcome: "missed",
  reason: "late_tip",
  note: "missed: evaluated late (56m)",
  lag_seconds: 3360,
  recorded_at: "2026-10-17T10:56:01+00:00",
};

const NOT_EVALUATED = {
  symbol: "SOLUSDT",
  timeframe: "1h",
  setup_bar_time: "2026-10-16T06:00:00+00:00",
  last_bar_time: "2026-10-16T11:00:00+00:00",
  bars: 6,
  outcome: "not_evaluated",
  reason: "beyond_catchup_window",
  note: "missed: 6 bars not evaluated (beyond_catchup_window, 1745m late)",
  recorded_at: "2026-10-17T12:06:00+00:00",
};

function health(testId: string): string {
  return screen.getByTestId(`passed-health-${testId}`).textContent ?? "";
}

describe("PassedRegimePanel · passed book health", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("shows the last evaluated bar, its lag and zero counts when healthy", async () => {
    await renderWith(
      response({ heartbeat: HEARTBEAT, missed_signals: 0, unevaluated_bars: 0, recent_missed: [] }),
    );

    expect(health("last-bar")).toBe(`ETH ${formatIst("2026-10-17T11:00:00+00:00")}`);
    expect(screen.getByTestId("passed-health-last-bar").getAttribute("title")).toBe(
      formatUtc("2026-10-17T11:00:00+00:00"),
    );
    expect(health("lag")).toBe("5m");
    expect(health("since")).toBe("2m ago");
    expect(health("missed")).toBe("0");
    expect(health("unevaluated")).toBe("0");
    expect(health("owed")).toBe("0");
    expect(screen.queryByTestId("passed-health-stale")).toBeNull();
    expect(screen.queryByTestId("passed-health-missed-list")).toBeNull();
  });

  it("shows a red STALE badge when the heartbeat is stale, and picks it up on the next poll", async () => {
    vi.mocked(fetchPassedRegime)
      .mockResolvedValueOnce(response({ heartbeat: HEARTBEAT }))
      .mockResolvedValueOnce(
        response({ heartbeat: { ...HEARTBEAT, stale: true, seconds_since_last_eval: 4860 } }),
      );
    render(<PassedRegimePanel />);
    await act(async () => Promise.resolve());
    expect(screen.queryByTestId("passed-health-stale")).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(fetchPassedRegime).toHaveBeenCalledTimes(2);
    const badge = screen.getByTestId("passed-health-stale");
    expect(badge.textContent).toBe("STALE");
    expect(badge.className).toContain("text-red-300");
    expect(screen.getByTestId("passed-health").className).toContain("border-red-500/50");
    expect(health("since")).toBe("1h 21m ago");
  });

  it("counts missed signals and lists recent missed rows with note and lag, collapsed", async () => {
    await renderWith(
      response({
        heartbeat: { ...HEARTBEAT, pending_bars: 1, pending_by_symbol: { ARBUSDT: 1, BTCUSDT: 0 } },
        missed_signals: 2,
        unevaluated_bars: 6,
        recent_missed: [MISSED_LATE, NOT_EVALUATED],
      }),
    );

    expect(health("missed")).toBe("2");
    expect(health("unevaluated")).toBe("6");
    expect(health("owed")).toBe("1 (ARB 1)");
    const list = screen.getByTestId("passed-health-missed-list") as HTMLDetailsElement;
    expect(list.tagName).toBe("DETAILS");
    expect(list.open).toBe(false);
    expect(list.querySelector("summary")?.textContent).toBe("Recent missed · 2");

    const rows = screen.getAllByTestId("passed-health-missed-row").map((row) => row.textContent ?? "");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toContain(formatIst("2026-10-17T09:00:00+00:00"));
    expect(screen.getAllByTestId("passed-health-missed-row")[0].querySelector("span")?.getAttribute("title")).toBe(
      formatUtc("2026-10-17T09:00:00+00:00"),
    );
    expect(rows[0]).toContain("ARB · COMBO_02_V2 · SHORT");
    expect(rows[0]).toContain("missed: evaluated late (56m)");
    expect(rows[0]).toContain("lag 56m");
    expect(rows[1]).toContain("SOL · 6 bars");
    expect(rows[1]).toContain("missed: 6 bars not evaluated (beyond_catchup_window, 1745m late)");
    expect(rows[1]).toContain("lag —");

    // The expander is a <details>, not a control.
    expect(
      screen
        .getByTestId("passed-regime")
        .querySelectorAll("button, input, select, textarea, a, form, [role='button'], [onclick]"),
    ).toHaveLength(0);
  });

  it("shows — for every field an older backend does not send", async () => {
    await renderWith(response());

    expect(health("last-bar")).toBe("—");
    expect(health("lag")).toBe("—");
    expect(health("since")).toBe("—");
    expect(health("missed")).toBe("—");
    expect(health("unevaluated")).toBe("—");
    expect(health("owed")).toBe("—");
    expect(screen.queryByTestId("passed-health-stale")).toBeNull();
    expect(screen.queryByTestId("passed-health-missed-list")).toBeNull();
  });

  it("shows — for a heartbeat that has not evaluated anything yet, and for rows missing fields", async () => {
    await renderWith(
      response({
        heartbeat: {
          last_eval_at: "",
          last_eval_symbol: "",
          last_eval_bar: "",
          last_eval_lag_seconds: null,
          seconds_since_last_eval: null,
          stale: false,
        },
        missed_signals: 1,
        recent_missed: [{ outcome: "missed" }],
      }),
    );

    expect(health("last-bar")).toBe("—");
    expect(health("lag")).toBe("—");
    expect(health("since")).toBe("—");
    expect(health("owed")).toBe("—");
    expect(health("unevaluated")).toBe("—");
    expect(health("missed")).toBe("1");
    const spans = screen
      .getAllByTestId("passed-health-missed-row")[0]
      .querySelectorAll("span");
    // Bar time, coin/strategy, note, lag.
    expect(Array.from(spans).map((span) => span.textContent)).toEqual(["—", "—", "—", "lag —"]);
  });

  it("keeps the allowlist lines next to the health strip", async () => {
    await renderWith(
      response({
        heartbeat: HEARTBEAT,
        symbol_allowlist: {
          version: 1,
          source: "passed_symbol_allowlist.json",
          symbols: {
            BTCUSDT: {
              allowed_sides: [{ strategy_id: "COMBO_01", timeframe: "1h", direction: "SHORT" }],
              blocked_sides: [],
            },
          },
        },
      }),
    );
    expect(screen.getByTestId("passed-health")).toBeTruthy();
    expect(screen.getByTestId("passed-regime-allowlist-BTCUSDT").textContent).toContain(
      "Allowed sides on BTC: COMBO_01 SHORT",
    );
  });
});
