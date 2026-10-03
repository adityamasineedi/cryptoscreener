import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { fetchResearchOhlcvRange, type OhlcvRangeRow } from "../api/client";
import { useOhlcvExpandStore } from "../store/ohlcvExpandStore";

const SYMBOL_OPTIONS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;
const TF_OPTIONS = ["5m", "15m", "1h", "4h", "1d"] as const;
const UNTIL_PRESETS = [
  { id: "2023", until: "2023-01-01", label: "Back to 2023" },
  { id: "2024", until: "2024-01-01", label: "Back to 2024" },
  { id: "2025", until: "2025-01-01", label: "Back to 2025" },
  { id: "2026", until: "2026-01-01", label: "Back to 2026" },
] as const;

function toggleInList<T extends string>(list: T[], value: T): T[] {
  return list.includes(value)
    ? list.filter((x) => x !== value)
    : [...list, value];
}

function parseListParam(raw: string | null, allowed: readonly string[]): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter((s) => allowed.map((a) => a.toUpperCase()).includes(s))
    .map((s) => {
      const hit = allowed.find((a) => a.toUpperCase() === s);
      return hit || s;
    });
}

function parseTfParam(raw: string | null, allowed: readonly string[]): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter((s) => allowed.includes(s));
}

export function OhlcvHistoryPanel() {
  const [searchParams] = useSearchParams();
  const initialSymbols = useMemo(() => {
    const fromUrl = parseListParam(searchParams.get("symbols"), SYMBOL_OPTIONS);
    return fromUrl.length ? fromUrl : ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
  }, [searchParams]);
  const initialTfs = useMemo(() => {
    const fromUrl = parseTfParam(searchParams.get("tfs"), TF_OPTIONS);
    return fromUrl.length ? fromUrl : ["15m", "1h"];
  }, [searchParams]);
  const initialUntil = searchParams.get("until") || "2023-01-01";

  const [symbols, setSymbols] = useState<string[]>(initialSymbols);
  const [timeframes, setTimeframes] = useState<string[]>(initialTfs);
  const [until, setUntil] = useState(initialUntil);
  const [refreshTip, setRefreshTip] = useState(true);
  const [maxPages, setMaxPages] = useState(200);
  const [coverage, setCoverage] = useState<OhlcvRangeRow[]>([]);

  const job = useOhlcvExpandStore((s) => s.job);
  const error = useOhlcvExpandStore((s) => s.error);
  const active = useOhlcvExpandStore((s) => s.active);
  const startJob = useOhlcvExpandStore((s) => s.start);
  const cancelJob = useOhlcvExpandStore((s) => s.cancel);
  const setError = useOhlcvExpandStore((s) => s.setError);

  const running = job?.status === "running" || active;

  const refreshCoverage = useCallback(async () => {
    if (!symbols.length || !timeframes.length) {
      setCoverage([]);
      return;
    }
    try {
      const r = await fetchResearchOhlcvRange({ symbols, timeframes });
      setCoverage(r.rows || []);
    } catch {
      setCoverage([]);
    }
  }, [symbols, timeframes]);

  useEffect(() => {
    void refreshCoverage();
  }, [refreshCoverage]);

  // When a background job finishes, refresh DB coverage on this tab.
  useEffect(() => {
    if (job?.status === "done" || job?.status === "cancelled") {
      void refreshCoverage();
    }
  }, [job?.status, job?.finished_at, refreshCoverage]);

  const start = useCallback(async () => {
    if (!symbols.length || !timeframes.length) {
      setError("Pick at least one symbol and timeframe");
      return;
    }
    if (!until) {
      setError("Pick an Until date (UTC day to walk history back to)");
      return;
    }
    await startJob({
      symbols,
      timeframes,
      until,
      refresh_tip: refreshTip,
      max_pages: maxPages,
    });
  }, [symbols, timeframes, until, refreshTip, maxPages, startJob, setError]);

  /** Keep one decimal under 10% so early page pulls are not stuck showing 0%. */
  const progressPct = Math.min(100, Number(job?.pct ?? 0));
  const progressLabel =
    progressPct > 0 && progressPct < 10
      ? progressPct.toFixed(1)
      : String(Math.round(progressPct));

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4 term-md:p-6">
      <div className="mb-4">
        <div className="font-display text-xl font-semibold text-terminal-text">
          OHLCV History
        </div>
        <div className="mt-1 text-sm text-terminal-muted">
          Fetch Binance Futures candles for the selected symbols/timeframes and store them in
          Postgres. Jobs keep running if you switch pages — progress stays in the top banner.
        </div>
        <div className="mt-1 text-[11px] text-terminal-muted">
          Real exchange data only · same engine as{" "}
          <code className="font-mono">scripts/expand_ohlcv_history.py</code>
          {" · "}
          <Link to="/backtest" className="text-terminal-accent hover:underline">
            Back to Backtest
          </Link>
        </div>
      </div>

      <div className="rounded border border-terminal-border bg-terminal-panel/40 p-4">
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
                    disabled={running}
                    onClick={() => setSymbols((prev) => toggleInList(prev, s))}
                    className={`rounded border px-2 py-1 font-mono text-xs disabled:opacity-50 ${
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
                    disabled={running}
                    onClick={() => setTimeframes((prev) => toggleInList(prev, tf))}
                    className={`rounded border px-2 py-1 font-mono text-xs disabled:opacity-50 ${
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
              Until (UTC day)
            </div>
            <div className="mb-2 flex flex-wrap gap-1">
              {UNTIL_PRESETS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  disabled={running}
                  onClick={() => setUntil(p.until)}
                  className={`rounded border px-2 py-1 font-mono text-xs disabled:opacity-50 ${
                    until === p.until
                      ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                      : "border-terminal-border text-terminal-muted"
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <input
              type="date"
              disabled={running}
              value={until}
              onChange={(e) => setUntil(e.target.value)}
              className="rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none disabled:opacity-50"
            />
          </div>

          <div className="flex flex-col gap-2 text-xs">
            <label className="flex items-center gap-2 text-terminal-muted">
              <input
                type="checkbox"
                disabled={running}
                checked={refreshTip}
                onChange={(e) => setRefreshTip(e.target.checked)}
              />
              Also refresh tip to now
            </label>
            <label>
              <span className="mb-1 block text-terminal-muted">Max REST pages / series</span>
              <input
                type="number"
                min={1}
                max={500}
                disabled={running}
                value={maxPages}
                onChange={(e) => setMaxPages(Number(e.target.value) || 200)}
                className="w-28 rounded border border-terminal-border bg-transparent px-2 py-1.5 font-mono text-sm focus:border-terminal-accent focus:outline-none disabled:opacity-50"
              />
            </label>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={running}
            onClick={() => void start()}
            className="rounded border border-terminal-accent bg-terminal-accent/15 px-4 py-1.5 text-sm text-terminal-accent disabled:opacity-50"
          >
            {running ? `Fetching… ${progressLabel}%` : "Fetch & store in DB"}
          </button>
          {running ? (
            <button
              type="button"
              onClick={() => void cancelJob()}
              className="rounded border border-rose-500/50 px-3 py-1.5 text-sm text-rose-200"
            >
              Cancel
            </button>
          ) : null}
          <button
            type="button"
            disabled={running}
            onClick={() => void refreshCoverage()}
            className="rounded border border-terminal-border px-3 py-1.5 text-sm text-terminal-muted hover:text-terminal-text disabled:opacity-50"
          >
            Refresh coverage
          </button>
        </div>

        {error ? (
          <div className="mt-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-[11px] text-rose-100">
            {error}
          </div>
        ) : null}

        {job && job.status !== "idle" ? (
          <div className="mt-3 rounded border border-terminal-border/70 bg-black/25 px-3 py-2">
            <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2 font-mono text-[11px]">
              <span className="text-terminal-accent">
                {job.status.toUpperCase()}
                {job.total_cells
                  ? ` · ${job.done_cells ?? 0}/${job.total_cells} cells · ${progressLabel}%`
                  : ""}
                {job.written_total != null ? ` · +${job.written_total} bars` : ""}
              </span>
              <span className="text-terminal-muted">
                {job.current
                  ? `Working ${job.current}`
                  : job.finished_at
                    ? "Finished — safe to leave this page while it ran"
                    : "Runs on server · survives page switches"}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded bg-terminal-border/50">
              <div
                className="h-full bg-terminal-accent transition-[width] duration-300 ease-out"
                style={{
                  width: `${Math.min(100, Math.max(progressPct > 0 ? 2 : 0, progressPct))}%`,
                }}
              />
            </div>
            {job.error ? (
              <div className="mt-1.5 text-[11px] text-rose-200">{job.error}</div>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="mt-4 overflow-x-auto rounded border border-terminal-border">
        <table className="w-full min-w-[640px] border-collapse text-left text-xs">
          <thead className="bg-black/30 font-mono text-[11px] uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-3 py-2">Symbol</th>
              <th className="px-3 py-2">TF</th>
              <th className="px-3 py-2">Bars</th>
              <th className="px-3 py-2">DB start</th>
              <th className="px-3 py-2">DB end</th>
              <th className="px-3 py-2">Last job</th>
            </tr>
          </thead>
          <tbody>
            {coverage.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-terminal-muted">
                  No coverage yet — pick symbols/TFs or run a fetch.
                </td>
              </tr>
            ) : (
              coverage.map((row) => {
                const jr = job?.results?.find(
                  (r) => r.symbol === row.symbol && r.timeframe === row.timeframe
                );
                return (
                  <tr
                    key={`${row.symbol}-${row.timeframe}`}
                    className="border-t border-terminal-border/60 font-mono"
                  >
                    <td className="px-3 py-2 text-terminal-text">
                      {row.symbol.replace("USDT", "")}
                    </td>
                    <td className="px-3 py-2 text-terminal-muted">{row.timeframe}</td>
                    <td className="px-3 py-2">{row.bars?.toLocaleString() ?? "—"}</td>
                    <td className="px-3 py-2">
                      {row.start ? row.start.slice(0, 10) : "—"}
                    </td>
                    <td className="px-3 py-2">
                      {row.end ? row.end.slice(0, 10) : "—"}
                    </td>
                    <td className="px-3 py-2 text-terminal-muted">
                      {jr
                        ? jr.error
                          ? `error: ${jr.error}`
                          : `+${jr.written} (${jr.direction || "—"})`
                        : "—"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
