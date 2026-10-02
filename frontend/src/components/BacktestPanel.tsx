import { useCallback, useMemo, useState } from "react";
import {
  fetchLongStrategyBacktest,
  type StrategyMatrixResponse,
  type StrategyMatrixRow,
} from "../api/client";

const SYMBOL_OPTIONS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;
const TF_OPTIONS = ["15m", "1h", "4h"] as const;
const LOOKBACKS = [
  { id: "12d", label: "~12 days", limit: 1200, hint: "15m ≈ 12.5d · 1h ≈ 50d" },
  { id: "30d", label: "~30 days", limit: 2880, hint: "15m ≈ 30d · 1h ≈ 120d" },
  { id: "60d", label: "~60 days", limit: 5760, hint: "15m ≈ 60d · 1h ≈ 240d" },
  { id: "90d", label: "~90 days", limit: 8640, hint: "15m ≈ 90d" },
  { id: "max", label: "Max (~127d 15m)", limit: 12200, hint: "Uses full DB tail" },
] as const;

function pct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function money(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${v.toFixed(2)}`;
}

function Sparkline({ values }: { values: number[] }) {
  if (!values.length) {
    return <span className="text-terminal-muted">—</span>;
  }
  const w = 120;
  const h = 28;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => {
      const x = (i / Math.max(1, values.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 4) - 2;
      return `${x},${y}`;
    })
    .join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-7 w-[120px]">
      <polyline
        fill="none"
        stroke={up ? "#3ecf8e" : "#f07178"}
        strokeWidth="1.5"
        points={pts}
      />
    </svg>
  );
}

function toggleInList<T extends string>(list: T[], value: T): T[] {
  return list.includes(value)
    ? list.filter((x) => x !== value)
    : [...list, value];
}

export function BacktestPanel() {
  const [symbols, setSymbols] = useState<string[]>(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
  const [timeframes, setTimeframes] = useState<string[]>(["15m", "1h"]);
  const [direction, setDirection] = useState<"LONG" | "SHORT">("LONG");
  const [lookbackId, setLookbackId] = useState<(typeof LOOKBACKS)[number]["id"]>("60d");
  const [customLimit, setCustomLimit] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [riskUsd, setRiskUsd] = useState(20);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StrategyMatrixResponse | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const limit = useMemo(() => {
    const custom = Number(customLimit);
    if (customLimit && Number.isFinite(custom) && custom >= 50) {
      return Math.min(Math.floor(custom), 20000);
    }
    return LOOKBACKS.find((l) => l.id === lookbackId)?.limit ?? 5760;
  }, [customLimit, lookbackId]);

  const run = useCallback(async () => {
    if (!symbols.length || !timeframes.length) {
      setError("Pick at least one symbol and timeframe");
      return;
    }
    setLoading(true);
    setError(null);
    setSelectedKey(null);
    try {
      const payload = await fetchLongStrategyBacktest({
        symbols,
        timeframes,
        direction,
        combination_id: "COMBO_02",
        limit,
        risk_usd: riskUsd,
        start_date: start || undefined,
        end_date: end || undefined,
      });
      if (payload.status === "NOT_FOUND" || payload.status === "ERROR") {
        setError(payload.reason || payload.status);
        setResult(null);
        return;
      }
      setResult(payload);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Backtest failed");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [symbols, timeframes, direction, limit, riskUsd, start, end]);

  const rows = result?.rows ?? [];
  const selected = rows.find(
    (r) => `${r.symbol}:${r.timeframe}` === selectedKey
  );
  const pooled = useMemo(() => {
    let n = 0;
    let sumR = 0;
    let pnl = 0;
    for (const r of rows) {
      const s = Number(r.sample_size || 0);
      if (!s || r.average_R == null) continue;
      n += s;
      sumR += Number(r.average_R) * s;
      pnl += Number(r.pnl_usd || 0);
    }
    return {
      n,
      avgR: n ? sumR / n : null,
      pnl,
    };
  }, [rows]);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto p-4">
      <div className="mb-4">
        <h1 className="font-display text-lg font-semibold text-terminal-text">
          Strategy Backtest
        </h1>
        <p className="mt-1 max-w-3xl text-xs text-terminal-muted">
          Run the HL Long Path A playbook (Trend + BOS / COMBO_02) on real Postgres
          OHLCV. Same engine as the research scripts — not a profitability claim.
        </p>
      </div>

      <section className="mb-4 rounded border border-terminal-border/80 p-3">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded border border-terminal-accent/40 bg-terminal-accent/10 px-2 py-1 font-mono text-terminal-accent">
            COMBO_02 · TREND_BOS
          </span>
          <span className="text-terminal-muted">
            {direction === "LONG"
              ? "Structure filter: HH + HL (longs only)"
              : "Structure filter: LH + LL (shorts)"}
          </span>
        </div>

        <div className="grid gap-3 term-md:grid-cols-2 term-lg:grid-cols-4">
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Symbols
            </div>
            <div className="flex flex-wrap gap-1">
              {SYMBOL_OPTIONS.map((s) => {
                const on = symbols.includes(s);
                return (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setSymbols((prev) => toggleInList(prev, s))}
                    className={`rounded border px-2 py-1 font-mono text-xs ${
                      on
                        ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                        : "border-terminal-border text-terminal-muted"
                    }`}
                  >
                    {s.replace("USDT", "")}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Timeframes
            </div>
            <div className="flex flex-wrap gap-1">
              {TF_OPTIONS.map((tf) => {
                const on = timeframes.includes(tf);
                return (
                  <button
                    key={tf}
                    type="button"
                    onClick={() => setTimeframes((prev) => toggleInList(prev, tf))}
                    className={`rounded border px-2 py-1 font-mono text-xs ${
                      on
                        ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                        : "border-terminal-border text-terminal-muted"
                    }`}
                  >
                    {tf}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
              Direction
            </div>
            <div className="flex flex-wrap gap-1">
              {(["LONG", "SHORT"] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setDirection(d)}
                  className={`rounded border px-2 py-1 font-mono text-xs ${
                    direction === d
                      ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                      : "border-terminal-border text-terminal-muted"
                  }`}
                >
                  {d}
                </button>
              ))}
            </div>
          </div>

          <label className="text-xs">
            <span className="mb-1 block text-terminal-muted">Risk $ per trade (1R)</span>
            <input
              type="number"
              min={1}
              step={1}
              value={riskUsd}
              onChange={(e) => setRiskUsd(Number(e.target.value) || 20)}
              className="w-full rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
            />
          </label>
        </div>

        <div className="mt-3">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
            Lookback (bars from DB tail)
          </div>
          <div className="flex flex-wrap gap-1">
            {LOOKBACKS.map((l) => (
              <button
                key={l.id}
                type="button"
                title={l.hint}
                onClick={() => {
                  setLookbackId(l.id);
                  setCustomLimit("");
                }}
                className={`rounded border px-2 py-1 text-xs ${
                  !customLimit && lookbackId === l.id
                    ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                    : "border-terminal-border text-terminal-muted"
                }`}
              >
                {l.label}
              </button>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap items-end gap-3">
            <label className="text-xs">
              <span className="mb-1 block text-terminal-muted">Custom bars (optional)</span>
              <input
                value={customLimit}
                onChange={(e) => setCustomLimit(e.target.value.replace(/[^\d]/g, ""))}
                placeholder={String(limit)}
                className="w-36 rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
              />
            </label>
            <label className="text-xs">
              <span className="mb-1 block text-terminal-muted">Start day (UTC, optional)</span>
              <input
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
                className="rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
              />
            </label>
            <label className="text-xs">
              <span className="mb-1 block text-terminal-muted">End day (UTC, optional)</span>
              <input
                type="date"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
                className="rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none"
              />
            </label>
            <button
              type="button"
              disabled={loading}
              onClick={() => void run()}
              className="rounded border border-terminal-accent bg-terminal-accent/15 px-4 py-1.5 text-sm text-terminal-accent disabled:opacity-50"
            >
              {loading ? "Running…" : "Run backtest"}
            </button>
          </div>
          <div className="mt-1 font-mono text-[11px] text-terminal-muted">
            Using limit={limit} bars · {symbols.length}×{timeframes.length} cells
          </div>
        </div>
      </section>

      {error ? (
        <div className="mb-3 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      ) : null}

      {result ? (
        <div className="mb-3 grid gap-1 rounded border border-terminal-border/80 bg-black/20 px-3 py-2 font-mono text-[11px] text-terminal-muted term-md:grid-cols-2">
          <div>
            Playbook:{" "}
            <span className="text-terminal-text">{result.playbook || "—"}</span>
          </div>
          <div>
            Elapsed:{" "}
            <span className="text-terminal-text">
              {result.elapsed_seconds != null ? `${result.elapsed_seconds}s` : "—"}
            </span>
          </div>
          <div>
            Pooled n={pooled.n} · avg R={num(pooled.avgR)} · PnL={money(pooled.pnl)}{" "}
            <span className="text-terminal-muted">(@ ${riskUsd}/R)</span>
          </div>
          <div className="text-terminal-muted">{result.disclaimer}</div>
        </div>
      ) : null}

      <div className="min-w-0 overflow-x-auto rounded border border-terminal-border/80">
        <table className="w-full min-w-[880px] border-collapse text-left text-xs">
          <thead className="bg-black/30 text-[11px] uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-2 py-2">Symbol</th>
              <th className="px-2 py-2">TF</th>
              <th className="px-2 py-2">n</th>
              <th className="px-2 py-2">Avg R</th>
              <th className="px-2 py-2">TP1</th>
              <th className="px-2 py-2">SL</th>
              <th className="px-2 py-2">PF</th>
              <th className="px-2 py-2">Max DD R</th>
              <th className="px-2 py-2">PnL</th>
              <th className="px-2 py-2">Equity R</th>
              <th className="px-2 py-2">Period</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={11} className="px-3 py-8 text-center text-terminal-muted">
                  LOADING — running Trend+BOS backtest on Postgres OHLCV…
                </td>
              </tr>
            ) : null}
            {!loading && !rows.length ? (
              <tr>
                <td colSpan={11} className="px-3 py-8 text-center text-terminal-muted">
                  {result
                    ? "NO TRADES — empty sample for selected filters"
                    : "Choose lookback and click Run backtest"}
                </td>
              </tr>
            ) : null}
            {!loading &&
              rows.map((row) => {
                const key = `${row.symbol}:${row.timeframe}`;
                const active = selectedKey === key;
                const avg = row.average_R;
                const avgCls =
                  avg == null
                    ? "text-terminal-muted"
                    : avg >= 0
                      ? "text-emerald-400"
                      : "text-red-400";
                return (
                  <tr
                    key={key}
                    onClick={() => setSelectedKey(key)}
                    className={`cursor-pointer border-t border-terminal-border/50 ${
                      active ? "bg-terminal-accent/10" : "hover:bg-white/[0.03]"
                    }`}
                  >
                    <td className="px-2 py-2 font-mono text-terminal-text">
                      {row.symbol}
                    </td>
                    <td className="px-2 py-2 font-mono">{row.timeframe}</td>
                    <td className="px-2 py-2 font-mono">{row.sample_size}</td>
                    <td className={`px-2 py-2 font-mono ${avgCls}`}>
                      {row.sample_size ? num(avg) : "—"}
                    </td>
                    <td className="px-2 py-2 font-mono">{pct(row.tp1_hit_rate)}</td>
                    <td className="px-2 py-2 font-mono">{pct(row.sl_rate)}</td>
                    <td className="px-2 py-2 font-mono">{num(row.profit_factor)}</td>
                    <td className="px-2 py-2 font-mono">{num(row.max_drawdown_R)}</td>
                    <td
                      className={`px-2 py-2 font-mono ${
                        (row.pnl_usd ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                      }`}
                    >
                      {row.sample_size ? money(row.pnl_usd) : "—"}
                    </td>
                    <td className="px-2 py-2">
                      <Sparkline values={row.equity_curve_r || []} />
                    </td>
                    <td className="px-2 py-2 font-mono text-terminal-muted">
                      {String(row.period_start || "—").slice(0, 10)} →{" "}
                      {String(row.period_end || "—").slice(0, 10)}
                      <div className="text-[10px]">bars={row.bars_loaded ?? "—"}</div>
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {selected ? <SelectedDetail row={selected} /> : null}
    </div>
  );
}

function SelectedDetail({ row }: { row: StrategyMatrixRow }) {
  const rs = row.r_values || [];
  return (
    <section className="mt-4 rounded border border-terminal-border/80 p-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-terminal-text">
          {row.symbol} · {row.timeframe} · {row.direction}
        </h2>
        <span className="font-mono text-[11px] text-terminal-muted">
          {row.structure} · n={row.sample_size}
        </span>
      </div>
      <div className="grid gap-3 term-md:grid-cols-2">
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
            Equity curve (R)
          </div>
          <div className="rounded border border-terminal-border/60 bg-black/20 p-2">
            <SparklineLarge values={row.equity_curve_r || []} />
          </div>
        </div>
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
            R multiples ({rs.length})
          </div>
          <div className="max-h-40 overflow-auto rounded border border-terminal-border/60 bg-black/20 p-2 font-mono text-[11px] text-terminal-muted">
            {rs.length
              ? rs.map((v, i) => (
                  <div key={i} className={v >= 0 ? "text-emerald-400/90" : "text-red-400/90"}>
                    #{i + 1} {v >= 0 ? "+" : ""}
                    {v.toFixed(3)}R
                  </div>
                ))
              : "No closed trades"}
          </div>
        </div>
      </div>
    </section>
  );
}

function SparklineLarge({ values }: { values: number[] }) {
  if (!values.length) {
    return <div className="py-6 text-center text-xs text-terminal-muted">No equity data</div>;
  }
  const w = 420;
  const h = 100;
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const zeroY = h - ((0 - min) / span) * (h - 8) - 4;
  const pts = values
    .map((v, i) => {
      const x = (i / Math.max(1, values.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 8) - 4;
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-24 w-full max-w-lg text-terminal-accent">
      <line
        x1={0}
        x2={w}
        y1={zeroY}
        y2={zeroY}
        stroke="currentColor"
        strokeOpacity={0.25}
        strokeDasharray="4 3"
      />
      <polyline fill="none" stroke="currentColor" strokeWidth="1.5" points={pts} />
    </svg>
  );
}
