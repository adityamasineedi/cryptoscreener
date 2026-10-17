/**
 * @vitest-environment jsdom
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LossAnalysisResponse, LossAnalysisTrade } from "../api/client";
import { BacktestLossAnalysis } from "./BacktestLossAnalysis";

function lossTrade(extra: Partial<LossAnalysisTrade> = {}): LossAnalysisTrade {
  return {
    trade_no: 7,
    trade_id: "t-7",
    entry_time: "2026-03-01T04:00:00+00:00",
    exit_time: "2026-03-01T09:00:00+00:00",
    direction: "LONG",
    outcome: "SL",
    net_usd: -21.5,
    gross_usd: -20,
    fees_usd: 1.5,
    r_net: -1.07,
    primary_reason: "GAVE_BACK",
    flags: ["TIGHT_STOP", "CHASED_ENTRY"],
    metrics: {
      stop_distance_pct: 0.8,
      stop_distance_atr: 0.74,
      tp1_distance_r: 2,
      mfe_r_before_exit: 1.2,
      mfe_pct_to_tp1: 60,
      bars_to_mfe: 3,
      hold_bars: 5,
      mae_r: -1.07,
      fees_r: 0.07,
    },
    hindsight: {
      reached_tp1_after_exit: true,
      bars_after_exit: 6,
      stop_needed_r: 1.4,
      max_favourable_after_exit_r: 2.3,
    },
    explanation: "Reached +1.2R (60% of the way to TP1) and then reversed to the stop.",
    ...extra,
  };
}

function analysis(extra: Partial<LossAnalysisResponse> = {}): LossAnalysisResponse {
  return {
    symbol: "BTCUSDT",
    timeframe: "1h",
    strategy_id: "COMBO_02_V1",
    direction: "LONG",
    frozen_book: false,
    suggestions_note: null,
    period: { start: "2026-01-01T00:00:00+00:00", end: "2026-06-30T23:00:00+00:00" },
    hindsight_bars: 48,
    summary: {
      trades: 20,
      winners: 8,
      losers: 11,
      scratches: 1,
      net_usd: -42.25,
      gross_usd: -10,
      fees_usd: 32.25,
      by_reason: [
        { reason: "GAVE_BACK", count: 6, net_usd: -120, share_of_loss: 0.6 },
        { reason: "STOP_BEFORE_PROGRESS", count: 5, net_usd: -80, share_of_loss: 0.4 },
      ],
      by_context: [{ structure: "WRONG", count: 7, net_usd: -140 }],
      largest_losses: [{ trade_no: 7, entry_time: "2026-03-01T04:00:00+00:00", net_usd: -21.5, reason: "GAVE_BACK" }],
      loss_weeks: [{ week: "2026-W09", count: 3, net_usd: -55 }],
      held_would_have_won: { count: 4, net_usd: 61.5 },
    },
    losses: [lossTrade()],
    fee_flipped: [
      lossTrade({
        trade_no: 9,
        trade_id: "t-9",
        primary_reason: "FEES_FLIPPED",
        flags: [],
        net_usd: -0.4,
        gross_usd: 1.1,
        r_net: -0.02,
        hindsight: null,
        explanation: "Gross +$1.10 but fees of $1.50 made it a loss.",
      }),
    ],
    what_ifs: [
      {
        id: "skip_wrong_structure",
        label: "Skip entries against structure",
        computable: true,
        trades: 13,
        net_usd: 97.75,
        delta_usd: 140,
        winners_lost: 1,
        caveat: "In-sample; the filter was chosen after seeing these losses.",
      },
      {
        id: "wider_stop",
        label: "Stop at 1.5×ATR",
        computable: false,
        trades: null,
        net_usd: null,
        delta_usd: null,
        winners_lost: null,
        caveat: "needs a full re-simulation of fills",
      },
    ],
    suggestions: [
      {
        rule: "skip_structure_wrong",
        reason_bucket: "AGAINST_CONTEXT",
        trades_affected: 7,
        net_change_usd: 140,
        winners_lost: 1,
        label: "Test skipping longs when structure is WRONG",
      },
    ],
    disclaimer: "Research only. In-sample diagnostics; nothing here changes the strategy.",
    ...extra,
  };
}

function okResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

const fetchMock = vi.fn();

function Harness({
  jobId = "job-1",
  symbol = "BTCUSDT",
  timeframe = "1h",
  onSelectTrade = () => undefined,
}: {
  jobId?: string;
  symbol?: string;
  timeframe?: string;
  onSelectTrade?: (t: LossAnalysisTrade) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <BacktestLossAnalysis
      jobId={jobId}
      symbol={symbol}
      timeframe={timeframe}
      open={open}
      onOpenChange={setOpen}
      onSelectTrade={onSelectTrade}
    />
  );
}

async function openAnalysis() {
  fireEvent.click(screen.getByRole("button", { name: "Analyze losses" }));
}

describe("BacktestLossAnalysis", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("is collapsed until Analyze losses is clicked, then fetches the viewed cell", async () => {
    fetchMock.mockResolvedValue(okResponse(analysis()));
    render(<Harness />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("loss-analysis-summary")).toBeNull();
    await openAnalysis();
    await screen.findByTestId("loss-analysis-summary");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/research/long-strategy/backtest/loss-analysis?symbol=BTCUSDT&timeframe=1h",
    );
  });

  it("renders the summary, reason chips, explanation, hindsight note and disclaimer", async () => {
    fetchMock.mockResolvedValue(okResponse(analysis()));
    render(<Harness />);
    await openAnalysis();
    const summary = await screen.findByTestId("loss-analysis-summary");
    expect(summary.textContent).toContain("Losers11");
    expect(summary.textContent).toContain("Winners8");
    expect(summary.textContent).toContain("$-42.25");
    expect(summary.textContent).toContain("-$32.25");
    expect(summary.textContent).toMatch(/Hindsight4 · \+\$61\.50/);

    const byReason = screen.getByTestId("loss-analysis-by-reason");
    expect(byReason.textContent).toContain("Gave back an open profit");
    expect(byReason.textContent).toContain("Stopped before any real progress");
    expect(byReason.textContent).toContain("60%");
    expect(byReason.textContent).toContain("$-120.00");
    expect(screen.getByTestId("loss-analysis-by-context").textContent).toContain("WRONG");

    const losses = screen.getByTestId("loss-analysis-losses");
    const item = within(losses).getByTestId("loss-item");
    const chip = within(item).getByTestId("loss-reason-chip");
    expect(chip.textContent).toBe("Gave back an open profit");
    expect(chip.getAttribute("title")).toMatch(/^GAVE_BACK: /);
    const flags = within(item).getAllByTestId("loss-flag-chip").map((el) => el.textContent);
    expect(flags).toEqual(["Stop tighter than 1×ATR", "Chased entry"]);
    expect(item.textContent).toContain("#7");
    expect(item.textContent).toContain("2026-03-01 04:00:00 UTC → 2026-03-01 09:00:00 UTC");
    expect(item.textContent).toContain("$-21.50");
    expect(item.textContent).toContain("-1.07 R");
    expect(item.textContent).toContain("Reached +1.2R (60% of the way to TP1) and then reversed to the stop.");
    expect(within(item).getByTestId("loss-metrics").textContent).toBe(
      "MFE to TP1 60% · MFE 1.20 R · stop 0.74×ATR · hold 5 bars · fees 0.07 R",
    );
    expect(within(item).getByTestId("loss-hindsight").textContent).toBe(
      "Hindsight: price reached TP1 6 bars after the stop; a stop of 1.40 R would have held.",
    );

    const feeFlipped = screen.getByTestId("loss-analysis-fee-flipped");
    expect(within(feeFlipped).getByTestId("loss-reason-chip").textContent).toBe("Fees turned a winner into a loss");
    expect(within(feeFlipped).queryByTestId("loss-hindsight")).toBeNull();

    expect(screen.getByTestId("loss-analysis-disclaimer").textContent).toBe(
      "Research only. In-sample diagnostics; nothing here changes the strategy.",
    );
  });

  it("shows what-ifs with change vs actual and the reason a what-if is not computable", async () => {
    fetchMock.mockResolvedValue(okResponse(analysis()));
    render(<Harness />);
    await openAnalysis();
    const table = await screen.findByTestId("loss-analysis-what-ifs");
    expect(screen.getByText("What-ifs — research only, in-sample")).toBeTruthy();
    const rows = within(table).getAllByTestId("what-if-row");
    expect(rows[0].textContent).toContain("Skip entries against structure");
    expect(rows[0].textContent).toContain("+$97.75");
    expect(rows[0].textContent).toContain("+$140.00");
    expect(rows[0].getAttribute("title")).toBe("In-sample; the filter was chosen after seeing these losses.");
    expect(within(rows[0]).getByText("+$140.00").className).toContain("text-emerald-400");
    expect(within(rows[1]).getByTestId("what-if-not-computable").textContent).toBe(
      "Not computable: needs a full re-simulation of fills",
    );
  });

  it("lists suggestions as research only, unless the book is frozen", async () => {
    fetchMock.mockResolvedValue(okResponse(analysis()));
    const { unmount } = render(<Harness />);
    await openAnalysis();
    const sg = await screen.findByTestId("loss-analysis-suggestions");
    expect(sg.textContent).toContain("Test skipping longs when structure is WRONG");
    expect(sg.textContent).toContain("research only");
    expect(within(sg).queryByTestId("loss-analysis-frozen-note")).toBeNull();
    unmount();

    fetchMock.mockResolvedValue(
      okResponse(
        analysis({
          frozen_book: true,
          suggestions_note: "Frozen CANDLE12_V2 BTC 1h LONG book: diagnostics only.",
        }),
      ),
    );
    render(<Harness />);
    await openAnalysis();
    const frozen = await screen.findByTestId("loss-analysis-frozen-note");
    expect(frozen.textContent).toBe("Frozen CANDLE12_V2 BTC 1h LONG book: diagnostics only.");
    expect(screen.getByTestId("loss-analysis-suggestions").textContent).not.toContain(
      "Test skipping longs when structure is WRONG",
    );
    // Diagnostics still render for the frozen book.
    expect(screen.getByTestId("loss-analysis-losses")).toBeTruthy();
    expect(screen.getByTestId("loss-analysis-disclaimer")).toBeTruthy();
  });

  it("shows the server's reason on a 409", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: "The finished job has no trades for BTCUSDT 1h." }),
    } as Response);
    render(<Harness />);
    await openAnalysis();
    const err = await screen.findByTestId("loss-analysis-error");
    expect(err.textContent).toBe("Loss analysis not available: The finished job has no trades for BTCUSDT 1h.");
    expect(screen.queryByTestId("loss-analysis-summary")).toBeNull();
  });

  it("clicking a loss calls the selection callback with that trade", async () => {
    fetchMock.mockResolvedValue(okResponse(analysis()));
    const onSelect = vi.fn();
    render(<Harness onSelectTrade={onSelect} />);
    await openAnalysis();
    const fee = await screen.findByTestId("loss-analysis-fee-flipped");
    fireEvent.click(within(screen.getByTestId("loss-analysis-losses")).getByTestId("loss-item"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toMatchObject({ trade_no: 7, trade_id: "t-7" });
    fireEvent.keyDown(within(fee).getByTestId("loss-item"), { key: "Enter" });
    expect(onSelect.mock.calls[1][0]).toMatchObject({ trade_no: 9 });
  });

  it("never shows the previous cell's analysis after the viewed cell changes", async () => {
    let resolveBtc: (r: Response) => void = () => undefined;
    let resolveEth: (r: Response) => void = () => undefined;
    fetchMock.mockImplementation((url: string) =>
      new Promise<Response>((resolve) => {
        if (url.includes("symbol=BTCUSDT")) resolveBtc = resolve;
        else resolveEth = resolve;
      }),
    );
    const { rerender } = render(<Harness symbol="BTCUSDT" />);
    await openAnalysis();
    expect(screen.getByTestId("loss-analysis-loading").textContent).toContain("BTCUSDT 1h");

    // Switch before BTC answers; the late BTC answer must not appear.
    rerender(<Harness symbol="ETHUSDT" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[1][0])).toContain("symbol=ETHUSDT");
    await act(async () => {
      resolveBtc(okResponse(analysis()));
    });
    expect(screen.queryByTestId("loss-analysis-summary")).toBeNull();
    expect(screen.getByTestId("loss-analysis-loading").textContent).toContain("ETHUSDT 1h");

    await act(async () => {
      resolveEth(
        okResponse(
          analysis({
            symbol: "ETHUSDT",
            losses: [lossTrade({ trade_no: 3, trade_id: "e-3", explanation: "ETH loss explanation." })],
          }),
        ),
      );
    });
    const losses = await screen.findByTestId("loss-analysis-losses");
    expect(losses.textContent).toContain("ETH loss explanation.");
    expect(losses.textContent).not.toContain("Reached +1.2R");

    // A shown analysis is dropped as soon as the job changes, before the refetch answers.
    fetchMock.mockImplementation(() => new Promise<Response>(() => undefined));
    rerender(<Harness symbol="ETHUSDT" jobId="job-2" />);
    expect(screen.queryByTestId("loss-analysis-losses")).toBeNull();
    expect(screen.getByTestId("loss-analysis-loading")).toBeTruthy();
  });

  it("refuses an answer for a different cell than the one viewed", async () => {
    fetchMock.mockResolvedValue(okResponse(analysis({ symbol: "SOLUSDT" })));
    render(<Harness symbol="BTCUSDT" />);
    await openAnalysis();
    const err = await screen.findByTestId("loss-analysis-error");
    expect(err.textContent).toMatch(/answered for SOLUSDT 1h/);
    expect(screen.queryByTestId("loss-analysis-losses")).toBeNull();
  });

  it("refuses an answer the server stamps with another job id", async () => {
    fetchMock.mockResolvedValue(okResponse(analysis({ job_id: "job-other" })));
    render(<Harness jobId="job-1" />);
    await openAnalysis();
    const err = await screen.findByTestId("loss-analysis-error");
    expect(err.textContent).toMatch(/job job-other/);
    expect(screen.queryByTestId("loss-analysis-summary")).toBeNull();
  });

  it("shows the server's note when an unfrozen book has no suggestions", async () => {
    fetchMock.mockResolvedValue(
      okResponse(analysis({ suggestions: [], suggestions_note: "No what-if improved net without losing winners." })),
    );
    render(<Harness />);
    await openAnalysis();
    const sg = await screen.findByTestId("loss-analysis-suggestions");
    expect(sg.textContent).toContain("No what-if improved net without losing winners.");
  });

  it("notes flags that are not used as a reason because winners carry them too", async () => {
    const base = analysis();
    fetchMock.mockResolvedValue(
      okResponse(
        analysis({
          summary: {
            ...base.summary,
            non_discriminating_flags: [
              {
                flag: "WRONG_RANGE_HALF",
                losers_rate: 0.95,
                winners_rate: 1,
                reason: "context read after the fill",
              },
            ],
          },
        }),
      ),
    );
    render(<Harness />);
    await openAnalysis();
    const note = await screen.findByTestId("loss-analysis-non-discriminating");
    expect(note.textContent).toBe(
      "Entered in the wrong half of the range is not used as a reason here: also present on 100% of winners (95% of losers) / context read after the fill.",
    );
  });

  it("has no flag note when the server sends none", async () => {
    fetchMock.mockResolvedValue(okResponse(analysis()));
    render(<Harness />);
    await openAnalysis();
    await screen.findByTestId("loss-analysis-summary");
    expect(screen.queryByTestId("loss-analysis-non-discriminating")).toBeNull();
  });

  it("pages a long loss list 50 at a time", async () => {
    const many = Array.from({ length: 105 }, (_, i) =>
      lossTrade({ trade_no: i + 1, trade_id: `t-${i + 1}` }),
    );
    fetchMock.mockResolvedValue(okResponse(analysis({ losses: many })));
    render(<Harness />);
    await openAnalysis();
    const list = await screen.findByTestId("loss-analysis-losses");
    expect(within(list).getAllByTestId("loss-item")).toHaveLength(50);
    expect(list.textContent).toContain("1/3");
    // The 50 loss items are role="button" too, so a by-role "Next" query
    // computes the accessible name of every item's full text (seconds in
    // jsdom). Match the pager's own <button> by its text instead.
    const next = () => within(list).getByText("Next", { selector: "button" });
    fireEvent.click(next());
    fireEvent.click(next());
    expect(within(list).getAllByTestId("loss-item")).toHaveLength(5);
    expect(within(list).getAllByTestId("loss-item")[0].textContent).toContain("#101");
  });
});
