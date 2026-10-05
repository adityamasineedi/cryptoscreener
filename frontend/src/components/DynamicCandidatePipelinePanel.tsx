/**
 * Dynamic Candidate Pipeline panel.
 * Research advance is allowed; no "Enable for v1" / Telegram / production actions.
 * Never displays bare approval wording — always experimental-paper / production scoped.
 */
import { Fragment, useCallback, useEffect, useState } from "react";
import {
  advanceDynamicCandidates,
  fetchDynamicCandidateDetail,
  fetchDynamicCandidates,
  type DynamicCandidateRow,
  type DynamicCandidatesResponse,
  type DynamicEligibilityReason,
} from "../api/client";
import { dedupedPanelFetch } from "../lib/researchPanelCache";

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function pctRisk(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(2)}%`;
}

function paperApprovalLabel(code: string | null | undefined): string {
  if (
    code === "EXPERIMENTAL_PAPER_APPROVED" ||
    code === "PAPER_VALIDATING"
  ) {
    return "Experimental paper approved";
  }
  return "Not approved for paper";
}

function operationalStateLabel(code: string | null | undefined): string {
  switch (code) {
    case "PAPER_VALIDATING":
      return "PAPER_VALIDATING";
    case "PAPER_APPROVED":
      return "Experimental paper approved";
    case "PRODUCTION_APPROVED":
      return "Not approved for production";
    case "SUSPENDED":
      return "SUSPENDED";
    case "NOT_APPROVED":
    case "NOT_APPROVED_FOR_PAPER":
      return "Not approved for paper";
    default:
      if (!code || code === "PRODUCTION_APPROVED" || code === "BARE_APPROVED_FORBIDDEN") {
        return "Not approved for paper";
      }
      return code;
  }
}

function oosStatusLabel(row: DynamicCandidateRow): string {
  if (row.oos_display_status) return row.oos_display_status;
  const raw = (row.oos_status || "").toUpperCase();
  if (
    raw === "PASS" ||
    raw === "COMPLETED_PASS" ||
    raw === "V2_PAPER_CANDIDATE"
  ) {
    return "PASS";
  }
  return row.oos_status || "—";
}

function baseBacktestLabel(row: DynamicCandidateRow): string {
  if (row.base_backtest_status) return row.base_backtest_status;
  const raw = (row.backtest_status || "").toUpperCase();
  if (raw === "COMPLETED" || raw === "PASS") return "PASS";
  return row.backtest_status || "—";
}

function productionApprovalLabel(code: string | null | undefined, approved?: boolean): string {
  if (approved || code === "PRODUCTION_APPROVED") {
    return "Production approved";
  }
  return "Not approved for production";
}

function telegramLabel(code: string | null | undefined, eligible?: boolean): string {
  if (eligible || code === "ENABLED") return "Telegram enabled";
  return "Telegram disabled";
}

function badgeClass(badge: string | null | undefined): string {
  switch (badge) {
    case "V2 PAPER CANDIDATE":
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
    case "EXPERIMENTAL PAPER":
      return "border-cyan-500/40 bg-cyan-500/10 text-cyan-200";
    case "PRODUCTION APPROVED (future, not v1)":
      return "border-violet-500/40 bg-violet-500/10 text-violet-200";
    case "RESEARCH REJECTED":
    case "OOS FAILED":
    case "DATA BLOCKED":
    case "SUSPENDED":
      return "border-red-500/40 bg-red-500/10 text-red-300";
    case "DISCOVERY ONLY":
      return "border-sky-500/40 bg-sky-500/10 text-sky-200";
    default:
      return "border-amber-500/40 bg-amber-500/10 text-amber-200";
  }
}

type RuleReason = DynamicEligibilityReason & {
  name?: string;
  detail?: string;
};

function formatRuleLine(r: RuleReason, i: number): string {
  const label = r.rule || r.name || `rule_${i}`;
  const required = r.required ? ` ${r.required}` : "";
  const actual =
    r.actual !== undefined && r.actual !== null ? ` — actual ${String(r.actual)}` : "";
  const mark = r.passed ? "✓" : "✗";
  return `${mark} ${label}${required}${actual}`;
}

function RuleList({
  title,
  rules,
}: {
  title: string;
  rules: RuleReason[] | null | undefined;
}) {
  return (
    <div className="mt-1">
      <div className="text-[10px] uppercase tracking-wider text-terminal-text">{title}</div>
      {!rules || rules.length === 0 ? (
        <p className="text-[11px] text-terminal-muted">No persisted rule reasons.</p>
      ) : (
        <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
          {rules.map((r, i) => {
            const pass = Boolean(r.passed);
            return (
              <li key={`${r.rule || r.name || i}-${i}`} className={pass ? "text-emerald-300" : "text-red-300"}>
                {formatRuleLine(r, i)}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function SafetyBadges({ row }: { row: DynamicCandidateRow }) {
  const badges =
    row.safety_badges && row.safety_badges.length > 0
      ? row.safety_badges
      : row.operational_state === "PAPER_VALIDATING" ||
          row.operator_paper_approval === "EXPERIMENTAL_PAPER_APPROVED"
        ? ["V2 RESEARCH", "PAPER ONLY", "NOT V1", "NOT PRODUCTION", "TELEGRAM OFF"]
        : ["V2 RESEARCH", "NOT V1", "NOT PRODUCTION", "TELEGRAM OFF"];
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {badges.map((b) => (
        <span
          key={b}
          className="rounded border border-terminal-border/80 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-terminal-muted"
        >
          {b}
        </span>
      ))}
    </div>
  );
}

function CandidateDetail({ row }: { row: DynamicCandidateRow }) {
  const report = (row.portfolio_report || {}) as Record<string, unknown>;
  const baseElig = (report.base_eligibility || {}) as {
    tier?: string;
    passed?: boolean;
    reasons?: RuleReason[];
  };
  const oosElig = (report.oos_eligibility || {}) as {
    tier?: string;
    passed?: boolean;
    reasons?: RuleReason[];
  };
  const blockReason =
    typeof report.last_block_reason === "string"
      ? report.last_block_reason
      : typeof report.blocked_reason === "string"
        ? report.blocked_reason
        : row.blocked_reason || null;
  const blockDetail =
    report.last_block_detail && typeof report.last_block_detail === "object"
      ? (report.last_block_detail as Record<string, unknown>)
      : null;

  return (
    <div className="mt-2 grid gap-3 border-t border-terminal-border/50 pt-2 text-[11px] text-terminal-muted sm:grid-cols-2 lg:grid-cols-3">
      <section>
        <h4 className="text-[10px] uppercase tracking-wider text-terminal-text">Research</h4>
        <p>Tier: {row.research_tier || row.backtest_tier || "—"}</p>
        <p>Trade count: {row.backtest_trade_count ?? "—"}</p>
        <p>Win rate: {num(row.backtest_win_rate)}</p>
        <p>Net avg R: {num(row.backtest_net_avg_r)}</p>
        <p>Profit factor: {num(row.backtest_profit_factor)}</p>
        <p>Net PnL: {num(row.backtest_net_pnl)}</p>
        <p>Fees: {num(row.backtest_fees)}</p>
        <p>Max DD: {num(row.backtest_max_dd_r)}R</p>
        <p>Max losing streak: {row.backtest_max_losing_streak ?? "—"}</p>
        <RuleList title="Backtest checks" rules={row.eligibility_reasons || baseElig.reasons} />
      </section>

      <section>
        <h4 className="text-[10px] uppercase tracking-wider text-terminal-text">OOS</h4>
        <p>Status: {oosStatusLabel(row)}</p>
        <p>Trade count: {row.oos_trade_count ?? "—"}</p>
        <p>Net avg R: {num(row.oos_net_avg_r)}</p>
        <p>Profit factor: {num(row.oos_profit_factor)}</p>
        <p>Net PnL: {num(row.oos_net_pnl)}</p>
        <p>Max DD: {num(row.oos_max_dd_r)}R</p>
        <p>Max losing streak: {row.oos_max_losing_streak ?? "—"}</p>
        <RuleList title="OOS checks" rules={row.oos_rule_reasons || oosElig.reasons} />
      </section>

      <section>
        <h4 className="text-[10px] uppercase tracking-wider text-terminal-text">Portfolio</h4>
        <p>BTC overlap: {num(row.portfolio_overlap_btc, 2)}</p>
        <p>ETH overlap: {num(row.portfolio_overlap_eth, 2)}</p>
        <p>SOL overlap: {num(row.portfolio_overlap_sol, 2)}</p>
        <p>Peak concurrent positions: {row.peak_concurrent_positions ?? "—"}</p>
        <p>Incremental DD: {num(row.portfolio_incremental_dd_r)}R</p>
        <p>Portfolio status: {row.portfolio_status || "—"}</p>
        {blockReason ? (
          <p className="text-amber-300">blocked_reason: {blockReason}</p>
        ) : null}
        {blockDetail ? (
          <p className="font-mono text-[10px] text-amber-200/80">
            v1={String(blockDetail.v1_open_risk_percent ?? blockDetail.v1_open_risk ?? "—")} ·
            dyn={String(
              blockDetail.dynamic_requested_risk_percent ??
                blockDetail.requested_risk ??
                "—"
            )}{" "}
            · max={String(blockDetail.max_total_risk_percent ?? blockDetail.cap ?? "—")}
          </p>
        ) : null}
      </section>

      <section>
        <h4 className="text-[10px] uppercase tracking-wider text-terminal-text">
          Operational
        </h4>
        <p>State: {operationalStateLabel(row.operational_state)}</p>
        <p>
          Operator approval:{" "}
          {paperApprovalLabel(row.operator_paper_approval)}
        </p>
        <p>Paper approved by: {row.operator_approved_by || "—"}</p>
        <p>Paper approval timestamp: {row.operator_approved_at_utc || "—"}</p>
        <p>Risk: {pctRisk(row.risk_percent)}</p>
        <p>
          Production approved:{" "}
          {row.production_approved ? "YES" : "NO"}
        </p>
        <p>
          Telegram eligible: {row.telegram_eligible ? "YES" : "NO"}
        </p>
        <p className="text-terminal-text">
          {productionApprovalLabel(row.production_approval, row.production_approved)} ·{" "}
          {telegramLabel(row.telegram_eligibility, row.telegram_eligible)}
        </p>
        <p>
          Strategy: {row.strategy_id || "—"} · Version: {row.combo_version || "—"}
        </p>
        <p>Source: {row.source || "—"}</p>
      </section>

      <section className="sm:col-span-2 lg:col-span-3">
        <h4 className="text-[10px] uppercase tracking-wider text-terminal-text">
          Status summary
        </h4>
        <div className="mt-1 grid gap-1 font-mono text-[11px] text-terminal-text sm:grid-cols-2">
          <p>Research: {row.research_tier || row.backtest_tier || "—"}</p>
          <p>Base backtest: {baseBacktestLabel(row)}</p>
          <p>OOS: {oosStatusLabel(row)}</p>
          <p>Portfolio: {row.portfolio_status || "—"}</p>
          <p>Operational: {operationalStateLabel(row.operational_state)}</p>
          <p>Approval: {paperApprovalLabel(row.operator_paper_approval)}</p>
          <p>
            Production:{" "}
            {productionApprovalLabel(row.production_approval, row.production_approved)}
          </p>
          <p>Telegram: {telegramLabel(row.telegram_eligibility, row.telegram_eligible)}</p>
          <p>Risk: {pctRisk(row.risk_percent)}</p>
          <p>Source: {row.source || "—"}</p>
        </div>
      </section>
    </div>
  );
}

export function DynamicCandidatePipelinePanel() {
  const [data, setData] = useState<DynamicCandidatesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [advancing, setAdvancing] = useState(false);
  const [advanceNote, setAdvanceNote] = useState<string | null>(null);
  const [lastRunSummary, setLastRunSummary] = useState<{
    run: Record<string, number>;
    registry: Record<string, number>;
  } | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [detailBySymbol, setDetailBySymbol] = useState<
    Record<string, DynamicCandidateRow>
  >({});
  const [detailLoading, setDetailLoading] = useState<Record<string, boolean>>({});
  const [detailError, setDetailError] = useState<Record<string, string>>({});

  const reload = useCallback(() => {
    setLoading(true);
    return dedupedPanelFetch(
      "latest",
      "dynamic",
      (signal) => fetchDynamicCandidates(undefined, { signal }),
    )
      .then((payload) => {
        setData(payload);
        setError(null);
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    let cancelled = false;
    setLoading(true);
    dedupedPanelFetch(
      "latest",
      "dynamic",
      (signal) => fetchDynamicCandidates(undefined, { signal }),
      ac.signal,
    )
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled && (e as Error)?.name !== "AbortError") {
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, []);

  async function loadDetail(symbol: string) {
    const ac = new AbortController();
    setDetailLoading((prev) => ({ ...prev, [symbol]: true }));
    setDetailError((prev) => {
      const next = { ...prev };
      delete next[symbol];
      return next;
    });
    try {
      const payload = await fetchDynamicCandidateDetail(symbol, {
        signal: ac.signal,
      });
      if (payload.status === "OK" && payload.candidate) {
        setDetailBySymbol((prev) => ({ ...prev, [symbol]: payload.candidate! }));
      } else {
        setDetailError((prev) => ({
          ...prev,
          [symbol]: payload.status || "NOT_FOUND",
        }));
      }
    } catch (e) {
      setDetailError((prev) => ({
        ...prev,
        [symbol]: e instanceof Error ? e.message : String(e),
      }));
    } finally {
      setDetailLoading((prev) => ({ ...prev, [symbol]: false }));
    }
  }

  async function onToggleExpand(key: string, symbol: string) {
    setExpanded((prev) => {
      const nextOpen = !prev[key];
      if (nextOpen) {
        void loadDetail(symbol);
      }
      return { ...prev, [key]: nextOpen };
    });
  }

  async function onAdvance() {
    setAdvancing(true);
    setAdvanceNote(null);
    try {
      const result = await advanceDynamicCandidates({ skip_oos: false });
      const run = result.run_summary ?? {
        health_ready: 0,
        backtests_started: 0,
        oos_started: 0,
        rejected: 0,
        advanced: 0,
        errors: 0,
      };
      const registry = result.registry_summary ?? {};
      setLastRunSummary({ run, registry });
      setAdvanceNote(
        `This run transitions only — ready=${run.health_ready}, backtests=${run.backtests_started}, ` +
          `oos=${run.oos_started}, advanced=${run.advanced}, rejected=${run.rejected}, ` +
          `errors=${run.errors}`
      );
      if (result.candidates) {
        setData({
          status: result.status,
          strategy_id: result.strategy_id,
          candidates: result.candidates,
          count: result.candidates.length,
          disclaimer: result.disclaimer,
          read_only: true,
          v1_unchanged: true,
        });
      } else {
        await reload();
      }
    } catch (e) {
      setAdvanceNote(e instanceof Error ? e.message : String(e));
      setLastRunSummary(null);
    } finally {
      setAdvancing(false);
    }
  }

  const rows: DynamicCandidateRow[] = data?.candidates || [];
  const experimentalCount =
    lastRunSummary?.registry.experimental_paper_candidates ??
    lastRunSummary?.registry.paper_validating ??
    rows.filter((r) => r.operational_state === "PAPER_VALIDATING").length;
  const productionCount =
    lastRunSummary?.registry.production_approved ??
    rows.filter((r) => r.production_approved).length;

  return (
    <section className="mt-8 border-t border-terminal-border pt-6">
      <div className="text-[11px] uppercase tracking-[0.18em] text-terminal-muted">
        v2 research / paper framework · v1 unchanged
      </div>
      <div className="mt-1 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-xl text-terminal-text">
            Dynamic Candidate Pipeline
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-terminal-muted">
            Screener-discovered symbols must pass data-health, frozen COMBO_02 research,
            OOS, and operator approval before experimental paper. Never Telegram-eligible.
            Never COMBO_02 v1. Never production via this panel.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void onAdvance()}
          disabled={advancing}
          className="rounded border border-terminal-border bg-white/5 px-3 py-2 text-sm text-terminal-text hover:bg-white/10 disabled:opacity-50"
        >
          {advancing ? "Running research…" : "Run research advance"}
        </button>
      </div>

      {advanceNote && (
        <p className="mt-3 text-sm text-terminal-muted">{advanceNote}</p>
      )}

      <div className="mt-3 flex flex-wrap gap-4 text-sm text-terminal-text">
        <span>Production-approved candidates: {productionCount}</span>
        <span>Experimental-paper candidates: {experimentalCount}</span>
      </div>

      {lastRunSummary && (
        <div
          className="mt-3 grid gap-3 text-xs text-terminal-muted sm:grid-cols-2"
          title="Current run counts only transitions from this action. Registry totals counts all persisted candidate records."
        >
          <div className="rounded border border-terminal-border/70 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-terminal-muted">
              This run
            </div>
            <p className="mt-1 font-mono text-terminal-text">
              health_ready={lastRunSummary.run.health_ready ?? 0} ·
              backtests_started={lastRunSummary.run.backtests_started ?? 0} ·
              oos_started={lastRunSummary.run.oos_started ?? 0} ·
              advanced={lastRunSummary.run.advanced ?? 0} ·
              rejected={lastRunSummary.run.rejected ?? 0} ·
              errors={lastRunSummary.run.errors ?? 0}
            </p>
            <p className="mt-1 text-[10px]">
              Current run counts only transitions from this action.
            </p>
          </div>
          <div className="rounded border border-terminal-border/70 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-terminal-muted">
              Registry totals
            </div>
            <p className="mt-1 font-mono text-terminal-text">
              discovered={lastRunSummary.registry.discovered ?? 0} ·
              data_pending={lastRunSummary.registry.data_pending ?? 0} ·
              data_ready={lastRunSummary.registry.data_ready ?? 0} ·
              backtests_completed={lastRunSummary.registry.backtest_completed ?? 0} ·
              research_rejected={lastRunSummary.registry.research_rejected ?? 0} ·
              oos_failed={lastRunSummary.registry.oos_failed ?? 0} ·
              v2_paper_candidate={lastRunSummary.registry.v2_paper_candidate ?? 0} ·
              experimental_paper={lastRunSummary.registry.experimental_paper_candidates ?? lastRunSummary.registry.paper_validating ?? 0} ·
              production_approved={lastRunSummary.registry.production_approved ?? 0} ·
              suspended={lastRunSummary.registry.suspended ?? 0}
            </p>
            <p className="mt-1 text-[10px]">
              Registry totals counts all persisted candidate records.
            </p>
          </div>
        </div>
      )}

      {loading && (
        <p className="mt-4 text-sm text-terminal-muted">Loading registry…</p>
      )}
      {error && (
        <p className="mt-4 text-sm text-red-300">Failed to load: {error}</p>
      )}
      {!loading && !error && rows.length === 0 && (
        <p className="mt-4 text-sm text-terminal-muted">
          No dynamic candidates registered yet. Run discovery from the backend script
          first.
        </p>
      )}

      {rows.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[1400px] border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-terminal-border text-[11px] uppercase tracking-wider text-terminal-muted">
                <th className="px-2 py-2">Symbol</th>
                <th className="px-2 py-2">Research tier</th>
                <th className="px-2 py-2">Operational state</th>
                <th className="px-2 py-2">1h / 4h health</th>
                <th className="px-2 py-2">Base n</th>
                <th className="px-2 py-2">Base avg R</th>
                <th className="px-2 py-2">Base PF</th>
                <th className="px-2 py-2">OOS n</th>
                <th className="px-2 py-2">OOS avg R</th>
                <th className="px-2 py-2">OOS status</th>
                <th className="px-2 py-2">Portfolio</th>
                <th className="px-2 py-2">Paper risk</th>
                <th className="px-2 py-2">Production</th>
                <th className="px-2 py-2">Telegram</th>
                <th className="px-2 py-2">Updated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const key = r.id || r.symbol;
                const open = Boolean(expanded[key]);
                const detail = detailBySymbol[r.symbol] || r;
                return (
                  <Fragment key={key}>
                    <tr
                      className="border-b border-terminal-border/60 align-top cursor-pointer hover:bg-white/[0.02]"
                      onClick={() => void onToggleExpand(key, r.symbol)}
                    >
                      <td className="px-2 py-2 font-mono text-terminal-text">
                        <span className="text-terminal-muted">{open ? "▼" : "▶"} </span>
                        {r.symbol}
                        <SafetyBadges row={r} />
                      </td>
                      <td className="px-2 py-2">
                        <span
                          className={`inline-block rounded border px-1.5 py-0.5 text-[10px] ${badgeClass(r.badge)}`}
                        >
                          {r.research_tier || r.backtest_tier || r.badge || r.state}
                        </span>
                      </td>
                      <td className="px-2 py-2 text-terminal-muted">
                        {operationalStateLabel(r.operational_state)}
                        <div className="text-[11px]">
                          {paperApprovalLabel(r.operator_paper_approval)}
                        </div>
                      </td>
                      <td className="px-2 py-2 text-terminal-muted">
                        1h {num((r.ohlcv_1h_completeness ?? 0) * 100, 1)}%
                        <br />
                        4h {num((r.ohlcv_4h_completeness ?? 0) * 100, 1)}%
                      </td>
                      <td className="px-2 py-2 font-mono text-terminal-muted">
                        {r.backtest_trade_count ?? "—"}
                      </td>
                      <td className="px-2 py-2 font-mono text-terminal-muted">
                        {num(r.backtest_net_avg_r)}
                      </td>
                      <td className="px-2 py-2 font-mono text-terminal-muted">
                        {num(r.backtest_profit_factor)}
                      </td>
                      <td className="px-2 py-2 font-mono text-terminal-muted">
                        {r.oos_trade_count ?? "—"}
                      </td>
                      <td className="px-2 py-2 font-mono text-terminal-muted">
                        {num(r.oos_net_avg_r)}
                      </td>
                      <td className="px-2 py-2 text-terminal-muted">
                        {oosStatusLabel(r)}
                      </td>
                      <td className="px-2 py-2 text-terminal-muted">
                        {r.portfolio_status || "—"}
                      </td>
                      <td className="px-2 py-2 font-mono text-terminal-muted">
                        {pctRisk(r.risk_percent)}
                      </td>
                      <td className="px-2 py-2 text-terminal-muted">
                        {productionApprovalLabel(
                          r.production_approval,
                          r.production_approved
                        )}
                      </td>
                      <td className="px-2 py-2 text-terminal-muted">
                        {telegramLabel(r.telegram_eligibility, r.telegram_eligible)}
                      </td>
                      <td className="px-2 py-2 text-[11px] text-terminal-muted">
                        {r.updated_at_utc || r.state_updated_at_utc || "—"}
                      </td>
                    </tr>
                    {open ? (
                      <tr className="border-b border-terminal-border/40">
                        <td colSpan={15} className="bg-black/20 px-3 py-2">
                          {detailLoading[r.symbol] ? (
                            <p className="text-[11px] text-terminal-muted">
                              Loading persisted detail…
                            </p>
                          ) : null}
                          {detailError[r.symbol] ? (
                            <p className="text-[11px] text-red-300">
                              Detail fetch failed: {detailError[r.symbol]}
                            </p>
                          ) : null}
                          <CandidateDetail row={detail} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-[11px] text-terminal-muted">
        {data?.disclaimer ||
          "Frozen COMBO_02 v1 remains BTC/ETH/SOL only. No Enable-for-v1 control."}
      </p>
    </section>
  );
}
