import { useEffect, useState } from "react";
import {
  fetchOhlcvStructureExperiment,
  type StructureExperimentExample,
  type StructureExperimentMetrics,
  type StructureExperimentReport,
} from "../api/client";

const VARIANT_LABELS: Record<string, string> = {
  A_corrected_baseline: "A · Next-bar baseline",
  B_acceptance: "B · Acceptance close",
  C_retest: "C · Three-bar retest",
  D_faster_confirmation: "D · 3-left / 1-right",
};

function pct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function num(value: number | null | undefined, digits = 3): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function MetricTable({
  rows,
}: {
  rows: Array<{ id: string; metrics: StructureExperimentMetrics; extra?: string }>;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-left font-mono text-[11px]">
        <thead className="text-terminal-muted">
          <tr>
            {[
              "Variant",
              "Candidates",
              "Fills",
              "Skips",
              "Expired",
              "Net E[R]",
              "Win rate",
              "Stop",
              "Target",
              "Timeout",
              "Level recross",
              "Post-entry recross",
            ].map((label) => (
              <th key={label} className="border-b border-terminal-border px-2 py-1 font-medium">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-terminal-border/60">
              <td className="px-2 py-1 text-terminal-text">
                {VARIANT_LABELS[row.id] ?? row.id}
                {row.extra ? <div className="text-terminal-muted">{row.extra}</div> : null}
              </td>
              <td className="px-2 py-1">{row.metrics.candidates}</td>
              <td className="px-2 py-1">{row.metrics.fills}</td>
              <td className="px-2 py-1">{row.metrics.skips}</td>
              <td className="px-2 py-1">{row.metrics.expirations}</td>
              <td className="px-2 py-1">{num(row.metrics.net_expectancy_r)}</td>
              <td className="px-2 py-1">{pct(row.metrics.win_rate)}</td>
              <td className="px-2 py-1">{pct(row.metrics.stop_pct)}</td>
              <td className="px-2 py-1">{pct(row.metrics.target_pct)}</td>
              <td className="px-2 py-1">{pct(row.metrics.timeout_pct)}</td>
              <td className="px-2 py-1">
                {pct(row.metrics.close_back_10_pct)}
                <span className="text-terminal-muted"> / {row.metrics.close_back_10_denominator}</span>
              </td>
              <td className="px-2 py-1">
                {pct(row.metrics.post_entry_close_back_10_pct)}
                <span className="text-terminal-muted">
                  {" "}
                  / {row.metrics.post_entry_close_back_10_denominator}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StructureExperimentPanel() {
  const [report, setReport] = useState<StructureExperimentReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<StructureExperimentExample | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchOhlcvStructureExperiment()
      .then((payload) => {
        if (!controller.signal.aborted) setReport(payload);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Request failed");
        }
      });
    return () => controller.abort();
  }, []);

  if (error) {
    return <p className="text-xs text-rose-200">Structure experiment unavailable: {error}</p>;
  }
  if (!report) {
    return <p className="text-xs text-terminal-muted">Loading structure experiment…</p>;
  }
  if (report.status !== "ready" || !report.variants) {
    return (
      <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
        <div className="font-semibold">Research data unavailable</div>
        <p className="mt-1">
          {report.reason || "No saved historical evaluation is on disk. Nothing below is a synthetic result."}
        </p>
      </div>
    );
  }

  const assumptions = report.assumptions || {};
  return (
    <div className="space-y-4 text-xs text-terminal-text">
      <div className="flex flex-wrap gap-2">
        <span className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase text-amber-100">
          Research only
        </span>
        <span className="rounded border border-terminal-border px-2 py-0.5 text-[10px] uppercase text-terminal-muted">
          {report.validation_status.replace(/_/g, " ")}
        </span>
      </div>
      <p className="max-w-3xl text-terminal-muted">
        Level recross counts a close back through the broken level. It is not a trade loss.
        Trade outcomes are stop, target, or the 20-bar timeout, after slippage and fees.
        {String(assumptions.funding_label || "")}
      </p>
      {report.legacy_reference ? (
        <p className="max-w-3xl text-terminal-muted">{report.legacy_reference.label}</p>
      ) : null}

      {(["development", "later_slice"] as const).map((windowId) => (
        <section key={windowId}>
          <h3 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
            {windowId === "development"
              ? "Development window — already inspected, not a final test"
              : "Later slice — still inside the inspected sample"}
          </h3>
          <MetricTable
            rows={report.variants!.map((variant) => ({
              id: variant.id,
              metrics: variant.windows[windowId],
              extra: `${variant.event_count} events · confirm-to-break median ${
                variant.median_bars_confirmation_to_break ?? "—"
              } bars`,
            }))}
          />
        </section>
      ))}

      <section>
        <h3 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Setup timing
        </h3>
        <div className="flex flex-wrap gap-1">
          {(report.examples || []).slice(0, 8).map((example) => (
            <button
              key={`${example.variant}:${example.event_id}`}
              type="button"
              onClick={() => setSelected(example)}
              className="rounded border border-terminal-border px-2 py-1 font-mono text-[10px] text-terminal-muted hover:text-terminal-text"
            >
              {example.status}
            </button>
          ))}
        </div>
        {selected ? (
          <dl className="mt-2 grid gap-1 font-mono text-[11px] text-terminal-muted sm:grid-cols-2">
            <div>Status: {selected.status} · provisional swing is not tradable</div>
            <div>Call: {selected.call_type} · {selected.direction}</div>
            <div>Pivot: {selected.pivot_at}</div>
            <div>Confirmed: {selected.confirmed_at}</div>
            <div>Break: {selected.break_at}</div>
            <div>Qualified: {selected.qualified_at || "—"}</div>
            <div>Entry: {selected.entry_at || "—"}</div>
            <div>Invalidated: {selected.invalidated_at || "—"}</div>
            <div>Expired: {selected.expired_at || "—"}</div>
            <div>
              Delay {selected.confirmation_delay_bars} bars, then{" "}
              {selected.bars_from_confirmation_to_break} bars until the break
            </div>
            <div>Outcome: {selected.outcome || "—"} {selected.ambiguous ? "(intrabar order ambiguous)" : ""}</div>
            <div>Net R: {num(selected.net_r)}</div>
          </dl>
        ) : null}
      </section>

      <section className="font-mono text-[10px] text-terminal-muted">
        <div>Entry: {String(assumptions.entry || "")}</div>
        <div>Hold: {String(assumptions.hold_window || "")}</div>
        <div>Costs: slippage {String(assumptions.slippage_rate ?? "—")}, fee/side {String(assumptions.fee_rate_per_side ?? "—")}</div>
        <div>Stop: {String(assumptions.stop_policy || "")}</div>
        <div>Retest equality: {String(assumptions.retest_equality || "")}</div>
        <div>Windows: {String(assumptions.windows || "")}</div>
      </section>
    </div>
  );
}
