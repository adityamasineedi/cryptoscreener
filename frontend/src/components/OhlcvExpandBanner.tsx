import { Link } from "react-router-dom";
import { useOhlcvExpandStore } from "../store/ohlcvExpandStore";

/** Persistent strip while an OHLCV expand job runs — survives page switches. */
export function OhlcvExpandBanner() {
  const job = useOhlcvExpandStore((s) => s.job);
  const active = useOhlcvExpandStore((s) => s.active);
  const cancel = useOhlcvExpandStore((s) => s.cancel);

  if (!active || !job || job.status !== "running") return null;

  const pctNum = Math.min(100, Number(job.pct ?? 0));
  const pct =
    pctNum > 0 && pctNum < 10 ? pctNum.toFixed(1) : String(Math.round(pctNum));
  const cells =
    job.total_cells != null
      ? `${job.done_cells ?? 0}/${job.total_cells}`
      : null;

  return (
    <div className="shrink-0 border-b border-terminal-accent/30 bg-terminal-accent/10 px-3 py-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-[11px]">
        <div className="min-w-0 text-terminal-accent">
          <span className="font-semibold">OHLCV fetch running in background</span>
          {cells ? ` · ${cells} cells · ${pct}%` : ` · ${pct}%`}
          {job.written_total != null ? ` · +${job.written_total} bars` : ""}
          {job.current ? (
            <span className="text-terminal-muted"> · {job.current}</span>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link
            to="/ohlcv-history"
            className="text-terminal-accent underline underline-offset-2 hover:text-terminal-text"
          >
            Open tab
          </Link>
          <button
            type="button"
            onClick={() => void cancel()}
            className="rounded border border-rose-500/40 px-2 py-0.5 text-rose-200 hover:bg-rose-500/10"
          >
            Cancel
          </button>
        </div>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded bg-terminal-border/50">
        <div
          className="h-full bg-terminal-accent transition-[width] duration-300 ease-out"
          style={{
            width: `${Math.min(100, Math.max(pctNum > 0 ? 2 : 0, pctNum))}%`,
          }}
        />
      </div>
    </div>
  );
}
