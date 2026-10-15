/** @vitest-environment jsdom */
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchPassedRegime, type PassedRegimeResponse } from "../api/client";
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
    expect(first).toContain(new Date("2026-10-14T08:00:00+00:00").toLocaleString());
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
});
