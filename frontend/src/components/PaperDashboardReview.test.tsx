/** @vitest-environment jsdom */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DashboardReview, PaperLedgerBook } from "../api/client";
import { BookConfigHeader, BookRollupPanel, RunnerHealthPanel } from "./PaperDashboardReview";

// Owner D200 (DECISIONS D233): the paper / forward-test cards carry the research verdicts.
const REVIEW = {
  read_only: true,
  as_of: "2026-10-22T18:00:00Z",
  armed_books: ["V1", "PASSED", "CANDLE12_V2", "FWD-1"],
  watchers: [
    { id: "V1", can_open: true, coins: [{ symbol: "BTCUSDT", timeframe: "1h", risk_percent: 0.02 }], risk: "2% of equity on each 1h book" },
    { id: "PASSED", can_open: true, coins: [], risk: "2% large-cap list" },
    { id: "CANDLE12_V2", can_open: true, coins: ["BTCUSDT"], risk: "$20 on a $1,000 account" },
    { id: "LEGACY", can_open: false, coins: [], risk: "15m setup stream" },
    { id: "FWD-1", can_open: true, coins: ["BTCUSDT", "ETHUSDT"], risk: "0.5% of $10,000 per coin" },
  ],
  telegram_text: "",
  forward_tests: [],
  reset_blocked: true,
  runners: [
    { runner: "passed book", last_heartbeat: null, age_seconds: null, status: "FRESH", detail: "" },
    { runner: "COMBO_02 v1", last_heartbeat: null, age_seconds: null, status: "FRESH", detail: "" },
    { runner: "CANDLE12_V2", last_heartbeat: null, age_seconds: null, status: "FRESH", detail: "" },
    { runner: "FWD-1 book 16", last_heartbeat: null, age_seconds: null, status: "FRESH", detail: "" },
  ],
} as unknown as DashboardReview;

const V1_TEXT = "Research: KILLED (OOS_FAIL archived; COMBO_02 LONG killed in DISCOVERY)";
const C12_TEXT = "Research: KILLED (Gate 2: below random p90)";
const PASSED_TEXT = "Research: KILLED strategies (research) · parity test";
const FWD1_TEXT = "Research: INCONCLUSIVE";

function book(id: PaperLedgerBook["id"], label: string): PaperLedgerBook {
  return { id, label, forward_start: null, forward_start_cite: "", equity: 1000, trades: 0, pnl_usd: 0, r_actual: 0, r_nominal: 0, r_ledger: 0, capped: 0, nominal_fallback: 0 };
}

afterEach(cleanup);

describe("dashboard verdict badges", () => {
  it("shows the verdict on each watcher line", () => {
    render(<BookConfigHeader review={REVIEW} />);
    const config = screen.getByTestId("paper-book-config");
    expect(within(config).getByTestId("paper-verdict-V1").textContent).toBe(V1_TEXT);
    expect(within(config).getByTestId("paper-verdict-CANDLE12_V2").textContent).toBe(C12_TEXT);
    expect(within(config).getByTestId("paper-verdict-PASSED").textContent).toBe(PASSED_TEXT);
    expect(within(config).getByTestId("paper-verdict-FWD-1").textContent).toBe(FWD1_TEXT);
    expect(within(config).queryByTestId("paper-verdict-LEGACY")).toBeNull();
  });

  it("shows the verdict on the per-book cards and the CANDLE12_V2 card", () => {
    render(
      <BookRollupPanel
        open={[]}
        ledgerBooks={[book("PASSED", "PARITY TEST"), book("V1", "PAPER"), book("LEGACY", "LEGACY (disabled)")]}
        startingEquity={1000}
        combined={{ equity: null, realized: null, openRisk: null, trades: null }}
        candle12={null}
      />
    );
    expect(within(screen.getByTestId("paper-book-V1")).getByTestId("paper-verdict-V1").textContent).toBe(V1_TEXT);
    expect(within(screen.getByTestId("paper-book-PASSED")).getByTestId("paper-verdict-PASSED").textContent).toBe(PASSED_TEXT);
    expect(within(screen.getByTestId("paper-book-LEGACY")).queryByTestId(/^paper-verdict-/)).toBeNull();
    expect(
      within(screen.getByTestId("paper-candle12-rollup")).getByTestId("paper-verdict-CANDLE12_V2").textContent
    ).toBe(C12_TEXT);
  });

  it("shows the verdict on the forward-test runner rows", () => {
    render(<RunnerHealthPanel review={REVIEW} />);
    const panel = screen.getByTestId("paper-runner-health");
    expect(within(panel).getByTestId("paper-verdict-COMBO_02 v1").textContent).toBe(V1_TEXT);
    expect(within(panel).getByTestId("paper-verdict-passed book").textContent).toBe(PASSED_TEXT);
    expect(within(panel).getByTestId("paper-verdict-CANDLE12_V2").textContent).toBe(C12_TEXT);
    expect(within(panel).getByTestId("paper-verdict-FWD-1 book 16").textContent).toBe(FWD1_TEXT);
  });
});
