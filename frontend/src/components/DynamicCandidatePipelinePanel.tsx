/**
 * Dynamic Candidate Pipeline panel.
 * Research advance is allowed; no "Enable for v1" / Telegram actions.
 */
import { useCallback, useEffect, useState } from "react";
import {
  advanceDynamicCandidates,
  fetchDynamicCandidates,
  type DynamicCandidateRow,
  type DynamicCandidatesResponse,
} from "../api/client";

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function badgeClass(badge: string | null | undefined): string {
  switch (badge) {
    case "V2 PAPER CANDIDATE":
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
    case "EXPERIMENTAL PAPER":
      return "border-cyan-500/40 bg-cyan-500/10 text-cyan-200";
    case "APPROVED (future, not v1)":
      return "border-violet-500/40 bg-violet-500/10 text-violet-200";
    case "DATA BLOCKED":
    case "SUSPENDED":
      return "border-red-500/40 bg-red-500/10 text-red-300";
    case "DISCOVERY ONLY":
      return "border-sky-500/40 bg-sky-500/10 text-sky-200";
    default:
      return "border-amber-500/40 bg-amber-500/10 text-amber-200";
  }
}

export function DynamicCandidatePipelinePanel() {
  const [data, setData] = useState<DynamicCandidatesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [advancing, setAdvancing] = useState(false);
  const [advanceNote, setAdvanceNote] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    return fetchDynamicCandidates()
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
    let cancelled = false;
    setLoading(true);
    fetchDynamicCandidates()
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

  async function onAdvance() {
    setAdvancing(true);
    setAdvanceNote(null);
    try {
      const result = await advanceDynamicCandidates({ skip_oos: false });
      setAdvanceNote(
        `Advanced: health_ready=${result.health_ready ?? 0}, ` +
          `blocked=${result.health_blocked ?? 0}, ` +
          `backtests=${result.backtests_run ?? 0}, oos=${result.oos_run ?? 0}`
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
    } finally {
      setAdvancing(false);
    }
  }

  const rows: DynamicCandidateRow[] = data?.candidates || [];

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
            Never COMBO_02 v1.
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
          <table className="w-full min-w-[1100px] border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-terminal-border text-[11px] uppercase tracking-wider text-terminal-muted">
                <th className="px-2 py-2">Symbol</th>
                <th className="px-2 py-2">Badge / State</th>
                <th className="px-2 py-2">Liquidity</th>
                <th className="px-2 py-2">1h / 4h health</th>
                <th className="px-2 py-2">Backtest</th>
                <th className="px-2 py-2">OOS</th>
                <th className="px-2 py-2">Portfolio</th>
                <th className="px-2 py-2">Approval</th>
                <th className="px-2 py-2">Risk</th>
                <th className="px-2 py-2">Updated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const overlapWarn =
                  (r.portfolio_overlap_btc != null && r.portfolio_overlap_btc >= 0.5) ||
                  (r.portfolio_overlap_eth != null && r.portfolio_overlap_eth >= 0.5) ||
                  (r.portfolio_overlap_sol != null && r.portfolio_overlap_sol >= 0.5);
                return (
                  <tr
                    key={r.id || r.symbol}
                    className="border-b border-terminal-border/60 align-top"
                  >
                    <td className="px-2 py-2 font-mono text-terminal-text">
                      {r.symbol}
                    </td>
                    <td className="px-2 py-2">
                      <span
                        className={`inline-block rounded border px-1.5 py-0.5 text-[10px] ${badgeClass(r.badge)}`}
                      >
                        {r.badge || r.state}
                      </span>
                      <div className="mt-1 text-[11px] text-terminal-muted">
                        {r.state}
                        {r.state_reason ? ` · ${r.state_reason}` : ""}
                      </div>
                    </td>
                    <td className="px-2 py-2 text-terminal-muted">
                      rank {r.discovery_rank ?? "—"}
                      <div className="text-[11px]">
                        vol {num(r.discovery_volume_usd, 0)}
                      </div>
                    </td>
                    <td className="px-2 py-2 text-terminal-muted">
                      1h {num((r.ohlcv_1h_completeness ?? 0) * 100, 1)}%
                      <br />
                      4h {num((r.ohlcv_4h_completeness ?? 0) * 100, 1)}%
                      {r.data_health_block_reason ? (
                        <div className="mt-1 text-[11px] text-red-300">
                          {r.data_health_block_reason}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-2 py-2 text-terminal-muted">
                      {r.backtest_tier || r.backtest_status || "—"}
                      <div className="text-[11px]">
                        n={r.backtest_trade_count ?? "—"} · avgR{" "}
                        {num(r.backtest_net_avg_r)} · PF{" "}
                        {num(r.backtest_profit_factor)}
                      </div>
                    </td>
                    <td className="px-2 py-2 text-terminal-muted">
                      {r.oos_status || "—"}
                      <div className="text-[11px]">
                        n={r.oos_trade_count ?? "—"} · avgR {num(r.oos_net_avg_r)}
                      </div>
                    </td>
                    <td className="px-2 py-2 text-terminal-muted">
                      BTC {num(r.portfolio_overlap_btc, 2)} · ETH{" "}
                      {num(r.portfolio_overlap_eth, 2)} · SOL{" "}
                      {num(r.portfolio_overlap_sol, 2)}
                      {overlapWarn ? (
                        <div className="mt-1 text-[11px] text-amber-300">
                          High overlap warning
                        </div>
                      ) : null}
                    </td>
                    <td className="px-2 py-2 text-terminal-muted">
                      {r.operator_approved ? "approved" : "not approved"}
                      <div className="text-[11px]">
                        TG {r.telegram_eligible ? "YES" : "false"}
                      </div>
                    </td>
                    <td className="px-2 py-2 font-mono text-terminal-muted">
                      {num((r.risk_percent ?? 0) * 100, 2)}%
                    </td>
                    <td className="px-2 py-2 text-[11px] text-terminal-muted">
                      {r.updated_at_utc || r.state_updated_at_utc || "—"}
                    </td>
                  </tr>
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
