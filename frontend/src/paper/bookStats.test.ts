import { describe, expect, it } from "vitest";
import type { PaperPosition } from "../api/client";
import { paperBook, rollupBooks, splitCancelled } from "./bookStats";

function row(over: Partial<PaperPosition>): PaperPosition {
  return {
    id: "1",
    symbol: "BTCUSDT",
    side: "LONG",
    status: "CLOSED",
    entry_price: 1,
    stop_price: 0.9,
    tp1_price: 1.2,
    quantity: 1,
    risk_usd: 10,
    opened_at: "2026-10-21T00:00:00+00:00",
    closed_at: "2026-10-21T01:00:00+00:00",
    exit_price: 1.1,
    exit_reason: "TP1",
    pnl_usd: 4,
    r_multiple: 0.4,
    mark_price: null,
    unrealized_pnl_usd: null,
    unrealized_r: null,
    source_candle_ts: null,
    timeframe: "1h",
    ...over,
  };
}

describe("paper book rollups", () => {
  it("keeps passed, v1 and legacy apart and drops duplicate fills", () => {
    const closed = [
      row({ id: "p", signal_snippet: { source: "PASSED_CAP_BOOK" }, pnl_usd: 5, r_multiple: 0.5 }),
      row({ id: "v", signal_snippet: { source: "V1_PAPER_WATCHER" }, pnl_usd: -2, r_multiple: -1 }),
      row({ id: "d", status: "CANCELLED", exit_reason: "duplicate_fill", pnl_usd: 99, r_multiple: 9 }),
    ];
    const open = [row({ id: "o", status: "OPEN", signal_snippet: { strategy_id: "COMBO_02_V1" }, risk_usd: 20 })];
    expect(paperBook(closed[0])).toBe("PASSED");
    expect(paperBook(open[0])).toBe("V1");
    const books = rollupBooks(open, closed, 1000);
    expect(books.find((book) => book.id === "PASSED")).toMatchObject({ realized: 5, netR: 0.5, trades: 1, openRisk: 0 });
    expect(books.find((book) => book.id === "V1")).toMatchObject({ realized: -2, trades: 1, openRisk: 20, equity: 998 });
    expect(books.find((book) => book.id === "LEGACY")?.trades).toBe(0);
    expect(splitCancelled(closed).duplicates).toHaveLength(1);
    expect(splitCancelled(closed).visible).toHaveLength(0);
  });
});
