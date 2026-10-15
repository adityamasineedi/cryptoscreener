/** Intraday BOS + CHoCH + supply/demand research status.

Actionable alerts exist only while status is SIGNAL_ENABLED and every stored
gate passed. This screen does not place orders.
*/

import { useEffect, useState } from "react";

type Gate = { gate: string; passed: boolean; detail: unknown };
type Signal = {
  symbol?: string;
  side?: string;
  publication?: string;
  actionable?: boolean;
  setup_time?: string;
  stop?: number;
  tier?: string;
  volume_ratio?: number | null;
  reasons?: string[];
  zone_low?: number | null;
  zone_high?: number | null;
  bos_level?: number | null;
  choch_level?: number | null;
  portfolio_approval?: string;
};

type Status = {
  strategy_id: string;
  strategy_version: string;
  spec_hash: string;
  status: string;
  backtest_passed: boolean;
  passed_means: string;
  validation: {
    validated_at?: string;
    coverage?: { start?: string; end?: string };
    scope?: string[];
    tier_kind?: string;
    checks?: Gate[];
    windows?: { holdout?: { status?: string; trades?: number; net_pnl?: number } };
  } | null;
  paper_policy: { forward_validation_passed: boolean; minimum_calendar_days: number };
  signals: Signal[];
  suspension_reason?: string;
};

function num(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(4);
}

export function IntradayBosSdPanel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/research/intraday-bos-sd/status")
      .then((res) => {
        if (!res.ok) throw new Error(`status ${res.status}`);
        return res.json();
      })
      .then((body: Status) => {
        if (!cancelled) setStatus(body);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="space-y-4 p-4 text-terminal-text" data-testid="intraday-bos-sd">
      <header>
        <h1 className="font-display text-xl">Intraday BOS / CHoCH / zones</h1>
        <p className="mt-1 text-sm text-terminal-muted">
          Research and screener status for one frozen strategy version. Passed gates are an
          acceptance policy, not a promise of profit.
        </p>
      </header>
      {error && <p className="text-red-300">{error}</p>}
      {!status && !error && <p className="text-terminal-muted">Loading strategy status…</p>}
      {status && (
        <>
          <div className="grid gap-3 md:grid-cols-4">
            <Stat label="Version" value={status.strategy_version} />
            <Stat label="Status" value={status.status} />
            <Stat label="Backtest passed" value={status.backtest_passed ? "yes" : "no"} />
            <Stat label="Forward validation" value={status.paper_policy.forward_validation_passed ? "passed" : "not passed"} />
          </div>
          <p className="text-xs text-terminal-muted">
            Spec {status.spec_hash.slice(0, 12)} · {status.passed_means}
            {status.suspension_reason ? ` · ${status.suspension_reason}` : ""}
          </p>
          <div className="text-sm text-terminal-muted">
            Coverage {status.validation?.coverage?.start || "—"} → {status.validation?.coverage?.end || "—"}
            {" · "}
            {status.validation?.tier_kind || "scope not validated"}
            {" · holdout "}
            {status.validation?.windows?.holdout?.status || "not run"}
          </div>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-terminal-muted">
                <th>Gate</th>
                <th>Result</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {(status.validation?.checks || []).map((gate) => (
                <tr key={gate.gate} className="border-t border-terminal-border">
                  <td>{gate.gate}</td>
                  <td>{gate.passed ? "pass" : "fail"}</td>
                  <td className="font-mono text-xs">{JSON.stringify(gate.detail)}</td>
                </tr>
              ))}
              {!status.validation?.checks?.length && (
                <tr>
                  <td colSpan={3}>No gate results stored. The strategy is not signal-enabled.</td>
                </tr>
              )}
            </tbody>
          </table>
          <h2 className="text-sm uppercase tracking-wide text-terminal-muted">Candidates and signals</h2>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-terminal-muted">
                <th>Symbol</th>
                <th>Side</th>
                <th>Label</th>
                <th>Setup</th>
                <th>Stop</th>
                <th>Zone</th>
                <th>Volume</th>
                <th>Why blocked</th>
              </tr>
            </thead>
            <tbody>
              {status.signals.map((row) => (
                <tr key={`${row.symbol}-${row.setup_time}`} className="border-t border-terminal-border">
                  <td>{row.symbol}</td>
                  <td>{row.side}</td>
                  <td>{row.actionable ? "actionable signal" : row.publication}</td>
                  <td>{row.setup_time}</td>
                  <td>{num(row.stop)}</td>
                  <td>
                    {num(row.zone_low)} – {num(row.zone_high)}
                  </td>
                  <td>{num(row.volume_ratio)}</td>
                  <td>{(row.reasons || []).join(", ") || row.portfolio_approval}</td>
                </tr>
              ))}
              {!status.signals.length && (
                <tr>
                  <td colSpan={8}>No stored candidates. Historical validation has not authorized alerts.</td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-terminal-border px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-terminal-muted">{label}</div>
      <div className="mt-1 font-mono text-sm">{value}</div>
    </div>
  );
}
