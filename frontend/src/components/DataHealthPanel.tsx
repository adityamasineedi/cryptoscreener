import { useEffect, useState } from "react";
import {
  fetchBackfill,
  fetchCoverage,
  fetchProviderHealth,
  fetchSystemStats,
} from "../api/client";

type Coverage = {
  symbols: number;
  ticker: Record<string, number>;
  ohlcv: Record<
    string,
    { covered?: number; live?: number; waiting?: number; total?: number; pct_available?: number }
  >;
  open_interest: Record<string, number>;
  liquidations: Record<string, number>;
  fundamentals: Record<string, Record<string, number>>;
  coverage_goals?: {
    targets?: Record<string, { pct?: number; goal_pct?: number; met?: boolean | null; goal?: string }>;
    all_measured_targets_met?: boolean;
  };
  fundamental_reasons?: Record<string, Record<string, number>>;
  metric_dependencies?: Array<{
    metric: string;
    coverage: string;
    status: string;
    blocking_dependency: string | null;
    missing_count: number;
    note?: string;
  }>;
  screen?: {
    max_screen_symbols?: number;
    note?: string;
  };
};

type Backfill = {
  total_jobs?: number;
  pending?: number;
  running?: number;
  complete?: number;
  failed?: number;
  retry_wait?: number;
  estimated_progress?: {
    pct_complete?: number;
    throughput_jobs_per_min?: number;
    eta_seconds?: number | null;
    eta_note?: string;
  };
  adaptive?: Record<string, unknown>;
};

function PctRow({
  label,
  pct,
  detail,
  rolling,
}: {
  label: string;
  pct: number | string;
  detail?: string;
  rolling?: boolean;
}) {
  const n = typeof pct === "number" ? pct : null;
  const color =
    rolling || n == null
      ? "text-terminal-muted"
      : n >= 95
        ? "text-emerald-300"
        : n >= 70
          ? "text-amber-300"
          : "text-rose-300";
  return (
    <div className="flex items-center justify-between gap-3 py-0.5 font-mono text-xs">
      <span className="text-terminal-muted">{label}</span>
      <span className={color}>
        {rolling ? "rolling" : `${typeof pct === "number" ? pct.toFixed(1) : pct}%`}
        {detail ? <span className="ml-2 text-terminal-muted">{detail}</span> : null}
      </span>
    </div>
  );
}

function ProviderPill({ name, status }: { name: string; status: string }) {
  const color =
    status.includes("HEALTHY") || status === "ok" || status === "CONNECTED" || status === "LIVE"
      ? "text-emerald-300"
      : status.includes("RATE") || status.includes("DEGRADED") || status === "WAITING"
        ? "text-amber-300"
        : status.includes("DISABLED") || status === "disabled"
          ? "text-terminal-muted"
          : "text-rose-300";
  return (
    <div className="flex items-center justify-between gap-2 py-0.5 text-xs">
      <span className="text-terminal-muted">{name}</span>
      <span className={`font-mono ${color}`}>{status}</span>
    </div>
  );
}

