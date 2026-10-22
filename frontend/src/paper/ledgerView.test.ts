import { describe, expect, it } from "vitest";
import type { PaperLedgerRow, PaperLedgerView } from "../api/client";
import { hiddenSummary, ledgerTables } from "./ledgerView";

function row(id: string, over: Partial<PaperLedgerRow>): PaperLedgerRow {
  return {
    id,
    symbol: "SUIUSDT",
    side: "SHORT",
    status: "CLOSED",
    entry_price: 1,
    stop_price: 1.02,
    tp1_price: 0.96,
    quantity: 1,
    risk_usd: 10,
    opened_at: "2026-10-21T10:00:00+00:00",
    closed_at: "2026-10-21T12:00:00+00:00",
    exit_price: 0.98,
    exit_reason: "TP1",
    pnl_usd: 1,
    r_multiple: 0.1,
    mark_price: null,
    unrealized_pnl_usd: null,
    unrealized_r: null,
    source_candle_ts: null,
    timeframe: "1h",
    book: "PASSED",
    evidence_tag: "VALID",
    evidence_reason: "",
    evidence_matches: [],
    in_default_view: true,
    capped_by_leverage: false,
    equity_at_open: 1000,
    nominal_risk_usd: 10,
    r_nominal: 0.1,
    r_actual: 0.1,
    ...over,
  };
}

const VIEW: PaperLedgerView = {
  read_only: true,
  available: true,
  as_of: "2026-10-22T00:00:00+00:00",
  books: [],
  rows: [
    row("a", { closed_at: "2026-10-21T12:00:00+00:00" }),
    row("b", { closed_at: "2026-10-21T15:00:00+00:00" }),
    row("old", { evidence_tag: "PRE_REWRITE", in_default_view: false, opened_at: "2026-10-12T10:00:00+00:00", closed_at: "2026-10-12T12:00:00+00:00" }),
    row("dup", { status: "CANCELLED", evidence_tag: "DUPLICATE_FILL", in_default_view: false }),
    row("open", { status: "OPEN", evidence_tag: null, in_default_view: false, closed_at: null }),
  ],
};

describe("paper ledger view tables (D187)", () => {
  it("default view keeps only rows the backend marked in_default_view, newest first", () => {
    const t = ledgerTables(VIEW, false);
    expect(t.closed.map((r) => r.id)).toEqual(["b", "a"]);
    expect(t.cancelled).toHaveLength(0);
  });

  it("show all history keeps every closed and cancelled row, never open ones", () => {
    const t = ledgerTables(VIEW, true);
    expect(t.closed.map((r) => r.id)).toEqual(["b", "a", "old"]);
    expect(t.cancelled.map((r) => r.id)).toEqual(["dup"]);
  });

  it("summarises the hidden rows by tag in precedence order", () => {
    expect(hiddenSummary(VIEW)).toEqual({ hidden: 2, text: "PRE_REWRITE 1 · DUPLICATE_FILL 1" });
    expect(hiddenSummary(null)).toEqual({ hidden: 0, text: "" });
  });
});
