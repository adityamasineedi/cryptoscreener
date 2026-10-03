import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchBosStrategies,
  fetchBosStrategiesCatalog,
  fetchBosStrategiesCoverage,
  type BosStrategiesResponse,
} from "../api/client";

type LoadState = "IDLE" | "LOADING" | "SUCCESS" | "ERROR";

function pct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function MetricCell({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-[5.5rem]">
      <div className="text-[10px] uppercase tracking-wide text-terminal-muted">{label}</div>
      <div className="font-mono text-sm text-terminal-text">{value}</div>
    </div>
  );
}

function StrategyCard({
  row,
}: {
  row: Record<string, unknown>;
}) {
  const [open, setOpen] = useState(false);
  const metrics = (row.metrics as Record<string, unknown> | undefined) || row;
  const long = (row.long as Record<string, unknown>) || {};
  const short = (row.short as Record<string, unknown>) || {};
  const byYear = (row.by_year as Record<string, Record<string, unknown>>) || {};
  const bySymbol = (row.by_symbol as Record<string, Record<string, unknown>>) || {};
  const byHtf = (row.by_htf_alignment as Record<string, Record<string, unknown>>) || {};
  const sampleStatus = String(metrics.sample_status || "OK");

  return (
    <div className="rounded border border-terminal-border/70 bg-black/20 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
            {String(row.strategy_id)} · Historical Result
          </div>
          <div className="font-display text-base text-terminal-text">
            {String(row.strategy_name || row.strategy_id)}
          </div>
          {sampleStatus === "INSUFFICIENT_SAMPLE" && (
            <div className="mt-1 text-xs text-amber-400">Insufficient Sample</div>
          )}
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="rounded border border-terminal-border px-2 py-1 text-xs text-terminal-muted hover:text-terminal-text"
        >
          {open ? "Collapse" : "Expand"}
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-4">
        <MetricCell label="Sample" value={String(metrics.sample_size ?? row.sample_size ?? 0)} />
        <MetricCell label="Win Rate" value={pct(metrics.win_rate as number | null)} />
        <MetricCell label="Expectancy R" value={num(metrics.expectancy_R as number | null)} />
        <MetricCell label="PF" value={num(metrics.profit_factor as number | null)} />
        <MetricCell label="Max DD" value={num(metrics.max_drawdown_R as number | null)} />
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <div className="rounded border border-terminal-border/40 p-2">
          <div className="mb-1 text-[10px] uppercase text-terminal-muted">LONG</div>
          <div className="flex flex-wrap gap-3 text-xs font-mono">
            <span>n={String(long.sample_size ?? 0)}</span>
            <span>WR {pct(long.win_rate as number | null)}</span>
            <span>E {num(long.expectancy_R as number | null)}</span>
          </div>
        </div>
        <div className="rounded border border-terminal-border/40 p-2">
          <div className="mb-1 text-[10px] uppercase text-terminal-muted">SHORT</div>
          <div className="flex flex-wrap gap-3 text-xs font-mono">
            <span>n={String(short.sample_size ?? 0)}</span>
            <span>WR {pct(short.win_rate as number | null)}</span>
            <span>E {num(short.expectancy_R as number | null)}</span>
          </div>
        </div>
      </div>

      {open && (
        <div className="mt-4 space-y-4 text-xs">
          <section>
            <div className="mb-1 uppercase tracking-wide text-terminal-muted">By Year</div>
            {Object.keys(byYear).length === 0 ? (
              <div className="text-terminal-muted">No yearly rows</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[420px] text-left font-mono">
                  <thead className="text-terminal-muted">
                    <tr>
                      <th className="py-1 pr-2">Year</th>
                      <th className="py-1 pr-2">n</th>
                      <th className="py-1 pr-2">WR</th>
                      <th className="py-1 pr-2">E</th>
                      <th className="py-1 pr-2">PF</th>
                      <th className="py-1">DD</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(byYear).map(([year, y]) => (
                      <tr key={year} className="border-t border-terminal-border/30">
                        <td className="py-1 pr-2">{year}</td>
                        <td className="py-1 pr-2">{String(y.n ?? 0)}</td>
                        <td className="py-1 pr-2">{pct(y.win_rate as number | null)}</td>
                        <td className="py-1 pr-2">{num(y.expectancy as number | null)}</td>
                        <td className="py-1 pr-2">{num(y.profit_factor as number | null)}</td>
                        <td className="py-1">{num(y.max_DD as number | null)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section>
            <div className="mb-1 uppercase tracking-wide text-terminal-muted">
              By HTF Alignment
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
              {["HTF_ALIGNED", "HTF_CONFLICT", "HTF_NEUTRAL_UNAVAILABLE"].map((k) => {
                const h = byHtf[k] || {};
                return (
                  <div key={k} className="rounded border border-terminal-border/40 p-2 font-mono">
                    <div className="text-[10px] text-terminal-muted">{k}</div>
                    <div>n={String(h.n ?? 0)}</div>
                    <div>WR {pct(h.win_rate as number | null)}</div>
                    <div>E {num(h.expectancy as number | null)}</div>
                    <div>PF {num(h.profit_factor as number | null)}</div>
                    <div>DD {num(h.max_DD as number | null)}</div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="grid gap-2 sm:grid-cols-3">
            {(
              [
                ["Train", row.train],
                ["Validation", row.validation],
                ["OOS", row.oos],
              ] as const
            ).map(([label, block]) => {
              const b = (block as Record<string, unknown>) || {};
              return (
                <div key={label} className="rounded border border-terminal-border/40 p-2 font-mono">
                  <div className="text-[10px] uppercase text-terminal-muted">{label}</div>
                  <div>n={String(b.sample_size ?? 0)}</div>
                  <div>WR {pct(b.win_rate as number | null)}</div>
                  <div>E {num(b.expectancy_R as number | null)}</div>
                  <div>PF {num(b.profit_factor as number | null)}</div>
                </div>
              );
            })}
          </section>

          <section>
            <div className="mb-1 uppercase tracking-wide text-terminal-muted">
              By Symbol (min sample enforced)
            </div>
            <div className="max-h-40 overflow-y-auto font-mono">
              {Object.entries(bySymbol)
                .slice(0, 40)
                .map(([sym, s]) => (
                  <div key={sym} className="flex justify-between border-t border-terminal-border/20 py-0.5">
                    <span>{sym}</span>
                    <span>
                      n={String(s.sample_size ?? 0)} · E {num(s.expectancy_R as number | null)} ·{" "}
                      {String(s.sample_status || "OK")}
                    </span>
                  </div>
                ))}
              {Object.keys(bySymbol).length === 0 && (
                <div className="text-terminal-muted">No symbol rows</div>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export function BosStrategyResearchPanel() {
  const [state, setState] = useState<LoadState>("IDLE");
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<BosStrategiesResponse | null>(null);
  const [coverage, setCoverage] = useState<Record<string, unknown> | null>(null);
  const [catalog, setCatalog] = useState<Array<Record<string, unknown>>>([]);
  const [symbols, setSymbols] = useState("BTCUSDT,ETHUSDT,SOLUSDT");
  const [startDate, setStartDate] = useState("2023-01-01");
  const [endDate, setEndDate] = useState("");
  const [limit, setLimit] = useState("2000");
  const [maxSymbols, setMaxSymbols] = useState("3");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [cat, cov] = await Promise.all([
          fetchBosStrategiesCatalog(),
          fetchBosStrategiesCoverage(),
        ]);
        if (cancelled) return;
        setCatalog(cat.strategies || []);
        setCoverage(cov as Record<string, unknown>);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const run = useCallback(async (opts?: { latestOnly?: boolean }) => {
    setState("LOADING");
    setError(null);
    try {
      const payload = await fetchBosStrategies({
        symbols: symbols.trim() || undefined,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        limit: limit ? Number(limit) : undefined,
        max_symbols: maxSymbols ? Number(maxSymbols) : undefined,
        latest_only: opts?.latestOnly,
        persist: true,
      });
      setData(payload);
      setState("SUCCESS");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("ERROR");
    }
  }, [symbols, startDate, endDate, limit, maxSymbols]);

  const universe = useMemo(() => {
    const u = (coverage?.universe as Record<string, unknown>) || {};
    return u;
  }, [coverage]);

  const results = (data?.results || []) as Array<Record<string, unknown>>;
  const strategies = results.filter((r) => String(r.strategy_id || "").startsWith("STRATEGY"));
  const controls = results.filter((r) => String(r.strategy_id || "").startsWith("CONTROL"));

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-y-auto p-4 md:p-6">
      <header>
        <div className="text-[11px] uppercase tracking-[0.18em] text-terminal-muted">
          Research only — live engine unchanged
        </div>
        <h1 className="font-display text-2xl text-terminal-text">BOS Strategy Research</h1>
        <p className="mt-1 max-w-3xl text-sm text-terminal-muted">
          Historical Result comparison of predefined BOS strategies and controls.
          No winner ranking. Sample size and Insufficient Sample are shown explicitly.
        </p>
      </header>

      <section className="grid gap-3 rounded border border-terminal-border/70 bg-terminal-panel/40 p-3 md:grid-cols-4">
        <MetricCell
          label="Research period"
          value={String(
            (data?.data_period as Record<string, unknown> | undefined)?.requested_start ||
              startDate ||
              "—"
          )}
        />
        <MetricCell
          label="Eligible symbols"
          value={String(universe.eligible_count ?? "—")}
        />
        <MetricCell
          label="Excluded symbols"
          value={String(universe.excluded_count ?? "—")}
        />
        <MetricCell label="Total trades" value={String(data?.trade_count ?? data?.total_trades ?? "—")} />
      </section>

      <section className="flex flex-wrap items-end gap-3 rounded border border-terminal-border/50 p-3">
        <label className="text-xs">
          <span className="mb-1 block text-terminal-muted">Symbols</span>
          <input
            value={symbols}
            onChange={(e) => setSymbols(e.target.value)}
            className="w-64 rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono text-sm"
          />
        </label>
        <label className="text-xs">
          <span className="mb-1 block text-terminal-muted">Start (UTC)</span>
          <input
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="w-36 rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono text-sm"
          />
        </label>
        <label className="text-xs">
          <span className="mb-1 block text-terminal-muted">End (UTC)</span>
          <input
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            placeholder="current"
            className="w-36 rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono text-sm"
          />
        </label>
        <label className="text-xs">
          <span className="mb-1 block text-terminal-muted">Limit / symbol</span>
          <input
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
            className="w-24 rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono text-sm"
          />
        </label>
        <label className="text-xs">
          <span className="mb-1 block text-terminal-muted">Max symbols</span>
          <input
            value={maxSymbols}
            onChange={(e) => setMaxSymbols(e.target.value)}
            className="w-20 rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono text-sm"
          />
        </label>
        <button
          type="button"
          disabled={state === "LOADING"}
          onClick={() => run()}
          className="rounded border border-terminal-accent/50 bg-terminal-accent/10 px-3 py-1.5 text-sm text-terminal-text hover:bg-terminal-accent/20 disabled:opacity-50"
        >
          {state === "LOADING" ? "Running…" : "Run research"}
        </button>
        <button
          type="button"
          disabled={state === "LOADING"}
          onClick={() => run({ latestOnly: true })}
          className="rounded border border-terminal-border px-3 py-1.5 text-sm text-terminal-muted hover:text-terminal-text disabled:opacity-50"
        >
          Load latest
        </button>
      </section>

      {error && (
        <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {catalog.length > 0 && results.length === 0 && (
        <section className="text-sm text-terminal-muted">
          Catalog loaded ({catalog.length} strategies/controls). Run research to compute Historical Results.
        </section>
      )}

      {strategies.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-display text-lg text-terminal-text">Strategies</h2>
          {strategies.map((row) => (
            <StrategyCard key={String(row.strategy_id)} row={row} />
          ))}
        </section>
      )}

      {controls.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-display text-lg text-terminal-text">Controls</h2>
          {controls.map((row) => (
            <StrategyCard key={String(row.strategy_id)} row={row} />
          ))}
        </section>
      )}

      {data?.condition_contribution && (
        <section className="rounded border border-terminal-border/50 p-3">
          <h2 className="mb-2 font-display text-lg text-terminal-text">
            Condition contribution (observational)
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-xs font-mono">
              <thead className="text-terminal-muted">
                <tr>
                  <th className="py-1 pr-2">Step</th>
                  <th className="py-1 pr-2">n</th>
                  <th className="py-1 pr-2">WR</th>
                  <th className="py-1 pr-2">E</th>
                  <th className="py-1 pr-2">PF</th>
                  <th className="py-1">DD</th>
                </tr>
              </thead>
              <tbody>
                {(data.condition_contribution as Array<Record<string, unknown>>).map((row) => (
                  <tr key={String(row.strategy_id)} className="border-t border-terminal-border/30">
                    <td className="py-1 pr-2">{String(row.strategy_name)}</td>
                    <td className="py-1 pr-2">{String(row.sample_size ?? 0)}</td>
                    <td className="py-1 pr-2">{pct(row.win_rate as number | null)}</td>
                    <td className="py-1 pr-2">{num(row.expectancy as number | null)}</td>
                    <td className="py-1 pr-2">{num(row.profit_factor as number | null)}</td>
                    <td className="py-1">{num(row.max_drawdown_R as number | null)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <footer className="pb-6 text-xs text-terminal-muted">
        Research only — live engine unchanged. Labels are Historical Result / Sample Size /
        Insufficient Sample. No BEST STRATEGY ranking.
      </footer>
    </div>
  );
}
