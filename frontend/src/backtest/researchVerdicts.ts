/**
 * Research verdict badges for the Backtest tab's strategy menu and chips (owner D202).
 * Reads the one static SCORECARD snapshot (research/scorecardSnapshot.ts, also used by the
 * Profit checker); this module only picks the sides to look up and words the hover text.
 * Display only: nothing here changes what a run does.
 */
import { researchVerdictFor, SCORECARD_SNAPSHOT_DATE } from "../research/scorecardSnapshot";

export type BacktestVerdict = "KILLED" | "INCONCLUSIVE" | "NOT RUN";

export type SideVerdict = {
  side: string;
  verdict: BacktestVerdict;
  /** Hover text: research key, stage, reason and source; the kill reason for KILLED. */
  reason: string;
};

function asVerdict(raw: string): BacktestVerdict {
  const v = raw.toUpperCase();
  if (v === "KILLED") return "KILLED";
  if (v === "INCONCLUSIVE") return "INCONCLUSIVE";
  return "NOT RUN";
}

/** Research verdict of one strategy side. A side with no SCORECARD row is NOT RUN. */
export function sideVerdict(combinationId: string, side: string): SideVerdict {
  const lookup = researchVerdictFor(combinationId, side);
  if (!lookup) {
    return {
      side,
      verdict: "NOT RUN",
      reason: `${combinationId} ${side}: no SCORECARD row (snapshot ${SCORECARD_SNAPSHOT_DATE}); research has not run this side.`,
    };
  }
  const { key, entry, note } = lookup;
  return {
    side,
    verdict: asVerdict(entry.verdict),
    reason: `${key} · ${entry.verdict} at ${entry.stage}: ${entry.reason} (${entry.source}; snapshot ${SCORECARD_SNAPSHOT_DATE})${
      note ? `. ${note}` : ""
    }`,
  };
}

/**
 * The sides to look up for a strategy on a book: daily trend books are researched as one
 * BOTH book; the hedge book runs LONG and SHORT; otherwise the book's (or engine's) side.
 */
export function researchSides(
  opt: { engineDirections?: readonly string[] },
  book: string,
  direction?: string,
): string[] {
  if (opt.engineDirections?.includes("BOTH")) return ["BOTH"];
  if (book === "hedge") return ["LONG", "SHORT"];
  if (opt.engineDirections?.length === 1) return [opt.engineDirections[0]];
  if (direction === "LONG" || direction === "SHORT") return [direction];
  return [book === "long" ? "LONG" : "SHORT"];
}

/** One entry per distinct verdict (sides with the same verdict share it). */
export function strategyVerdicts(combinationId: string, sides: readonly string[]): SideVerdict[] {
  const out: SideVerdict[] = [];
  for (const side of sides) {
    const v = sideVerdict(combinationId, side);
    const same = out.find((o) => o.verdict === v.verdict);
    if (same) {
      same.side = `${same.side}+${v.side}`;
      same.reason = `${same.reason}\n${v.reason}`;
    } else {
      out.push(v);
    }
  }
  return out;
}

/** Plain-text suffix for a <select> option, e.g. " — KILLED". */
export function verdictSuffix(verdicts: readonly SideVerdict[]): string {
  if (!verdicts.length) return "";
  return ` — ${verdicts.map((v) => (verdicts.length > 1 ? `${v.side} ${v.verdict}` : v.verdict)).join(", ")}`;
}
