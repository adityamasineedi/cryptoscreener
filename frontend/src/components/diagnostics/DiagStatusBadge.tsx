const COLORS: Record<string, string> = {
  HEALTHY: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  DEGRADED: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  WARNING: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  ERROR: "bg-rose-500/15 text-rose-300 border-rose-500/40",
  CRITICAL: "bg-rose-500/20 text-rose-200 border-rose-400/50",
  STALE: "bg-orange-500/15 text-orange-300 border-orange-500/40",
  WAITING: "bg-sky-500/15 text-sky-300 border-sky-500/40",
  DISABLED: "bg-white/5 text-terminal-muted border-terminal-border",
  UNAVAILABLE: "bg-rose-500/10 text-rose-300/90 border-rose-500/30",
  UNKNOWN: "bg-white/5 text-terminal-muted border-terminal-border",
  MISSING: "bg-violet-500/15 text-violet-300 border-violet-500/40",
  OPEN: "bg-rose-500/15 text-rose-300 border-rose-500/40",
  ACKNOWLEDGED: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  RESOLVED: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  SUPPRESSED: "bg-white/5 text-terminal-muted border-terminal-border",
  INFO: "bg-sky-500/15 text-sky-300 border-sky-500/40",
};

export function DiagStatusBadge({ status }: { status: string }) {
  const key = (status || "UNKNOWN").toUpperCase();
  const cls = COLORS[key] || COLORS.UNKNOWN;
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold tracking-wide ${cls}`}
    >
      {key}
    </span>
  );
}
