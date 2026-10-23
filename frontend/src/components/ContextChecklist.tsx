export const BANNER = "Context only; no combination of these is a tested signal (FINAL_VERDICT).";

export type ChecklistRow = {
  category: string;
  state: string;
  alignment: string;
};

export type ContextChecklistPayload = {
  banner: string;
  timeframe?: string | null;
  symbol?: string | null;
  rows: ChecklistRow[];
  counts: Record<string, number>;
  read_only?: boolean;
};

export function ContextChecklist({ data }: { data: ContextChecklistPayload }) {
  const counts = data.counts || {};
  return (
    <div className="space-y-2" data-testid="context-checklist">
      <p className="text-[12px] text-amber-200" data-testid="context-banner">
        {data.banner}
      </p>
      <p className="text-[10px] uppercase tracking-wide text-terminal-muted">
        {data.symbol} {data.timeframe} · aligned with a long {counts["aligned with a long"] ?? 0} ·
        aligned with a short {counts["aligned with a short"] ?? 0} · mixed/neutral{" "}
        {counts["mixed/neutral"] ?? 0}
      </p>
      <ul className="space-y-1">
        {data.rows.map((row) => (
          <li
            key={row.category}
            className="rounded border border-terminal-border/70 px-2 py-1"
            data-testid={`context-row-${row.category}`}
          >
            <div className="text-[11px] uppercase tracking-wide text-terminal-muted">{row.category}</div>
            <div className="text-[12px]">{row.state}</div>
            <div className="text-[10px] text-terminal-muted">{row.alignment}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}
