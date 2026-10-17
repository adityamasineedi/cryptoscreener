import type { PaperOpportunity } from "../api/client";

/** Closed rows the paper screen asks for. Realized PnL is the full ledger. */
export const CLOSED_LIMIT = 100;

function money(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pct(n: number): string {
  const value = Math.abs(n) <= 1 ? n * 100 : n;
  return `${value.toFixed(2).replace(/\.?0+$/, "")}%`;
}

/** Action cell for a 15m trade-chance row. Only says "Auto open" when on_setup_signal would open. */
export function opportunityAction(
  row: Pick<PaperOpportunity, "already_open" | "tier">,
  flags: { autoOn: boolean; watcherOwns: boolean; legacyAutoOn: boolean },
): string {
  if (row.already_open) return "Open";
  if (flags.watcherOwns) return "Research only";
  if (row.tier !== "READY") return "Watch";
  if (!flags.legacyAutoOn) return "Research only (legacy auto-entry off)";
  return flags.autoOn ? "Auto open (15m research)" : "Ready (Auto OFF)";
}

/** Risk cell. After the leverage cap the dollars are below the planned percent, so say so. */
export function paperRiskLabel(
  riskPercent: number | null | undefined,
  riskUsd: number | null | undefined,
  execution: unknown,
): string {
  const exec = (execution && typeof execution === "object" ? execution : {}) as {
    capped_by_leverage?: boolean;
    leverage?: number;
  };
  if (exec.capped_by_leverage === true) {
    const lev = Number(exec.leverage);
    const cap = Number.isFinite(lev) && lev > 0 ? `${lev}×` : "leverage";
    const planned = riskPercent != null ? `; planned ${pct(riskPercent)}` : "";
    return `$${money(riskUsd)} (capped ${cap}${planned})`;
  }
  return riskPercent != null ? `${pct(riskPercent)} / $${money(riskUsd)}` : `$${money(riskUsd)}`;
}

/** Note under the closed table when it shows fewer rows than the realized PnL covers. */
export function closedTruncationNote(shown: number, limit: number): string | null {
  if (shown < limit) return null;
  return `Showing the ${shown} most recent closed trades. Realized PnL covers the full ledger.`;
}
