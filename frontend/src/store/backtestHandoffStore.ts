import { create } from "zustand";

type BacktestHandoffState = {
  symbols: string[];
  revision: number;
  queue: (symbols: string[]) => void;
  consume: () => string[];
};

export const useBacktestHandoffStore = create<BacktestHandoffState>((set, get) => ({
  symbols: [],
  revision: 0,
  queue: (symbols) => {
    const unique: string[] = [];
    const seen = new Set<string>();
    for (const raw of symbols) {
      const symbol = String(raw || "").trim().toUpperCase();
      if (!symbol || seen.has(symbol)) continue;
      seen.add(symbol);
      unique.push(symbol);
    }
    if (!unique.length) return;
    set({ symbols: unique, revision: get().revision + 1 });
  },
  consume: () => {
    const symbols = get().symbols;
    if (!symbols.length) return [];
    set({ symbols: [] });
    return symbols;
  },
}));
