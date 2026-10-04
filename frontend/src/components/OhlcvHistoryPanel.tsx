import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { fetchResearchOhlcvRange, type OhlcvRangeRow } from "../api/client";
import {
  mergeSymbols,
  parseSymbolList,
  RESEARCH_SYMBOL_PRESETS,
} from "../research/symbols";
import { useOhlcvExpandStore } from "../store/ohlcvExpandStore";

const SYMBOL_OPTIONS = RESEARCH_SYMBOL_PRESETS;
const TF_OPTIONS = ["5m", "15m", "1h", "4h", "1d"] as const;
const TF_MS: Record<string, number> = {
  "5m": 5 * 60_000,
  "15m": 15 * 60_000,
  "1h": 60 * 60_000,
  "4h": 4 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
};
const UNTIL_PRESETS = [
  { id: "2023", until: "2023-01-01", label: "From 2023" },
  { id: "2024", until: "2024-01-01", label: "From 2024" },
  { id: "2025", until: "2025-01-01", label: "From 2025" },
  { id: "2026", until: "2026-01-01", label: "From 2026" },
] as const;

/** True when Postgres tip is more than ~2 bars behind now (or empty). */
function isTipStale(end: string | null | undefined, timeframe: string): boolean {
  if (!end) return true;
  const endMs = Date.parse(end);
  if (!Number.isFinite(endMs)) return true;
  const step = TF_MS[timeframe] ?? 60_000;
  return Date.now() - endMs > step * 2;
}

function formatCoverageTs(raw: string | null | undefined): string {
  if (!raw) return "—";
  const d = new Date(raw);
  if (!Number.isFinite(d.getTime())) return String(raw).slice(0, 19);
  return d.toISOString().replace("T", " ").slice(0, 19) + "Z";
}

/** How much older history is still missing vs the backfill-from target. */
function remainingBackLabel(
  start: string | null | undefined,
  until: string
): string {
  if (!until) return "—";
  if (!start) return `need all → ${until}`;
  const startDay = start.slice(0, 10);
  if (startDay <= until) return "complete";
  const days = Math.round(
    (Date.parse(`${startDay}T00:00:00Z`) - Date.parse(`${until}T00:00:00Z`)) /
      86_400_000
  );
  if (!Number.isFinite(days) || days <= 0) return "complete";
  return `~${days.toLocaleString()}d left → ${until}`;
}

/** How far the DB tip lags behind the latest closed bar / now. */
function behindNowLabel(
  end: string | null | undefined,
  timeframe: string
): { text: string; stale: boolean } {
  if (!end) return { text: "empty — sync tip", stale: true };
  const endMs = Date.parse(end);
  if (!Number.isFinite(endMs)) return { text: "?", stale: true };
  const lagMs = Date.now() - endMs;
  const step = TF_MS[timeframe] ?? 60_000;
  if (lagMs <= step * 2) {
    return { text: `current (${formatCoverageTs(end)})`, stale: false };
  }
  const hours = lagMs / 3_600_000;
  if (hours < 48) {
    return { text: `${hours.toFixed(1)}h behind`, stale: true };
  }
  return { text: `${(hours / 24).toFixed(1)}d behind`, stale: true };
}

