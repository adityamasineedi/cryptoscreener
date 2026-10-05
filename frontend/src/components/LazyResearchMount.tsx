/**
 * Collapsible research section — children mount only when expanded.
 * Prevents nested Backtest panels from fetching on initial page load.
 */
import { useState, type ReactNode } from "react";

export function LazyResearchMount({
  panelId,
  label,
  children,
  defaultOpen = false,
}: {
  panelId: string;
  label: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section
      className="mt-8 border-t border-terminal-border pt-6"
      data-panel-id={panelId}
      data-lazy-research="true"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="text-[11px] uppercase tracking-[0.18em] text-terminal-muted">
          Research panel — loaded on demand
          <span className="ml-2 normal-case tracking-normal text-terminal-text/70">
            · {label}
          </span>
        </div>
        <button
          type="button"
          className="rounded border border-terminal-border px-3 py-1.5 text-[11px] uppercase tracking-wide text-terminal-text hover:border-terminal-accent/60 hover:text-terminal-accent"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          data-testid={`lazy-research-toggle-${panelId}`}
        >
          {open ? "Collapse" : "Expand to load"}
        </button>
      </div>
      {open ? children : null}
    </section>
  );
}
