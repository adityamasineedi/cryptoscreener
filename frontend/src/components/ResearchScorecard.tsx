/**
 * Research scorecard for the Strategies page (owner D191, DECISIONS D218). Display only.
 *
 * Reads the one static snapshot (research/scorecardSnapshot.ts). It does not call the
 * backend, run a backtest, or read any data.
 */
import { useMemo, useState } from "react";
import {
  LATER_STUDY_KEYS,
  RESEARCH_RESULTS_PAGE_PATH,
  SCORECARD_SNAPSHOT,
  SCORECARD_SNAPSHOT_DATE,
  SCORECARD_SNAPSHOT_SOURCE,
  paperCardVerdict,
  researchVerdictFor,
  verdictText,
  type ResearchVerdict,
} from "../research/scorecardSnapshot";

export const NO_PASS_BANNER = "No strategy passed validation (FINAL_VERDICT).";

const VERDICTS = ["KILLED", "INCONCLUSIVE", "NOT RUN"] as const;
type Filter = "ALL" | (typeof VERDICTS)[number];

const badge =
  "rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide";

export function verdictClass(verdict: string): string {
  switch (verdict.toUpperCase()) {
    case "KILLED":
      return "border-red-500/40 bg-red-500/10 text-red-300";
    case "INCONCLUSIVE":
      return "border-amber-500/40 bg-amber-500/10 text-amber-200";
    case "NOT RUN":
      return "border-terminal-border bg-white/5 text-terminal-muted";
    default:
      return "border-terminal-border bg-white/5 text-terminal-muted";
  }
}

/** One research verdict pill. `text` is shown as written (no upper-casing). */
export function VerdictPill({
  verdict,
  text,
  title,
  testId = "research-verdict-pill",
}: {
  verdict: string;
  text: string;
  title?: string;
  testId?: string;
}) {
  return (
    <span className={`${badge} normal-case ${verdictClass(verdict)}`} title={title} data-testid={testId}>
      {text}
    </span>
  );
}

/** Research verdict of a dashboard paper / forward-test card (owner D200). Nothing for cards without one. */
export function PaperCardVerdictPill({ cardId }: { cardId: string }) {
  const hit = paperCardVerdict(cardId);
  if (!hit) return null;
  return <VerdictPill verdict={hit.verdict} text={`Research: ${hit.text}`} title={hit.title} testId={`paper-verdict-${cardId}`} />;
}

/** Research verdicts of a catalog card, one per researched side (LONG / SHORT / BOTH). */
export function CatalogVerdictPills({ id, direction }: { id: string; direction?: string }) {
  const named = (direction || "").toUpperCase().match(/LONG|SHORT|BOTH/g);
  const sides = named && named.length ? [...new Set(named)] : ["LONG", "SHORT", "BOTH"];
  const hits = sides
    .map((side) => researchVerdictFor(id, side))
    .filter((hit): hit is NonNullable<typeof hit> => hit != null);
  if (!hits.length) return null;
  return (
    <>
      {hits.map(({ key, entry, note }) => (
        <VerdictPill
          key={key}
          verdict={entry.verdict}
          text={`Research: ${verdictText(entry)}${hits.length > 1 ? ` (${key.split("|")[1].toLowerCase()})` : ""}`}
          title={`${key} · ${entry.stage}: ${entry.reason} (${entry.source})${note ? `; ${note}` : ""}`}
          testId={`catalog-verdict-${key}`}
        />
      ))}
    </>
  );
}

function splitKey(key: string): [string, string] {
  const at = key.lastIndexOf("|");
  return at < 0 ? [key, ""] : [key.slice(0, at), key.slice(at + 1)];
}

function sideLabel(side: string): string {
  return side.replace(/_/g, " ").toLowerCase();
}

type Entry = { key: string; entry: ResearchVerdict };

