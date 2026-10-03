/**
 * Read-only COMBO_02 candidate research panel.
 * No enable/promote/Telegram/paper controls — research labels only.
 */
import { useEffect, useState } from "react";
import {
  fetchCombo02CandidateResults,
  type Combo02CandidateResultsResponse,
} from "../api/client";

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function tierClass(tier: string | null | undefined): string {
  switch (tier) {
    case "PROMISING":
    case "V2_PAPER_CANDIDATE":
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
    case "WATCHLIST":
    case "PROMISING_NEEDS_MORE_EVIDENCE":
      return "border-amber-500/40 bg-amber-500/10 text-amber-200";
    case "REJECT":
      return "border-red-500/40 bg-red-500/10 text-red-300";
    default:
      return "border-terminal-border bg-white/5 text-terminal-muted";
  }
}

export function CandidateResearchPanel() {
  const [data, setData] = useState<Combo02CandidateResultsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchCombo02CandidateResults()
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const rows = data?.results || [];
  const portfolioBySym = new Map(
    (data?.portfolio || []).map((p) => [String(p.symbol || ""), p])
  );

  return (
    <section className="mt-8 border-t border-terminal-border pt-6">
      <div className="text-[11px] uppercase tracking-[0.18em] text-terminal-muted">
        Research only — read-only · v1 unchanged
      </div>
      <h2 className="mt-1 font-display text-xl text-terminal-text">
        COMBO_02 Candidate Research
      </h2>
      <p className="mt-1 max-w-3xl text-sm text-terminal-muted">
        Screener-universe coins evaluated under frozen COMBO_02 LONG 1h + HTF_ALIGNED.
        Labels are research-only. No enable-for-v1, Telegram, or paper-trading actions.
      </p>

      {loading && (
        <p className="mt-4 text-sm text-terminal-muted">Loading latest report…</p>
      )}
      {error && (
        <p className="mt-4 text-sm text-red-300">{error}</p>
      )}
      {!loading && !error && data?.status === "EMPTY" && (
        <p className="mt-4 text-sm text-terminal-muted">
          {data.note || "No candidate research artifacts yet."}
        </p>
      )}

      {data?.disclaimer && (
        <p className="mt-3 rounded border border-terminal-border bg-white/5 px-3 py-2 text-xs text-terminal-muted">
          {data.disclaimer}
        </p>
      )}

      {rows.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-left text-xs">
            <thead className="text-terminal-muted">
              <tr>
                <th className="px-2 py-1.5 font-medium">Symbol</th>
                <th className="px-2 py-1.5 font-medium">Tier</th>
                <th className="px-2 py-1.5 font-medium">Net avg R</th>
                <th className="px-2 py-1.5 font-medium">Net PnL</th>
                <th className="px-2 py-1.5 font-medium">PF</th>
                <th className="px-2 py-1.5 font-medium">Max DD</th>
                <th className="px-2 py-1.5 font-medium">OOS</th>
                <th className="px-2 py-1.5 font-medium">Overlap / corr warning</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const sym = String(r.symbol || "");
                const port = portfolioBySym.get(sym);
                const warn =
                  port?.recommendation === "CORRELATED"
                    ? `CORRELATED — BTC overlap ${
                        port.btc_overlap_pct == null
                          ? "—"
                          : `${(Number(port.btc_overlap_pct) * 100).toFixed(0)}%`
                      }`
                    : port?.recommendation_note ||
                      (r.corr_daily_net_r_vs_btc != null
                        ? `corr BTC ${num(Number(r.corr_daily_net_r_vs_btc), 2)}`
                        : "—");
                const oos = String(r.oos_label || "—");
                const tier = String(r.eligibility_tier || "—");
                return (
                  <tr key={sym} className="border-t border-terminal-border/60">
                    <td className="px-2 py-1.5 font-mono text-terminal-text">{sym}</td>
                    <td className="px-2 py-1.5">
                      <span
                        className={`inline-block rounded border px-1.5 py-0.5 text-[10px] ${tierClass(tier)}`}
                      >
                        {tier}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 font-mono">{num(r.net_avg_r as number)}</td>
                    <td className="px-2 py-1.5 font-mono">{num(r.net_pnl as number)}</td>
                    <td className="px-2 py-1.5 font-mono">{num(r.profit_factor as number)}</td>
                    <td className="px-2 py-1.5 font-mono">{num(r.max_drawdown_r as number)}</td>
                    <td className="px-2 py-1.5">
                      <span
                        className={`inline-block rounded border px-1.5 py-0.5 text-[10px] ${tierClass(oos)}`}
                      >
                        {oos}
                      </span>
                    </td>
                    <td className="max-w-xs px-2 py-1.5 text-terminal-muted">{warn}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {data?.source_file && (
        <p className="mt-3 text-[11px] text-terminal-muted">
          Source: {data.source_file}
          {data.generated_at_utc ? ` · ${data.generated_at_utc}` : ""}
        </p>
      )}
    </section>
  );
}