function toggleInList<T extends string>(list: T[], value: T): T[] {
  return list.includes(value)
    ? list.filter((x) => x !== value)
    : [...list, value];
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
    const fromUrl = parseSymbolList(searchParams.get("symbols"));
    return fromUrl.length ? fromUrl : ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
  }, [searchParams]);
  const initialTfs = useMemo(() => {
    const fromUrl = parseTfParam(searchParams.get("tfs"), TF_OPTIONS);
    return fromUrl.length ? fromUrl : ["15m", "1h"];
  }, [searchParams]);
  const initialUntil = searchParams.get("until") || "2023-01-01";

  const [symbols, setSymbols] = useState<string[]>(initialSymbols);
  const [customSymbols, setCustomSymbols] = useState("");
  const [timeframes, setTimeframes] = useState<string[]>(initialTfs);
  const [until, setUntil] = useState(initialUntil);
  const [maxPages, setMaxPages] = useState(200);
  const [coverage, setCoverage] = useState<OhlcvRangeRow[]>([]);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const job = useOhlcvExpandStore((s) => s.job);
  const error = useOhlcvExpandStore((s) => s.error);
  const active = useOhlcvExpandStore((s) => s.active);
  const startJob = useOhlcvExpandStore((s) => s.start);
  const cancelJob = useOhlcvExpandStore((s) => s.cancel);
  const setError = useOhlcvExpandStore((s) => s.setError);

  const running = job?.status === "running" || active;
  const autoTipKeyRef = useRef<string>("");
  const [autoTipNote, setAutoTipNote] = useState<string | null>(null);

  // Keep "behind now" labels live while viewing the page.
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 15_000);
    return () => window.clearInterval(id);
  }, []);

  const refreshCoverage = useCallback(async () => {
    if (!symbols.length || !timeframes.length) {
      setCoverage([]);
      return [];
    }
    try {
      const r = await fetchResearchOhlcvRange({ symbols, timeframes });
      const rows = r.rows || [];
      setCoverage(rows);
      return rows;
    } catch {
      setCoverage([]);
      return [];
    }
  }, [symbols, timeframes]);

  const syncTipToNow = useCallback(
    async (rows?: OhlcvRangeRow[], { quiet = false } = {}) => {
      if (!symbols.length || !timeframes.length) {
        if (!quiet) setError("Pick at least one symbol and timeframe");
        return;
      }
      if (running) return;
      const staleTfs = timeframes.filter((tf) => {
        const matching = (rows ?? coverage).filter((r) => r.timeframe === tf);
        if (!matching.length) return true;
        return matching.some((r) => isTipStale(r.end, tf));
      });
      const tfs = staleTfs.length ? staleTfs : timeframes;
      if (!quiet) setAutoTipNote(null);
      await startJob({
        symbols,
        timeframes: tfs,
        refresh_tip: true,
        max_pages: Math.min(40, maxPages),
        // omit until → tip-only forward fill to current closed bars
      });
    },
    [symbols, timeframes, coverage, running, maxPages, startJob, setError]
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const rows = await refreshCoverage();
      if (cancelled) return;
      const key = `${symbols.join(",")}|${timeframes.join(",")}`;
      if (autoTipKeyRef.current === key) return;
      const store = useOhlcvExpandStore.getState();
      if (store.active || store.job?.status === "running") return;
      const stale =
        rows.length === 0 ||
        rows.some((r) => isTipStale(r.end, r.timeframe));
      if (!stale) {
        autoTipKeyRef.current = key;
        setAutoTipNote(null);
        return;
      }
      autoTipKeyRef.current = key;
      setAutoTipNote("Syncing DB tip to now…");
      await syncTipToNow(rows, { quiet: true });
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols, timeframes]);

  useEffect(() => {
    if (job?.status === "done" || job?.status === "cancelled") {
      void refreshCoverage();
      if (job?.status === "done") {
        setAutoTipNote(
          job.until
            ? "Backfill finished · tip filled to latest closed bars"
            : "Tip synced to latest closed bars"
        );
      }
    }
  }, [job?.status, job?.finished_at, job?.until, refreshCoverage]);

  const start = useCallback(async () => {
    if (!symbols.length || !timeframes.length) {
      setError("Pick at least one symbol and timeframe");
      return;
    }
    if (!until) {
      setError("Pick a history-from date (UTC day to walk older bars back to)");
      return;
    }
    setAutoTipNote(null);
    await startJob({
      symbols,
      timeframes,
      until,
      // Always fill forward to the latest closed bar — Until is only the back target.
      refresh_tip: true,
      max_pages: maxPages,
    });
  }, [symbols, timeframes, until, maxPages, startJob, setError]);

  const progressPct = Math.min(100, Number(job?.pct ?? 0));
  const progressLabel =
    progressPct > 0 && progressPct < 10
      ? progressPct.toFixed(1)
      : String(Math.round(progressPct));

  const nowLabel = useMemo(
    () => new Date(nowMs).toISOString().replace("T", " ").slice(0, 19) + "Z",
    [nowMs]
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4 term-md:p-6">
      <div className="mb-4">
        <div className="font-display text-xl font-semibold text-terminal-text">
          OHLCV History
        </div>
        <div className="mt-1 text-sm text-terminal-muted">
          Coverage always shows the full DB range (start → tip). Fetch only pulls{" "}
          <em>missing</em> gaps (older than DB start, or tip behind now). Series that
          already cover your History-from date and tip are skipped instantly. Now:{" "}
          <span className="font-mono text-terminal-text">{nowLabel}</span>.
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
              {symbols
                .filter((s) => !(SYMBOL_OPTIONS as readonly string[]).includes(s))
                .map((s) => (
                  <button
                    key={s}
                    type="button"
                    disabled={running}
                    title={`Remove ${s}`}
                    onClick={() => setSymbols((prev) => prev.filter((x) => x !== s))}
                    className="rounded border border-terminal-accent bg-terminal-accent/15 px-2 py-1 font-mono text-xs text-terminal-accent disabled:opacity-50"
                  >
                    {s.replace("USDT", "")} ×
                  </button>
                ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              <input
                value={customSymbols}
                disabled={running}
                onChange={(e) => setCustomSymbols(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  e.preventDefault();
                  const added = parseSymbolList(customSymbols);
                  if (!added.length) return;
                  setSymbols((prev) => mergeSymbols(prev, added));
                  setCustomSymbols("");
                }}
                placeholder="Add: INJUSDT, PEPEUSDT…"
                className="min-w-[12rem] flex-1 rounded border border-terminal-border bg-transparent px-2 py-1 font-mono text-xs focus:border-terminal-accent focus:outline-none disabled:opacity-50"
              />
              <button
                type="button"
                disabled={running}
                onClick={() => {
                  const added = parseSymbolList(customSymbols);
                  if (!added.length) return;
                  setSymbols((prev) => mergeSymbols(prev, added));
                  setCustomSymbols("");
                }}
                className="rounded border border-terminal-border px-2 py-1 text-xs text-terminal-muted hover:text-terminal-text disabled:opacity-50"
              >
                Add
              </button>
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
              History from (UTC day)
            </div>
            <div className="mb-1 text-[10px] text-terminal-muted">
              Walk <em>older</em> bars back to this day. Does not truncate the tip —
              DB end stays at the latest closed candle.
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
            <div className="rounded border border-terminal-border/70 bg-black/20 px-2 py-1.5 text-terminal-muted">
              Tip fill: <span className="text-emerald-300">always on</span>
              <div className="mt-0.5 text-[10px]">
                Every Fetch also pulls forward to the latest closed bar (
                {nowLabel}).
              </div>
            </div>
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
            {running
              ? `Fetching… ${progressLabel}%`
              : `Fetch ${until} → now`}
          </button>
          <button
            type="button"
            disabled={running}
            onClick={() => void syncTipToNow()}
            className="rounded border border-terminal-border px-3 py-1.5 text-sm text-terminal-text hover:border-terminal-accent disabled:opacity-50"
          >
            Sync tip to now only
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

        {autoTipNote ? (
          <div className="mt-3 rounded border border-terminal-accent/30 bg-terminal-accent/10 px-3 py-2 text-[11px] text-terminal-accent">
            {autoTipNote}
          </div>
        ) : null}

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
                {job.until ? ` · from ${job.until} → tip` : " · tip only"}
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
        <table className="w-full min-w-[880px] border-collapse text-left text-xs">
          <thead className="bg-black/30 font-mono text-[11px] uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-3 py-2">Symbol</th>
              <th className="px-3 py-2">TF</th>
              <th className="px-3 py-2">Bars</th>
              <th className="px-3 py-2">DB start</th>
              <th className="px-3 py-2">DB end (UTC)</th>
              <th className="px-3 py-2">Still missing back</th>
              <th className="px-3 py-2">Tip vs now</th>
              <th className="px-3 py-2">Last job</th>
            </tr>
          </thead>
          <tbody>
            {coverage.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-4 text-terminal-muted">
                  No coverage yet — pick symbols/TFs or run a fetch.
                </td>
              </tr>
            ) : (
              coverage.map((row) => {
                const jr = job?.results?.find(
                  (r) => r.symbol === row.symbol && r.timeframe === row.timeframe
                );
                const back = remainingBackLabel(row.start, until);
                const tip = behindNowLabel(row.end, row.timeframe);
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
                    <td className="px-3 py-2">{formatCoverageTs(row.start)}</td>
                    <td className="px-3 py-2">{formatCoverageTs(row.end)}</td>
                    <td
                      className={`px-3 py-2 ${
                        back === "complete"
                          ? "text-emerald-300"
                          : "text-amber-300"
                      }`}
                    >
                      {back}
                    </td>
                    <td
                      className={`px-3 py-2 ${
                        tip.stale ? "text-amber-300" : "text-emerald-300"
                      }`}
                    >
                      {tip.text}
                    </td>
                    <td className="px-3 py-2 text-terminal-muted">
                      {jr
                        ? jr.error
                          ? `error: ${jr.error}`
                          : jr.direction === "already_complete"
                            ? "already complete (0 new)"
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
