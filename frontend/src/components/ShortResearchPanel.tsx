/**
 * COMBO_02 SHORT research-only panel.
 * No paper / production / Telegram actions.
 */
import { Fragment, useEffect, useState } from "react";
import {
  fetchShortResearchCandidates,
  runShortResearchCandidates,
  type ShortResearchCandidate,
  type ShortResearchListResponse,
} from "../api/client";

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(Number(v))) return "—";
  return Number(v).toFixed(digits);
}

function stateLabel(state: string | null | undefined): string {
  switch (String(state || "").toUpperCase()) {
    case "SHORT_RESEARCH_CANDIDATE":
    case "PROMISING":
      return "Research candidate";
    case "OOS_FAILED":
      return "OOS failed";
    case "RESEARCH_REJECTED":
      return "Research rejected";
    default:
      return "Research only";
  }
}

function stateClass(state: string | null | undefined): string {
  switch (String(state || "").toUpperCase()) {
    case "SHORT_RESEARCH_CANDIDATE":
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
    case "OOS_FAILED":
      return "border-amber-500/40 bg-amber-500/10 text-amber-200";
    case "RESEARCH_REJECTED":
      return "border-red-500/40 bg-red-500/10 text-red-300";
    default:
      return "border-terminal-border bg-white/5 text-terminal-muted";
  }
}

function CandidateDetails({ row }: { row: ShortResearchCandidate }) {
  const health = (row.data_health || {}) as Record<string, unknown>;
  return (
    <div className="space-y-2 px-2 py-2 text-[11px] text-terminal-muted">
      <div className="rounded border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-amber-100">
        SHORT RESEARCH ONLY · NO PAPER TRADING · NO PRODUCTION · TELEGRAM DISABLED
        {row.historical_disclaimer ? (
          <div className="mt-1 text-[10px] text-amber-200/80">
            {row.historical_disclaimer}
          </div>
        ) : null}
      </div>
      <div>
        <div className="font-medium text-terminal-text">Coverage</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(
            {
              requested_start: health.requested_start,
              requested_end: health.requested_end,
              actual_first: health.coverage_start,
              actual_last: health.coverage_end,
              requested_range_available: health.requested_range_available,
              data_health_status: health.data_health_status ?? health.health_status,
              bars_used: health.bars_used,
              bars_loaded: health.bars_loaded,
              dataset_hash: row.dataset_hash,
              configuration_hash: row.configuration_hash,
              engine_version: row.engine_version,
              research_quality: row.research_quality,
            },
            null,
            2
          )}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Review / blockers</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(
            {
              human_label: row.human_label ?? row.labels?.review,
              review_status: (row.prepaper_review as { review_status?: string } | undefined)
                ?.review_status,
              blockers: row.blockers ?? [],
              hard_gates: (row.prepaper_review as { hard_gates?: unknown } | undefined)
                ?.hard_gates,
            },
            null,
            2
          )}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Sample-size warning</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(row.quality_warnings ?? row.sample_size ?? {}, null, 2)}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Identity</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(
            {
              symbol: row.symbol,
              timeframe: row.timeframe,
              direction: row.direction,
              strategy_id: row.strategy_id,
              combo_version: row.combo_version,
              source: row.source,
              run_id: row.run_id,
              strategy_fingerprint: row.strategy_fingerprint,
            },
            null,
            2
          )}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Execution model / configuration</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(row.execution_model ?? {}, null, 2)}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Data health</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(row.data_health ?? {}, null, 2)}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Canonical research windows</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(
            {
              window_status: row.window_status,
              research_windows: row.research_windows ?? {},
              partition_counts: row.partition_counts ?? {},
            },
            null,
            2
          )}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Base window metrics</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(row.base_research ?? {}, null, 2)}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">OOS development</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(row.oos_development ?? {}, null, 2)}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">OOS validation</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(row.oos_validation ?? {}, null, 2)}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">OOS summary / split</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(
            { oos: row.oos ?? {}, oos_split: row.oos_split ?? {} },
            null,
            2
          )}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Forensic lookahead</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(
            {
              forensic: row.forensic_lookahead ?? {},
              contract_checklist: row.lookahead_audit ?? {},
              trade_audits: row.trade_forensic_audits ?? [],
            },
            null,
            2
          )}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Directional rule checks</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(row.direction_checks ?? [], null, 2)}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Rejection reasons</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(row.rejection_reasons ?? [], null, 2)}
        </pre>
      </div>
      <div>
        <div className="font-medium text-terminal-text">Reconciliation</div>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(row.reconciliation ?? {}, null, 2)}
        </pre>
      </div>
    </div>
  );
}

