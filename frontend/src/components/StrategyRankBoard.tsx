/**
 * Strategies page board (owner D191, DECISIONS D218). Display only.
 *
 * Top: the research scorecard (static snapshot, research/scorecardSnapshot.ts).
 * Below, collapsed: the old in-sample ranking, a read-only view of strategy_rank/ranked.json.
 * Order, pass flags and sums come from the backend as written by merge_rank.py.
 */
import { useMemo, useState, type ReactNode } from "react";
import type {
  StrategyRankGroup,
  StrategyRankResponse,
  StrategyRankRow,
} from "../api/client";
import { researchVerdictFor } from "../research/scorecardSnapshot";
import { ResearchScorecard, verdictClass } from "./ResearchScorecard";

export const OLD_RANKING_TITLE =
  "Old in-sample ranking (window 2026-02-14 to 2026-10-12 overlaps the sealed holdout; not evidence)";
/** Tab count text (owner D197, DECISIONS D225): cells that passed merge_rank.py's four-part rule. */
export const OLD_RULE_CELLS_TEXT =
  "cells passed the old rule (net > 0, PF > 1.2, >= 15 trades, fees < gross; in-sample, overlaps the holdout)";
/** Current default taker fee (owner D144; grid and backtest defaults since e4e389e). */
export const CURRENT_DEFAULT_TAKER_PCT = 0.05;

const TOP_N = 15;

function usd(value: number | null | undefined, signed = true): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (!signed) return abs;
  if (value > 0) return `+${abs}`;
  return value < 0 ? `−${abs}` : abs;
}

function netClass(value: number | null | undefined): string {
  if (value == null || value === 0) return "text-terminal-muted";
  return value > 0 ? "text-emerald-300" : "text-red-300";
}

function directionLabel(direction: string): string {
  const d = direction.toUpperCase();
  if (d === "LONG") return "Long";
  if (d === "SHORT") return "Short";
  return d ? d.charAt(0) + d.slice(1).toLowerCase() : "—";
}

function directionClass(direction: string): string {
  const d = direction.toUpperCase();
  if (d === "LONG") return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
  if (d === "SHORT") return "border-red-500/40 bg-red-500/10 text-red-300";
  return "border-terminal-border bg-white/5 text-terminal-muted";
}

function sourceNote(row: StrategyRankRow): string | null {
  if (row.description_source === "catalog") {
    return row.variant ? `Variant ${row.variant} of catalog entry ${row.catalog_id}.` : null;
  }
  if (row.description_source === "combination_registry") {
    return "Not in the strategy catalog. Text from the combination registry.";
  }
  return null;
}

const badge =
  "rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide";

const SMALL_RUN = /[:,;]?\s*small run\b[\s\S]*$/i;

/**
 * Card text without the catalog's "small run" result. That run (BTC 1h, 1,200 bars to
 * 2026-10-16) is not part of this ranking, and its window is inside the sealed holdout, so the
 * card leaves it out and says so.
 */
export function withoutSmallRun(summary: string): { text: string; dropped: boolean } {
  const hit = SMALL_RUN.exec(summary);
  if (!hit) return { text: summary, dropped: false };
  const head = summary.slice(0, hit.index).trim().replace(/[:,;]$/, "");
  return { text: head ? `${head}.` : "", dropped: true };
}

/** The research verdict badge for one ranked card. Verdict and stage only; no research numbers. */
export function ResearchVerdictBadge({
  strategyId,
  direction,
}: {
  strategyId: string;
  direction: string;
}) {
  const hit = researchVerdictFor(strategyId, direction);
  if (!hit) {
    return (
      <span
        className={`${badge} border-terminal-border bg-white/5 font-normal normal-case text-terminal-muted`}
        title="This id has no row in the research SCORECARD, KILLED.md or FINAL_VERDICT."
        data-testid="research-verdict-badge"
      >
        Research: no verdict (not in scorecard)
      </span>
    );
  }
  const { entry, key, note } = hit;
  return (
    <span
      className={`${badge} ${verdictClass(entry.verdict)}`}
      title={`Research row ${key} (${entry.source})${note ? `; ${note}` : ""}. See the scorecard above.`}
      data-testid="research-verdict-badge"
    >
      Research: {entry.verdict} · {entry.stage}
    </span>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-terminal-muted">{label}</div>
      <div className="mt-0.5 font-mono text-[12px] text-terminal-text">{children}</div>
    </div>
  );
}

