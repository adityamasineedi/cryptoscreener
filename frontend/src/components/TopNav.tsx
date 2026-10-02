import { useEffect, useState } from "react";
import { fetchPresets } from "../api/client";
import { useMarketStore } from "../store/marketStore";
import type { ScreenerPreset } from "../types/market";
import { StatusBadge } from "./FreshCell";

const tabs = [
  "Fundamentals",
  "Futures",
  "Market Structure",
  "Supply/Demand",
  "Liquidations",
  "Volume Analysis",
  "Open Interest",
];

function DomainLoadingHint() {
  const loading = useMarketStore((s) => s.domainLoading);
  if (!loading) return null;
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-terminal-accent">
      <span
        className="h-3 w-3 animate-spin rounded-full border border-terminal-border border-t-terminal-accent"
        aria-hidden
      />
      Loading
    </span>
  );
}

export function TopNav({
  active,
  onChange,
  health,
  detailToggle,
}: {
  active: string;
  onChange: (t: string) => void;
  health?: {
    connected: boolean;
    ingestion: string;
    symbols: number;
    tickers: number;
  };
  detailToggle?: {
    open: boolean;
    onToggle: () => void;
    hasSelection: boolean;
  };
}) {
  return (
    <div className="min-w-0 shrink-0 overflow-hidden border-b border-terminal-border bg-terminal-panel/60">
      <div className="flex min-w-0 items-center gap-3 px-4 py-2">
        <div className="nav-scroll flex min-w-0 flex-1 items-center gap-1">
          {tabs.map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => onChange(tab)}
              disabled={active === tab}
              className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded px-3 py-1.5 text-xs font-medium transition ${
                active === tab
                  ? "bg-terminal-accent text-white"
                  : "text-terminal-muted hover:bg-white/5 hover:text-terminal-text"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>
        <div className="flex shrink-0 items-center gap-2 text-xs text-terminal-muted sm:gap-3">
          <DomainLoadingHint />
          {detailToggle ? (
            <button
              type="button"
              onClick={detailToggle.onToggle}
              className={`h-7 shrink-0 rounded border px-2 text-[11px] ${
                detailToggle.open
                  ? "border-terminal-accent bg-terminal-accent/15 text-terminal-text"
                  : "border-terminal-border bg-terminal-bg/70 text-terminal-muted"
              }`}
            >
              {detailToggle.open ? "Hide detail" : "Detail"}
            </button>
          ) : null}
          <StatusBadge status={health?.connected ? "LIVE" : "STALE"} />
          <span className="hidden whitespace-nowrap term:inline">
            WS {health?.connected ? "connected" : "reconnecting"}
          </span>
          <span className="hidden whitespace-nowrap term-md:inline">
            Ingestion: {health?.ingestion ?? "—"}
          </span>
          <span className="whitespace-nowrap">
            {health?.tickers ?? 0}/{health?.symbols ?? 0}
          </span>
        </div>
      </div>
      <FilterBar domain={active} />
    </div>
  );
}

function FilterBar({ domain }: { domain: string }) {
  const search = useMarketStore((s) => s.search);
  const setSearch = useMarketStore((s) => s.setSearch);
  const preset = useMarketStore((s) => s.preset);
  const setPreset = useMarketStore((s) => s.setPreset);
  const [presets, setPresets] = useState<ScreenerPreset[]>([]);

  useEffect(() => {
    let alive = true;
    fetchPresets()
      .then((d) => {
        if (alive) setPresets(d.presets || []);
      })
      .catch(() => {
        if (alive) setPresets([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="flex min-w-0 items-center gap-2 border-t border-terminal-border px-4 py-2">
      <input
        value={search}
        onChange={(e) => setSearch(e.target.value.toUpperCase())}
        placeholder="Search symbol…"
        className="h-8 w-36 shrink-0 rounded border border-terminal-border bg-terminal-bg px-2 font-mono text-xs outline-none focus:border-terminal-accent sm:w-44"
      />
      {(domain === "Fundamentals" || domain === "Futures") && (
        <div className="nav-scroll flex min-w-0 flex-1 items-center gap-1">
          <span className="shrink-0 text-[10px] uppercase text-terminal-muted">Preset</span>
          <button
            type="button"
            onClick={() => setPreset(null)}
            className={`h-8 shrink-0 rounded border px-2.5 text-[11px] ${
              !preset
                ? "border-terminal-accent bg-terminal-accent/15 text-terminal-text"
                : "border-terminal-border bg-terminal-bg/70 text-terminal-muted"
            }`}
          >
            All
          </button>
          {presets.map((p) => (
            <button
              key={p.id}
              type="button"
              title={p.description}
              onClick={() => setPreset(p.id === "custom" && preset === "custom" ? null : p.id)}
              className={`h-8 shrink-0 whitespace-nowrap rounded border px-2.5 text-[11px] ${
                preset === p.id
                  ? "border-terminal-accent bg-terminal-accent/15 text-terminal-text"
                  : "border-terminal-border bg-terminal-bg/70 text-terminal-muted hover:text-terminal-text"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      )}
      <span className="hidden shrink-0 text-[10px] text-terminal-muted term-lg:inline">
        Cap presets are filter configs — not hardcoded lists
      </span>
    </div>
  );
}