export function DataHealthPanel() {
  const [cov, setCov] = useState<Coverage | null>(null);
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [providers, setProviders] = useState<Record<string, unknown> | null>(null);
  const [backfill, setBackfill] = useState<Backfill | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const [c, s, p, b] = await Promise.all([
          fetchCoverage(),
          fetchSystemStats(),
          fetchProviderHealth(),
          fetchBackfill(),
        ]);
        if (!alive) return;
        setCov(c as Coverage);
        setStats(s as unknown as Record<string, unknown>);
        setProviders(p as unknown as Record<string, unknown>);
        setBackfill(b as Backfill);
      } catch {
        /* keep last */
      }
    }
    load();
    const id = window.setInterval(load, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const total = cov?.symbols ?? 0;
  const ohlcv = cov?.ohlcv || {};
  const goals = cov?.coverage_goals?.targets || {};
  const provList = (providers?.providers as Array<{ name: string; status: string }>) || [];
  const liq = (providers?.liquidations as { liquidation_status?: string }) || {};
  const fund = (providers?.fundamentals as { coingecko_cooldown?: boolean }) || {};
  const reasons = cov?.fundamental_reasons || {};

  const providerStatus = (name: string) => {
    const hit = provList.find((p) => p.name === name);
    if (name === "coingecko" && fund.coingecko_cooldown) return "RATE LIMITED";
    return hit?.status || "UNKNOWN";
  };

  const tfPct = (tf: string) => {
    const g = goals[tf];
    if (g?.pct != null) return g.pct;
    return ohlcv[tf]?.pct_available ?? 0;
  };

  const oiAvail =
    (cov?.open_interest?.live ?? 0) +
    (cov?.open_interest?.cached ?? 0) +
    (cov?.open_interest?.stale ?? 0);
  const oiPct = total ? (100 * oiAvail) / total : 0;
  const mcapAvail =
    (cov?.fundamentals?.market_cap?.live ?? 0) +
    (cov?.fundamentals?.market_cap?.cached ?? 0) +
    (cov?.fundamentals?.market_cap?.stale ?? 0);
  const tvlAvail =
    (cov?.fundamentals?.tvl?.live ?? 0) +
    (cov?.fundamentals?.tvl?.cached ?? 0) +
    (cov?.fundamentals?.tvl?.stale ?? 0);

  const ep = backfill?.estimated_progress;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto p-4">
      <h2 className="font-display text-lg font-semibold">System / Data Health</h2>
      <p className="mt-1 text-xs text-terminal-muted">
        Measured coverage only — incomplete targets are never marked complete. LIVE vs HISTORICAL
        fields update at different frequencies.
      </p>
      <div className="mt-3 rounded border border-terminal-border/70 bg-terminal-panel/40 px-3 py-2 font-mono text-xs">
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          <span>
            Symbols (full universe): <span className="text-terminal-text">{total}</span>
          </span>
          <span>
            Screen (display max):{" "}
            <span className="text-terminal-text">
              {cov?.screen?.max_screen_symbols ?? 100} / {total}
            </span>
          </span>
        </div>
        <p className="mt-1 text-[10px] text-terminal-muted">
          {cov?.screen?.note ||
            "Main screener shows ≤100 dynamically selected symbols. This page reports the full backend universe."}
        </p>
      </div>

      <section className="mt-5 grid max-w-4xl gap-6 md:grid-cols-2">
        <div className="rounded border border-terminal-border/80 p-3">
          <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
            OHLCV coverage
          </div>
          <PctRow label="Symbols" pct={total ? 100 : 0} detail={`${total}`} />
          {(["1d", "4h", "1h", "15m", "5m"] as const).map((tf) => (
            <PctRow
              key={tf}
              label={tf.toUpperCase()}
              pct={tfPct(tf)}
              detail={
                goals[tf]?.met === false
                  ? `<95%`
                  : goals[tf]?.met
                    ? "goal met"
                    : undefined
              }
            />
          ))}
          <PctRow label="1M" pct={tfPct("1m")} rolling detail="top-vol + visible" />
          <div className="my-2 border-t border-terminal-border/50" />
          <PctRow label="OI" pct={goals.oi?.pct ?? oiPct} detail={`${oiAvail}/${total}`} />
          <PctRow
            label="Market Cap"
            pct={total ? (100 * mcapAvail) / total : 0}
            detail={`${mcapAvail}/${total}`}
          />
          <PctRow
            label="TVL"
            pct={total ? (100 * tvlAvail) / total : 0}
            detail={`${tvlAvail}/${total}`}
          />
          <div className="mt-2 flex items-center justify-between text-xs">
            <span className="text-terminal-muted">Liquidations</span>
            <span className="font-mono text-amber-300">
              {liq.liquidation_status || "WAITING"}
            </span>
          </div>
          <div className="mt-3 text-[10px] text-terminal-muted">
            Goals met:{" "}
            {cov?.coverage_goals?.all_measured_targets_met ? "YES" : "NO — still converging"}
          </div>
        </div>

        <div className="rounded border border-terminal-border/80 p-3">
          <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
            Backfill / Providers
          </div>
          <div className="mb-2 font-mono text-[11px] text-terminal-muted">
            jobs {backfill?.complete ?? 0}/{backfill?.total_jobs ?? 0} · pending{" "}
            {backfill?.pending ?? "—"} · running {backfill?.running ?? "—"} · failed{" "}
            {backfill?.failed ?? "—"} · retry {backfill?.retry_wait ?? "—"}
          </div>
          <div className="mb-3 font-mono text-[11px]">
            progress {ep?.pct_complete ?? 0}% · {ep?.throughput_jobs_per_min ?? 0}/min
            {ep?.eta_seconds != null ? (
              <span className="text-terminal-muted">
                {" "}
                · ETA ~{Math.round(ep.eta_seconds / 60)}m (measured)
              </span>
            ) : (
              <span className="text-terminal-muted"> · {ep?.eta_note || "no ETA yet"}</span>
            )}
          </div>
          <ProviderPill name="Binance" status={providerStatus("binance_rest")} />
          <ProviderPill name="CoinGecko" status={providerStatus("coingecko")} />
          <ProviderPill name="DefiLlama" status={providerStatus("defillama")} />
          <ProviderPill name="Liquidations" status={liq.liquidation_status || "WAITING"} />
          <ProviderPill
            name="Redis"
            status={String(stats?.redis || "disabled").toUpperCase() === "OK" ? "CONNECTED" : "DISABLED"}
          />
          <ProviderPill
            name="PostgreSQL"
            status={
              String(stats?.database || "disabled").toUpperCase() === "OK" ? "CONNECTED" : "DISABLED"
            }
          />
          {reasons.market_cap ? (
            <div className="mt-3 border-t border-terminal-border/60 pt-2 font-mono text-[10px] text-terminal-muted">
              mcap: covered {reasons.market_cap.covered ?? 0} · not covered{" "}
              {reasons.market_cap.asset_not_covered ?? 0} · rate limited{" "}
              {reasons.market_cap.rate_limited ?? 0}
            </div>
          ) : null}
          <div className="mt-2 font-mono text-[11px] text-terminal-muted">
            streams: {String(stats?.active_streams ?? "—")} · ws:{" "}
            {String(stats?.websocket_connections ?? "—")} · rest/min:{" "}
            {String(stats?.rest_requests_last_minute ?? "—")} · 429:{" "}
            {String(stats?.rate_limit_errors ?? "—")}
          </div>
        </div>
      </section>

      {(cov?.metric_dependencies?.length ?? 0) > 0 ? (
        <section className="mt-5 max-w-4xl rounded border border-terminal-border/80 p-3">
          <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
            Metric dependency diagnostics
          </div>
          <p className="mb-3 text-[10px] text-terminal-muted">
            Derived WAITING is explained by upstream blocks — never zero-filled or substituted.
          </p>
          <div className="space-y-1.5">
            {(cov?.metric_dependencies || []).map((d) => (
              <div
                key={d.metric}
                className="grid grid-cols-[7rem_4.5rem_minmax(0,1fr)_3.5rem] items-center gap-2 font-mono text-[11px]"
              >
                <span className="truncate text-terminal-text">{d.metric}</span>
                <span
                  className={
                    d.status === "READY" ? "text-emerald-300" : "text-amber-300"
                  }
                >
                  {d.coverage}
                </span>
                <span className="min-w-0 truncate text-terminal-muted">
                  {d.blocking_dependency
                    ? `blocked by ${d.blocking_dependency}`
                    : d.note || d.status}
                </span>
                <span className="text-right text-terminal-muted">{d.missing_count}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