export function ShortResearchPanel() {
  const [data, setData] = useState<ShortResearchListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    fetchShortResearchCandidates()
      .then((payload) => {
        setData(payload);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    reload();
  }, []);

  const rows = data?.candidates || [];

  return (
    <section className="mt-8 border-t border-terminal-border pt-6">
      <div className="text-[11px] uppercase tracking-[0.18em] text-terminal-muted">
        SHORT RESEARCH ONLY · NO PAPER TRADING · NO PRODUCTION · TELEGRAM DISABLED
      </div>
      <h2 className="mt-1 font-display text-xl text-terminal-text">
        COMBO_02 SHORT Research
      </h2>
      <p className="mt-1 max-w-3xl text-sm text-terminal-muted">
        Research-only bearish COMBO_02 path (1h + 4h HTF). Never opens paper or live
        trades, never Telegram-eligible, never production-approved.
      </p>

      <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
        <span className="rounded border border-terminal-border px-2 py-1">
          Paper disabled
        </span>
        <span className="rounded border border-terminal-border px-2 py-1">
          Production not approved
        </span>
        <span className="rounded border border-terminal-border px-2 py-1">
          Telegram disabled
        </span>
        <button
          type="button"
          className="rounded border border-terminal-border px-2 py-1 text-terminal-text hover:bg-white/5 disabled:opacity-50"
          disabled={running}
          onClick={() => {
            setRunning(true);
            runShortResearchCandidates()
              .then(() => reload())
              .catch((e) => setError(e instanceof Error ? e.message : String(e)))
              .finally(() => setRunning(false));
          }}
        >
          {running ? "Running…" : "Run SHORT research"}
        </button>
      </div>

      {loading && (
        <p className="mt-4 text-sm text-terminal-muted">Loading SHORT research…</p>
      )}
      {error && <p className="mt-4 text-sm text-red-300">{error}</p>}

      {data?.disclaimer && (
        <p className="mt-3 rounded border border-terminal-border bg-white/5 px-3 py-2 text-xs text-terminal-muted">
          {data.disclaimer}
        </p>
      )}

      {data?.batch_summary && (
        <pre className="mt-3 overflow-x-auto rounded border border-terminal-border bg-white/5 px-3 py-2 text-[11px] text-terminal-muted">
          {JSON.stringify(
            {
              run_id: (data.batch_summary as { run_id?: string }).run_id,
              total_symbols: (data.batch_summary as { total_symbols?: number })
                .total_symbols,
              review_ready: (data.batch_summary as { review_ready?: number })
                .review_ready,
              review_blocked: (data.batch_summary as { review_blocked?: number })
                .review_blocked,
              insufficient_oos_sample: (
                data.batch_summary as { insufficient_oos_sample?: number }
              ).insufficient_oos_sample,
              data_failures: (data.batch_summary as { data_failures?: number })
                .data_failures,
              forensic_failures: (
                data.batch_summary as { forensic_failures?: number }
              ).forensic_failures,
              paper_eligible: 0,
              production_approved: 0,
              telegram_eligible: 0,
            },
            null,
            2
          )}
        </pre>
      )}

      {rows.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-left text-xs">
            <thead className="text-terminal-muted">
              <tr>
                <th className="px-2 py-1.5 font-medium">Symbol</th>
                <th className="px-2 py-1.5 font-medium">Review</th>
                <th className="px-2 py-1.5 font-medium">Quality</th>
                <th className="px-2 py-1.5 font-medium">Forensic</th>
                <th className="px-2 py-1.5 font-medium">Data health</th>
                <th className="px-2 py-1.5 font-medium">Base</th>
                <th className="px-2 py-1.5 font-medium">OOS dev</th>
                <th className="px-2 py-1.5 font-medium">OOS val</th>
                <th className="px-2 py-1.5 font-medium">OOS status</th>
                <th className="px-2 py-1.5 font-medium">Recon</th>
                <th className="px-2 py-1.5 font-medium">Blockers</th>
                <th className="px-2 py-1.5 font-medium">Paper</th>
                <th className="px-2 py-1.5 font-medium">Production</th>
                <th className="px-2 py-1.5 font-medium">Telegram</th>
                <th className="px-2 py-1.5 font-medium">Updated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const key = String(r.symbol || "");
                const open = expanded === key;
                return (
                  <Fragment key={key}>
                    <tr
                      className="cursor-pointer border-t border-terminal-border/60 hover:bg-white/[0.03]"
                      onClick={() => setExpanded(open ? null : key)}
                    >
                      <td className="px-2 py-1.5 font-medium text-terminal-text">
                        {r.symbol}
                      </td>
                      <td className="px-2 py-1.5">
                        {String(
                          r.human_label ||
                            (
                              r.prepaper_review as
                                | { review_status?: string }
                                | undefined
                            )?.review_status ||
                            "REVIEW_BLOCKED"
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        {String(r.research_quality || "REVIEW_REQUIRED")}
                      </td>
                      <td className="px-2 py-1.5">
                        {String(
                          (
                            (
                              r.forensic_lookahead as
                                | { evidence_classes?: string[] }
                                | undefined
                            )?.evidence_classes || ["UNKNOWN"]
                          ).join(",")
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        {String(
                          (
                            r.data_health as
                              | { data_health_status?: string; health_status?: string }
                              | undefined
                          )?.data_health_status ||
                            (
                              r.data_health as { health_status?: string } | undefined
                            )?.health_status ||
                            "—"
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        {num(
                          (r.partition_counts as { base_trades?: number } | undefined)
                            ?.base_trades ??
                            (r.base_research as { trade_count?: number } | undefined)
                              ?.trade_count,
                          0
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        {num(
                          (
                            r.partition_counts as { oos_dev_trades?: number } | undefined
                          )?.oos_dev_trades,
                          0
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        {num(
                          (
                            r.partition_counts as { oos_val_trades?: number } | undefined
                          )?.oos_val_trades ??
                            (
                              r.oos_validation as { trade_count?: number } | undefined
                            )?.trade_count,
                          0
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        {String(
                          (r.oos as { oos_status?: string } | undefined)?.oos_status ||
                            "—"
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        {String(
                          (
                            r.reconciliation as
                              | { equity_reconciliation?: string; ok?: boolean }
                              | undefined
                          )?.equity_reconciliation ||
                            (
                              (r.reconciliation as { ok?: boolean } | undefined)?.ok
                                ? "PASS"
                                : "—"
                            )
                        )}
                      </td>
                      <td className="px-2 py-1.5 max-w-[14rem] truncate">
                        {(r.blockers || []).slice(0, 3).join("; ") || "—"}
                      </td>
                      <td className="px-2 py-1.5">false</td>
                      <td className="px-2 py-1.5">false</td>
                      <td className="px-2 py-1.5">false</td>
                      <td className="px-2 py-1.5">
                        {String(r.updated_at || r.created_at || "—").slice(0, 19)}
                      </td>
                    </tr>
                    {open && (
                      <tr>
                        <td colSpan={15}>
                          <CandidateDetails row={r} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && rows.length === 0 && (
        <p className="mt-4 text-sm text-terminal-muted">
          No SHORT research artifacts yet. Use “Run SHORT research” or{" "}
          <code>scripts/run_combo02_short_research.py</code>.
        </p>
      )}
    </section>
  );
}