function ScorecardTable({ rows, testId }: { rows: Entry[]; testId: string }) {
  if (!rows.length) {
    return <p className="mt-2 text-[11px] text-terminal-muted">None with this verdict.</p>;
  }
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full min-w-[44rem] text-left text-[11px]" data-testid={testId}>
        <thead className="text-[10px] uppercase tracking-wide text-terminal-muted">
          <tr className="border-b border-terminal-border/50">
            <th className="py-1 pr-3 font-normal">Strategy</th>
            <th className="py-1 pr-3 font-normal">Side</th>
            <th className="py-1 pr-3 font-normal">Final verdict</th>
            <th className="py-1 pr-3 font-normal">Stage</th>
            <th className="py-1 pr-3 font-normal">Gate failed / reason</th>
            <th className="py-1 font-normal">Source</th>
          </tr>
        </thead>
        <tbody className="text-terminal-text">
          {rows.map(({ key, entry }) => {
            const [id, side] = splitKey(key);
            return (
              <tr
                key={key}
                className="border-b border-terminal-border/30 align-top"
                data-testid={`scorecard-row-${key}`}
              >
                <td className="py-1 pr-3 font-mono">
                  {entry.family ? (
                    <span className="mr-1.5 font-sans font-semibold text-terminal-text">
                      {entry.family}
                    </span>
                  ) : null}
                  <span className={entry.family ? "text-terminal-muted" : ""}>{id}</span>
                </td>
                <td className="py-1 pr-3 text-terminal-muted">{sideLabel(side)}</td>
                <td className="py-1 pr-3">
                  <span className={`${badge} ${entry.display ? "normal-case " : ""}${verdictClass(entry.verdict)}`}>
                    {verdictText(entry)}
                  </span>
                </td>
                <td className="py-1 pr-3 font-mono text-[10px] text-terminal-muted">{entry.stage}</td>
                <td className="py-1 pr-3 leading-relaxed text-terminal-muted">{entry.reason}</td>
                <td className="py-1 text-[10px] text-terminal-muted">{entry.source}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ResearchScorecard() {
  const [filter, setFilter] = useState<Filter>("ALL");

  const { later, earlier, counts } = useMemo(() => {
    const all: Entry[] = Object.entries(SCORECARD_SNAPSHOT).map(([key, entry]) => ({ key, entry }));
    const laterSet = new Set(LATER_STUDY_KEYS);
    const counted: Record<string, number> = { ALL: all.length };
    for (const v of VERDICTS) counted[v] = all.filter((e) => e.entry.verdict === v).length;
    return {
      later: LATER_STUDY_KEYS.filter((k) => SCORECARD_SNAPSHOT[k]).map((key) => ({
        key,
        entry: SCORECARD_SNAPSHOT[key],
      })),
      earlier: all.filter((e) => !laterSet.has(e.key)),
      counts: counted,
    };
  }, []);

  const keep = (e: Entry) => filter === "ALL" || e.entry.verdict === filter;

  return (
    <div data-testid="research-scorecard">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-base font-semibold text-terminal-text">
          Research scorecard
        </h2>
        <span className="font-mono text-[10px] text-terminal-muted" data-testid="scorecard-snapshot">
          Snapshot {SCORECARD_SNAPSHOT_DATE}, {SCORECARD_SNAPSHOT_SOURCE}
        </span>
      </div>

      <div
        role="status"
        className="mt-2 rounded border border-red-500/50 bg-red-500/10 px-3 py-2 text-sm font-semibold text-red-200"
        data-testid="no-pass-banner"
      >
        {NO_PASS_BANNER}
      </div>

      <p className="mt-2 text-[11px] leading-relaxed text-terminal-muted" data-testid="scorecard-pointer">
        Every strategy with its final research verdict, the stage it reached and the gate it
        failed. Full results, charts and budgets:{" "}
        <code className="font-mono text-terminal-text">{RESEARCH_RESULTS_PAGE_PATH}</code> in the
        research-audit worktree. The app has no route to that page; open the file directly.
      </p>
      <p className="mt-1 text-[11px] leading-relaxed text-terminal-muted" data-testid="scorecard-harness">
        These verdicts come from the research harness, not from the old ranking further down. The
        tournament ran each engine as coded (the COMBO family on its next-bar-open book with
        adverse slippage; the multicap engine still at the signal close, flagged in DESIGN_FLAWS)
        with 0.04% taker and 0.02% maker. D142 showed this changes no verdict. N1, N2, HD-2, HR-1, HD-1 and H-COMBO used the D144 costs:
        0.05% taker, 2 bp slippage (5 bp on seven thinner coins), no zero-slippage fills.
      </p>

      <div className="mt-3 flex flex-wrap gap-2" role="tablist" aria-label="Verdict filter">
        {(["ALL", ...VERDICTS] as Filter[]).map((f) => (
          <button
            key={f}
            type="button"
            role="tab"
            aria-selected={f === filter}
            onClick={() => setFilter(f)}
            data-testid={`scorecard-filter-${f}`}
            className={`rounded border px-2.5 py-1 text-[11px] transition ${
              f === filter
                ? "border-terminal-accent bg-terminal-accent/10 text-terminal-text"
                : "border-terminal-border/70 text-terminal-muted hover:text-terminal-text"
            }`}
          >
            {f === "ALL" ? "All" : f}
            <span className="ml-1.5 font-mono text-[10px] text-terminal-muted">{counts[f]}</span>
          </button>
        ))}
      </div>

      <h3 className="mt-4 text-[11px] font-semibold uppercase tracking-wide text-terminal-text">
        Later studies (FINAL_VERDICT, KILLED.md)
      </h3>
      <ScorecardTable rows={later.filter(keep)} testId="scorecard-later" />

      <h3 className="mt-4 text-[11px] font-semibold uppercase tracking-wide text-terminal-text">
        Tournament and earlier rulings (SCORECARD.csv, KILLED.md, DECISIONS)
      </h3>
      <div className="max-h-[28rem] overflow-y-auto">
        <ScorecardTable rows={earlier.filter(keep)} testId="scorecard-earlier" />
      </div>
    </div>
  );
}
