import { create } from "zustand";
import type { FreshValue, ScreenerRow } from "../types/market";
import { mergeScreenerRowPreferLive } from "../utils/freshDisplay";

interface MarketState {
  rows: Record<string, ScreenerRow>;
  orderedSymbols: string[];
  total: number;
  ingestion: string;
  connected: boolean;
  selectedSymbol: string | null;
  search: string;
  preset: string | null;
  domain: string;
  /** True while screener REST reload runs after domain/preset/search change */
  domainLoading: boolean;
  setSnapshot: (rows: ScreenerRow[], total: number, ingestion: string) => void;
  applyBatch: (updates: Array<{ type: string; symbol: string; payload: Record<string, unknown> }>) => void;
  applyRowPatch: (symbol: string, changes: Partial<ScreenerRow>) => void;
  setConnected: (v: boolean) => void;
  setSelected: (symbol: string | null) => void;
  setSearch: (q: string) => void;
  setPreset: (id: string | null) => void;
  setDomain: (d: string) => void;
  setDomainLoading: (v: boolean) => void;
}

function patchFresh(
  row: ScreenerRow,
  field: keyof ScreenerRow,
  value: unknown,
  timestamp: string,
  source: string
) {
  if (value === undefined) return;
  const fv: FreshValue = {
    value: value as number | string,
    timestamp,
    source,
    status: "LIVE",
  };
  (row as unknown as Record<string, unknown>)[field] = fv;
}

export const useMarketStore = create<MarketState>((set, get) => ({
  rows: {},
  orderedSymbols: [],
  total: 0,
  ingestion: "starting",
  connected: false,
  selectedSymbol: null,
  search: "",
  preset: "large_cap",
  domain: "Futures",
  domainLoading: false,
  setSnapshot: (rows, total, ingestion) => {
    const prev = get().rows;
    const map: Record<string, ScreenerRow> = {};
    const ordered: string[] = [];
    for (const row of rows) {
      // Don't let a stale REST snapshot overwrite a newer LIVE WS tick
      // (that was causing UNAVAILABLE badges next to freshly streamed prices).
      map[row.symbol] = mergeScreenerRowPreferLive(prev[row.symbol], row);
      ordered.push(row.symbol);
    }
    set({ rows: map, orderedSymbols: ordered, total, ingestion, domainLoading: false });
  },
  applyRowPatch: (symbol, changes) => {
    const existing = get().rows[symbol];
    if (!existing) return;
    // Replace only the patched symbol entry — avoids full table remap
    set({
      rows: {
        ...get().rows,
        [symbol]: { ...existing, ...changes, symbol },
      },
    });
  },
  applyBatch: (updates) => {
    const { rows } = get();
    let mutated = false;
    const next = { ...rows };
    for (const u of updates) {
      const existing = next[u.symbol];
      if (!existing) continue;
      const row = { ...existing };
      const p = u.payload;
      const ts = (p.timestamp as string) || new Date().toISOString();
      const source = (p.source as string) || "binance_ws";
      if (u.type === "ticker") {
        patchFresh(row, "price", p.price, ts, source);
        patchFresh(row, "change_24h_pct", p.price_change_pct_24h, ts, source);
        patchFresh(row, "volume_24h", p.volume_24h, ts, source);
        patchFresh(row, "quote_volume_24h", p.quote_volume_24h, ts, source);
        patchFresh(row, "high_24h", p.high_24h, ts, source);
        patchFresh(row, "low_24h", p.low_24h, ts, source);
      } else if (u.type === "mark_price") {
        patchFresh(row, "funding_rate", p.funding_rate, ts, source);
        if (p.mark_price != null) {
          patchFresh(row, "price", p.mark_price, ts, source);
        }
      }
      next[u.symbol] = row;
      mutated = true;
    }
    if (mutated) set({ rows: next });
  },
  setConnected: (v) => set({ connected: v }),
  setSelected: (symbol) => set({ selectedSymbol: symbol }),
  setSearch: (q) => set({ search: q, domainLoading: true }),
  setPreset: (id) => set({ preset: id, domainLoading: true }),
  setDomain: (d) => set({ domain: d, domainLoading: true }),
  setDomainLoading: (v) => set({ domainLoading: v }),
}));
