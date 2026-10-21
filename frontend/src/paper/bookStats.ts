import type { PaperPosition } from "../api/client";

export type BookId = "PASSED" | "V1" | "LEGACY";

export type BookRollup = {
  id: BookId;
  label: string;
  equity: number;
  realized: number;
  netR: number;
  trades: number;
  openRisk: number;
};

const LABELS: Record<BookId, string> = {
  PASSED: "PARITY TEST",
  V1: "PAPER",
  LEGACY: "LEGACY (disabled)",
};

export function paperBook(row: PaperPosition): BookId {
  const snippet = row.signal_snippet || {};
  const source = String(snippet.source || row.source || "");
  const strategy = String(snippet.strategy_id || row.strategy_id || "");
  if (source === "PASSED_CAP_BOOK" || strategy.startsWith("PASSED|")) return "PASSED";
  if (source === "V1_PAPER_WATCHER" || strategy === "COMBO_02_V1") return "V1";
  return "LEGACY";
}

function counted(row: PaperPosition): boolean {
  const status = String(row.status || "").toUpperCase();
  if (status === "CANCELLED") return false;
  if (row.exit_reason === "duplicate_fill") return false;
  return true;
}

export function rollupBooks(
  open: PaperPosition[],
  closed: PaperPosition[],
  startingEquity: number,
): BookRollup[] {
  const ids: BookId[] = ["PASSED", "V1", "LEGACY"];
  return ids.map((id) => {
    const done = closed.filter((row) => paperBook(row) === id && counted(row));
    const live = open.filter((row) => paperBook(row) === id);
    const realized = done.reduce((sum, row) => sum + Number(row.pnl_usd || 0), 0);
    const netR = done.reduce((sum, row) => sum + Number(row.r_multiple || 0), 0);
    const openRisk = live.reduce((sum, row) => sum + Number(row.risk_usd || 0), 0);
    return {
      id,
      label: LABELS[id],
      equity: startingEquity + realized,
      realized,
      netR,
      trades: done.length,
      openRisk,
    };
  });
}

export function splitCancelled(rows: PaperPosition[]): {
  visible: PaperPosition[];
  duplicates: PaperPosition[];
} {
  const cancelled = rows.filter((row) => String(row.status || "").toUpperCase() === "CANCELLED");
  return {
    duplicates: cancelled.filter((row) => row.exit_reason === "duplicate_fill"),
    visible: cancelled.filter((row) => row.exit_reason !== "duplicate_fill"),
  };
}
