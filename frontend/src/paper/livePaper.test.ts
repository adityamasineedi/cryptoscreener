import { describe, expect, it } from "vitest";
import type { PaperPosition, PaperStatus, PassedRegimeResponse } from "../api/client";
import { isLivePaperPosition, liveAutoStrategies } from "./livePaper";

const status = {
  enabled: true,
  v1_watcher: {
    enabled: true,
    timeframe: "1h",
    combo_id: "COMBO_02",
    books: [{ symbol: "BTCUSDT", timeframe: "1h" }],
  },
} as PaperStatus;

const regime = {
  trend: "BEARISH",
  daily_read_ok: true,
  watcher_loop_enabled: true,
  book_open: true,
  book_note: "",
  allowed: [{ strategy_id: "COMBO_01", timeframe: "1h", direction: "SHORT" }],
  symbols: ["BTCUSDT", "NEARUSDT"],
  symbol_risk: {
    large_symbols: ["BTCUSDT"],
    mid_symbols: ["NEARUSDT"],
    large_risk_percent: 0.02,
    mid_risk_percent: 0.01,
  },
} as PassedRegimeResponse;

describe("liveAutoStrategies", () => {
  it("lists the v1 long watcher and the passed sides the daily trend allows", () => {
    const book = liveAutoStrategies(status, regime);
    expect(book.rows.map((row) => `${row.strategyId} ${row.direction}`)).toEqual([
      "COMBO_02 LONG",
      "COMBO_01 SHORT",
    ]);
    expect(book.rows[1]?.coins).toBe("BTC, NEAR");
    expect(book.notes).toEqual([]);
  });

  it("lists nothing when paper auto is off", () => {
    const book = liveAutoStrategies({ ...status, enabled: false }, regime);
    expect(book.rows).toEqual([]);
    expect(book.notes[0]).toMatch(/Paper auto is off/);
  });

  it("does not call auto off before status has loaded", () => {
    const book = liveAutoStrategies(null, null);
    expect(book.rows).toEqual([]);
    expect(book.notes[0]).toMatch(/Loading/);
  });

  it("drops the passed book when its loop is off", () => {
    const book = liveAutoStrategies(status, { ...regime, watcher_loop_enabled: false });
    expect(book.rows.map((row) => row.strategyId)).toEqual(["COMBO_02"]);
    expect(book.notes.some((note) => note.includes("Passed-book watcher is off"))).toBe(true);
  });
});

describe("isLivePaperPosition", () => {
  it("keeps passed-book and v1 trades and drops research trades", () => {
    const row = (source: string): PaperPosition =>
      ({ id: source, signal_snippet: { source } }) as PaperPosition;
    expect(isLivePaperPosition(row("PASSED_CAP_BOOK"))).toBe(true);
    expect(isLivePaperPosition(row("V1_PAPER_WATCHER"))).toBe(true);
    expect(isLivePaperPosition(row("LEGACY_SETUP_SIGNAL"))).toBe(false);
    expect(isLivePaperPosition(row("V21A_PAPER_WATCHER"))).toBe(false);
  });
});
