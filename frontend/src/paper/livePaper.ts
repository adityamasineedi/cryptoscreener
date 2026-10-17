import type { PaperPosition, PaperStatus, PassedRegimeResponse } from "../api/client";

/** Sources that open paper trades without a research screen. */
export const LIVE_PAPER_SOURCES = ["PASSED_CAP_BOOK", "V1_PAPER_WATCHER"] as const;

export type LiveAutoStrategy = {
  strategyId: string;
  direction: string;
  timeframe: string;
  book: string;
  coins: string;
  stake: string;
};

export type LiveAutoBook = {
  rows: LiveAutoStrategy[];
  notes: string[];
};

function positionSource(row: PaperPosition): string {
  const snippet = row.signal_snippet;
  const fromSnippet =
    snippet && typeof snippet.source === "string" ? snippet.source : "";
  return String(row.source || fromSnippet || "");
}

export function isLivePaperPosition(row: PaperPosition): boolean {
  return (LIVE_PAPER_SOURCES as readonly string[]).includes(positionSource(row));
}

/** Open / closed / cancelled, as the paper screen splits them. Cancelled is not a closed trade. */
export function splitLivePaperRows(rows: PaperPosition[]): {
  open: PaperPosition[];
  closed: PaperPosition[];
  cancelled: PaperPosition[];
} {
  const live = rows.filter(isLivePaperPosition);
  const state = (row: PaperPosition) => String(row.status || "").toUpperCase();
  return {
    open: live.filter((row) => state(row) === "OPEN"),
    closed: live.filter((row) => state(row) !== "OPEN" && state(row) !== "CANCELLED"),
    cancelled: live.filter((row) => state(row) === "CANCELLED"),
  };
}

function coinLine(symbols: string[] | undefined, fallback: string): string {
  if (!symbols?.length) return fallback;
  return symbols.map((symbol) => symbol.replace(/USDT$/, "")).join(", ");
}

function passedStake(direction: string, trend: string): string {
  const side = direction.toUpperCase();
  const against =
    (trend === "BULLISH" && side === "SHORT") || (trend === "BEARISH" && side === "LONG");
  return against
    ? "Large 1% · mid 0.5% (against the daily trend)"
    : "Large 2% · mid 1%";
}

/** Strategies that will open a paper trade on the next valid closed bar. */
export function liveAutoStrategies(
  status: PaperStatus | null,
  regime: PassedRegimeResponse | null,
): LiveAutoBook {
  const rows: LiveAutoStrategy[] = [];
  const notes: string[] = [];
  if (!status) {
    notes.push("Loading paper status.");
    return { rows, notes };
  }
  if (!status.enabled) {
    notes.push("Paper auto is off, so nothing on this screen can open a trade.");
    return { rows, notes };
  }

  const watcher = status.v1_watcher;
  if (watcher?.enabled && watcher.replay_mode !== true) {
    const books = (watcher.books || []).filter((book) => (book.timeframe || "1h") === "1h");
    rows.push({
      strategyId: watcher.combo_id || "COMBO_02",
      direction: "LONG",
      timeframe: watcher.timeframe || "1h",
      book: "COMBO_02 v1 watcher",
      coins: coinLine(
        books.map((book) => book.symbol),
        "Watcher book list",
      ),
      stake: "2% of equity",
    });
  } else {
    notes.push("COMBO_02 v1 watcher is not auto-trading.");
  }

  if (!regime) {
    notes.push("Passed-book status has not loaded.");
    return { rows, notes };
  }
  if (regime.watcher_loop_enabled !== true) {
    notes.push("Passed-book watcher is off.");
    return { rows, notes };
  }
  if (regime.book_open !== true) {
    notes.push(regime.book_note || "Passed-book entries are off.");
    return { rows, notes };
  }
  if (regime.daily_read_ok === false) {
    notes.push("BTC daily trend could not be read, so the passed book will not open.");
    return { rows, notes };
  }
  const trend = regime.trend;
  if (trend !== "BULLISH" && trend !== "BEARISH") {
    notes.push("BTC daily trend is neutral, so the passed book will not open.");
    return { rows, notes };
  }

  const large = new Set(regime.symbol_risk?.large_symbols || []);
  const mid = new Set(regime.symbol_risk?.mid_symbols || []);
  const symbols = regime.symbols || [...large, ...mid];
  for (const side of regime.allowed || []) {
    rows.push({
      strategyId: side.strategy_id,
      direction: side.direction,
      timeframe: side.timeframe,
      book: "Passed cap book",
      coins: coinLine(symbols, "Passed-book coins"),
      stake: passedStake(side.direction, trend),
    });
  }
  if ((regime.allowed || []).length === 0) {
    notes.push("The passed book has no side allowed for this daily trend.");
  }
  return { rows, notes };
}
