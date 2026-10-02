export function Spinner({
  label = "Loading…",
  className = "",
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center gap-2 text-terminal-muted ${className}`}
      role="status"
      aria-live="polite"
    >
      <span
        className="h-5 w-5 animate-spin rounded-full border-2 border-terminal-border border-t-terminal-accent"
        aria-hidden
      />
      <span className="text-[11px] uppercase tracking-wide">{label}</span>
    </div>
  );
}