function RankRow({
  row,
  selected,
  onSelect,
}: {
  row: StrategyRankRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const muted = row.do_not_use || row.alias_of != null;
  const note = sourceNote(row);
  const summary = withoutSmallRun(row.summary);
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      data-testid={`rank-row-${row.key}`}
      className={`grid w-full gap-3 rounded-lg border px-3 py-3 text-left transition lg:grid-cols-[2.25rem_minmax(0,1fr)_minmax(0,24rem)] ${
        selected
          ? "border-terminal-accent bg-terminal-accent/10"
          : "border-terminal-border/70 bg-terminal-panel/40 hover:border-terminal-border hover:bg-white/[0.03]"
      } ${muted ? "opacity-75" : ""}`}
    >
      <div className="font-mono text-lg font-semibold text-terminal-accent">{row.rank}</div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[12px] font-semibold text-terminal-text">
            {row.strategy_id}
          </span>
          <ResearchVerdictBadge strategyId={row.strategy_id} direction={row.direction} />
          <span className={`${badge} ${directionClass(row.direction)}`}>
            {directionLabel(row.direction)}
          </span>
          <span className="font-mono text-[10px] text-terminal-muted">{row.timeframe}</span>
          <span
            className={`${badge} border-terminal-border bg-white/5 font-normal normal-case text-terminal-muted`}
            title={row.execution_models.join(" | ")}
          >
            Fill: {row.fill_label}
          </span>
          {row.sizing_note ? (
            <span className={`${badge} border-amber-500/40 bg-amber-500/10 font-normal normal-case text-amber-200`}>
              {row.sizing_note}
            </span>
          ) : null}
          {row.alias_of ? (
            <span className={`${badge} border-violet-500/40 bg-violet-500/10 text-violet-300`}>
              Alias of {row.alias_of.id}
            </span>
          ) : null}
          {row.do_not_use ? (
            <span className={`${badge} border-red-500/40 bg-red-500/10 text-red-300`}>
              Not a pass
            </span>
          ) : null}
          {!row.in_catalog ? (
            <span className={`${badge} border-terminal-border bg-white/5 font-normal text-terminal-muted`}>
              Not in catalog
            </span>
          ) : null}
        </div>
        <div className="mt-1 text-sm font-medium text-terminal-text">{row.name}</div>
        <p className="mt-1 text-[11px] leading-relaxed text-terminal-muted">
          {summary.text}
          {note ? ` ${note}` : ""}
        </p>
        {summary.dropped ? (
          <p className="mt-1 text-[10px] text-terminal-muted" data-testid="small-run-dropped">
            A separate small run in the catalog text is left off this card: it is not part of
            this ranking and its window is inside the sealed holdout.
          </p>
        ) : null}
        {row.gates.length ? (
          <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-terminal-muted">
            <span className="text-terminal-text">How it works: </span>
            {row.gates.join(" · ")}
          </p>
        ) : null}
        {row.aliases.map((alias) => (
          <p key={alias.id} className="mt-1 text-[10px] text-violet-300/90">
            {alias.kind === "identical" ? "Same as" : "Nearly the same as"} {alias.id} ({alias.note}).
          </p>
        ))}
        {row.alias_of ? (
          <p className="mt-1 text-[10px] text-violet-300/90">
            Nearly the same strategy as {row.alias_of.id} ({row.alias_of.note}). Not a separate
            winner.
          </p>
        ) : null}
        {row.do_not_use_reason ? (
          <p className="mt-1 text-[10px] text-red-200/90">{row.do_not_use_reason}</p>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4 lg:grid-cols-2">
        <div className="col-span-2 sm:col-span-4 lg:col-span-2">
          <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
            Coins passed
          </div>
          <div className="mt-0.5 font-mono text-[12px] text-terminal-text">
            {row.coins_passing} of {row.coins}
            <span className="ml-2 text-terminal-muted">
              {row.passing_symbols.length ? row.passing_symbols.join(" ") : "none"}
            </span>
          </div>
        </div>
        <Stat label="Net USD">
          <span className={netClass(row.net_pnl_usd)}>{usd(row.net_pnl_usd)}</span>
        </Stat>
        <Stat label="Trades">{row.trades.toLocaleString("en-US")}</Stat>
        <Stat label="Fees USD">{usd(row.fees_usd)}</Stat>
        <Stat label="Worst coin USD">
          <span className={netClass(row.worst_cell_net_usd)}>{usd(row.worst_cell_net_usd)}</span>
        </Stat>
      </div>
    </button>
  );
}

function groupTitle(group: StrategyRankGroup): string {
  return group.id === "grid" ? group.label : `${group.label} (${group.timeframes})`;
}

export function StrategyRankBoard({
  rank,
  loading,
  error,
  selectedKey,
  onSelect,
}: {
  rank: StrategyRankResponse | null;
  loading: boolean;
  error: string | null;
  selectedKey: string | null;
  onSelect: (row: StrategyRankRow) => void;
}) {
  const [groupId, setGroupId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState<Record<string, boolean>>({});

  const groups = rank?.groups ?? [];
  const group = useMemo(
    () => groups.find((g) => g.id === groupId) ?? groups[0] ?? null,
    [groups, groupId]
  );
  const failed = error || (rank && rank.status !== "OK" ? rank.error || rank.status : null);
  const win = rank?.window;
  const acct = win?.account;

  return (
    <section
      className="mb-6 rounded-lg border border-terminal-border/70 bg-black/20 p-4"
      data-testid="strategy-rank-board"
    >
      <ResearchScorecard />

      <details
        className="mt-6 rounded border border-terminal-border/60 bg-terminal-panel/20 px-3 py-2"
        data-testid="old-ranking"
      >
        <summary
          className="cursor-pointer font-display text-sm font-semibold text-terminal-text"
          data-testid="old-ranking-summary"
        >
          {OLD_RANKING_TITLE}
        </summary>
        <div className="mt-3 flex flex-wrap items-baseline justify-between gap-2">
          {!failed && win?.start && win?.end ? (
            <span
              className="rounded border border-terminal-accent/50 bg-terminal-accent/10 px-2 py-0.5 font-mono text-[12px] font-semibold text-terminal-text"
              data-testid="strategy-rank-dates"
            >
              Tested {win.start} to {win.end}
            </span>
          ) : null}
          {rank?.source?.path ? (
            <span className="font-mono text-[10px] text-terminal-muted">{rank.source.path}</span>
          ) : null}
        </div>

        {loading ? (
          <div className="mt-2 text-sm text-terminal-muted">Loading ranking…</div>
        ) : null}
        {failed ? (
          <div
            className="mt-2 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200"
            data-testid="strategy-rank-error"
          >
            Ranking unavailable. {failed} The catalog below is not affected.
          </div>
        ) : null}

        {!failed && win && group ? (
          <>
            <dl
              className="mt-3 grid gap-x-6 gap-y-2 rounded border border-terminal-border/50 bg-terminal-panel/30 px-3 py-2 text-[11px] sm:grid-cols-2 xl:grid-cols-4"
              data-testid="strategy-rank-window"
            >
              <div>
                <dt className="text-[10px] uppercase tracking-wide text-terminal-muted">
                  Backtest window
                </dt>
                <dd className="mt-0.5 font-mono text-terminal-text">
                  {win.start} to {win.end}
                  {win.note ? <span className="text-terminal-muted"> ({win.note})</span> : null}
                </dd>
                <dd className="mt-0.5 text-amber-200">{win.sample_note}</dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase tracking-wide text-terminal-muted">
                  Coins ({win.coins.length}, {win.market})
                </dt>
                <dd className="mt-0.5 font-mono text-terminal-text">{win.coins.join(" ")}</dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase tracking-wide text-terminal-muted">Account</dt>
                <dd className="mt-0.5 font-mono text-terminal-text">
                  {acct?.principal_usd} USD, {acct?.risk_usd} USD risk per trade, {acct?.leverage}x
                </dd>
                <dd className="mt-0.5 text-terminal-muted">{acct?.exceptions}</dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase tracking-wide text-terminal-muted">Fees</dt>
                <dd className="mt-0.5 font-mono text-terminal-text" data-testid="strategy-rank-fees">
                  this ranking used {win.fees?.taker_pct}% taker; current default{" "}
                  {CURRENT_DEFAULT_TAKER_PCT.toFixed(2)}% (D144)
                </dd>
                {win.fees?.maker_pct != null ? (
                  <dd className="mt-0.5 text-terminal-muted">Maker {win.fees.maker_pct}%.</dd>
                ) : null}
              </div>
            </dl>

            {rank?.pass_rule?.text ? (
              <p className="mt-2 text-[11px] leading-relaxed text-terminal-muted">
                {rank.pass_rule.text}
              </p>
            ) : null}
            {rank?.caveats?.length ? (
              <ul
                className="mt-2 space-y-1 text-[11px] leading-relaxed text-amber-200/90"
                data-testid="strategy-rank-caveats"
              >
                {rank.caveats.map((c) => (
                  <li key={c}>• {c}</li>
                ))}
              </ul>
            ) : null}

            <div className="mt-4 flex flex-wrap gap-2" role="tablist" aria-label="Trading style">
              {groups.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  role="tab"
                  aria-selected={g.id === group.id}
                  onClick={() => setGroupId(g.id)}
                  data-testid={`rank-tab-${g.id}`}
                  className={`rounded border px-2.5 py-1 text-[11px] transition ${
                    g.id === group.id
                      ? "border-terminal-accent bg-terminal-accent/10 text-terminal-text"
                      : "border-terminal-border/70 text-terminal-muted hover:text-terminal-text"
                  }`}
                >
                  {groupTitle(g)}
                  <span className="ml-1.5 font-mono text-[10px] text-terminal-muted">
                    {g.cells_passing ?? 0} of {g.cells ?? 0} {OLD_RULE_CELLS_TEXT}
                  </span>
                </button>
              ))}
            </div>

            {group.caveats.length ? (
              <ul className="mt-3 space-y-1 text-[11px] leading-relaxed text-amber-200/90">
                {group.caveats.map((c) => (
                  <li key={c}>• {c}</li>
                ))}
              </ul>
            ) : null}

            {group.hidden && group.hidden.duplicates + group.hidden.failures > 0 ? (
              <p className="mt-3 text-[11px] text-terminal-muted" data-testid="rank-hidden-note">
                Left off this list: {group.hidden.failures} that lost money or made no trades,{" "}
                {group.hidden.duplicates} duplicate{group.hidden.duplicates === 1 ? "" : "s"} of
                another row.
              </p>
            ) : null}
            {!group.rows.length ? (
              <p className="mt-3 text-[11px] text-terminal-muted">
                No strategy in this group ended the window with a positive net.
              </p>
            ) : null}

            <div className="mt-3 space-y-2" data-testid={`rank-group-${group.id}`}>
              {(showAll[group.id] ? group.rows : group.rows.slice(0, TOP_N)).map((row) => (
                <RankRow
                  key={row.key}
                  row={row}
                  selected={row.key === selectedKey}
                  onSelect={() => onSelect(row)}
                />
              ))}
            </div>
            {group.rows.length > TOP_N ? (
              <button
                type="button"
                onClick={() => setShowAll((s) => ({ ...s, [group.id]: !s[group.id] }))}
                className="mt-3 rounded border border-terminal-border/70 px-2.5 py-1 text-[11px] text-terminal-muted hover:text-terminal-text"
              >
                {showAll[group.id]
                  ? `Show top ${TOP_N}`
                  : `Show all ${group.rows.length} ranked rows`}
              </button>
            ) : null}
          </>
        ) : null}
      </details>
    </section>
  );
}

/** Coin-level results for one ranked row. Pass or fail is the rank file's flag. */
export function StrategyRankDetail({
  row,
  window: win,
}: {
  row: StrategyRankRow;
  window?: StrategyRankResponse["window"];
}) {
  return (
    <section
      className="rounded-lg border border-terminal-border/70 bg-terminal-panel/40 p-4"
      data-testid="strategy-rank-detail"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[10px] uppercase tracking-wide text-terminal-muted">
          Old in-sample rank {row.rank}
        </span>
        <ResearchVerdictBadge strategyId={row.strategy_id} direction={row.direction} />
        <span className={`${badge} ${directionClass(row.direction)}`}>
          {directionLabel(row.direction)}
        </span>
        <span className="font-mono text-[10px] text-terminal-muted">{row.timeframe}</span>
      </div>
      <h3 className="mt-2 text-base font-semibold text-terminal-text">
        {row.strategy_id} <span className="font-normal text-terminal-muted">· {row.name}</span>
      </h3>
      {!row.in_catalog ? (
        <p className="mt-1 text-[11px] text-amber-200/90">
          This id is not in the strategy catalog, so there is no gates or example pane for it.
        </p>
      ) : null}
      {win?.start && win?.end ? (
        <p className="mt-1 text-[11px] leading-relaxed text-terminal-muted">
          <span className="text-terminal-text">Tested: </span>
          <span className="font-mono text-terminal-text">
            {win.start} to {win.end}
          </span>
          {win.note ? ` (${win.note})` : ""}. {win.sample_note}
        </p>
      ) : null}
      <p className="mt-1 text-[11px] leading-relaxed text-terminal-muted">
        <span className="text-terminal-text">Fill model: </span>
        {row.execution_models.join(" | ") || "Not stated"}
      </p>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="Coins passed">
          {row.coins_passing} of {row.coins}
        </Stat>
        <Stat label="Net USD">
          <span className={netClass(row.net_pnl_usd)}>{usd(row.net_pnl_usd)}</span>
        </Stat>
        <Stat label="Trades">{row.trades.toLocaleString("en-US")}</Stat>
        <Stat label="Fees USD">{usd(row.fees_usd)}</Stat>
        <Stat label="Worst coin USD">
          <span className={netClass(row.worst_cell_net_usd)}>{usd(row.worst_cell_net_usd)}</span>
        </Stat>
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[34rem] text-left text-[11px]">
          <thead className="text-[10px] uppercase tracking-wide text-terminal-muted">
            <tr className="border-b border-terminal-border/50">
              <th className="py-1 pr-3 font-normal">Coin</th>
              <th className="py-1 pr-3 text-right font-normal">Net USD</th>
              <th className="py-1 pr-3 text-right font-normal">Win rate</th>
              <th className="py-1 pr-3 text-right font-normal">Profit factor</th>
              <th className="py-1 pr-3 text-right font-normal">Trades</th>
              <th className="py-1 font-normal">Result</th>
            </tr>
          </thead>
          <tbody className="font-mono text-terminal-text">
            {row.cells.map((cell) => (
              <tr key={cell.symbol} className="border-b border-terminal-border/30">
                <td className="py-1 pr-3">{cell.coin}</td>
                <td className={`py-1 pr-3 text-right ${netClass(cell.net_pnl_usd)}`}>
                  {usd(cell.net_pnl_usd)}
                </td>
                <td className="py-1 pr-3 text-right">
                  {cell.win_rate == null || cell.trades_closed === 0
                    ? "—"
                    : `${(cell.win_rate * 100).toFixed(1)}%`}
                </td>
                <td className="py-1 pr-3 text-right">
                  {cell.profit_factor == null ? "—" : cell.profit_factor.toFixed(2)}
                </td>
                <td className="py-1 pr-3 text-right">{cell.trades_closed}</td>
                <td className="py-1">
                  {cell.passes ? (
                    <span className="text-emerald-300">Pass</span>
                  ) : (
                    <span className="text-terminal-muted">
                      Fail{cell.fail_reasons.length ? `: ${cell.fail_reasons.join(", ")}` : ""}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
