import type { PaperEvidenceTag, PaperLedgerRow, PaperLedgerView } from "../api/client";

/** Owner D187 precedence: the backend picks the first rule a row matches, in this order. */
export const EVIDENCE_TAGS: PaperEvidenceTag[] = [
  "LEGACY",
  "PRE_REWRITE",
  "PRE_COUNTERS",
  "CATCHUP_CLOSE",
  "DUPLICATE_FILL",
  "VALID",
];

function newestFirst(a: PaperLedgerRow, b: PaperLedgerRow): number {
  const ta = Date.parse(a.closed_at || a.opened_at || "") || 0;
  const tb = Date.parse(b.closed_at || b.opened_at || "") || 0;
  if (tb !== ta) return tb - ta;
  return a.id < b.id ? 1 : -1;
}

/**
 * Closed and cancelled rows for the tables. Default view: only rows the backend marked
 * in_default_view (VALID and opened at or after the book's forward start). Show all: every row.
 */
export function ledgerTables(
  view: PaperLedgerView | null,
  showAll: boolean,
): { closed: PaperLedgerRow[]; cancelled: PaperLedgerRow[] } {
  const rows = (view?.rows || []).filter((row) => showAll || row.in_default_view);
  const status = (row: PaperLedgerRow) => String(row.status || "").toUpperCase();
  return {
    closed: rows.filter((row) => status(row) === "CLOSED").sort(newestFirst),
    cancelled: rows.filter((row) => status(row) === "CANCELLED").sort(newestFirst),
  };
}

/** "LEGACY 17 · PRE_REWRITE 47 …" for the hidden (non-default) closed/cancelled rows. */
export function hiddenSummary(view: PaperLedgerView | null): { hidden: number; text: string } {
  const counts: Partial<Record<PaperEvidenceTag, number>> = {};
  let hidden = 0;
  for (const row of view?.rows || []) {
    if (row.evidence_tag == null || row.in_default_view) continue;
    counts[row.evidence_tag] = (counts[row.evidence_tag] || 0) + 1;
    hidden += 1;
  }
  const text = EVIDENCE_TAGS.filter((tag) => counts[tag])
    .map((tag) => `${tag} ${counts[tag]}`)
    .join(" · ");
  return { hidden, text };
}

export function tagTone(tag: PaperEvidenceTag | null | undefined): string {
  if (tag === "VALID") return "text-emerald-300";
  if (tag === "DUPLICATE_FILL" || tag === "CATCHUP_CLOSE") return "text-rose-300";
  if (tag == null) return "text-terminal-muted";
  return "text-amber-200";
}
