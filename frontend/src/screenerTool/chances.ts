/** Owner D188 part 1e: sort and filter Trade-chances rows by cost OK, HTF agreement and coin. Pure. */
import type { Arrow, ContextRow } from "./client";

export type ChanceSort = "default" | "cost" | "stop" | "coin";

export type ChanceFilter = {
  costOkOnly: boolean;
  htfAgreesOnly: boolean;
  coin: string;
};

export const DEFAULT_FILTER: ChanceFilter = { costOkOnly: false, htfAgreesOnly: false, coin: "" };

type Keyed = { symbol: string };

export function contextBySymbol(rows: ContextRow[] | undefined | null): Map<string, ContextRow> {
  const out = new Map<string, ContextRow>();
  for (const r of rows ?? []) out.set(r.symbol.toUpperCase(), r);
  return out;
}

export function applyChanceFilter<T extends Keyed>(rows: T[], ctx: Map<string, ContextRow>, f: ChanceFilter): T[] {
  const coin = f.coin.trim().toUpperCase();
  return rows.filter((r) => {
    const c = ctx.get(r.symbol.toUpperCase());
    if (coin && !r.symbol.toUpperCase().includes(coin)) return false;
    if (f.costOkOnly && c?.cost.cost_ok !== true) return false;
    if (f.htfAgreesOnly && c?.htf_agrees !== true) return false;
    return true;
  });
}

function cmpNullLast(a: number | null | undefined, b: number | null | undefined, desc = false): number {
  const an = a == null || Number.isNaN(a);
  const bn = b == null || Number.isNaN(b);
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  return desc ? (b as number) - (a as number) : (a as number) - (b as number);
}

/** "default" keeps the server's tier order; the other sorts are stable, rows without a value last. */
export function sortChances<T extends Keyed>(rows: T[], ctx: Map<string, ContextRow>, sort: ChanceSort): T[] {
  if (sort === "default") return rows;
  const keyed = rows.map((r, i) => ({ r, i, c: ctx.get(r.symbol.toUpperCase()) }));
  keyed.sort((a, b) => {
    let d = 0;
    if (sort === "cost") d = cmpNullLast(a.c?.cost.cost_r, b.c?.cost.cost_r);
    else if (sort === "stop") d = cmpNullLast(a.c?.cost.stop_distance_pct, b.c?.cost.stop_distance_pct, true);
    else if (sort === "coin") d = a.r.symbol.localeCompare(b.r.symbol);
    return d !== 0 ? d : a.i - b.i;
  });
  return keyed.map((k) => k.r);
}

export function arrowGlyph(a: Arrow | undefined): string {
  if (a === "UP") return "↑";
  if (a === "DOWN") return "↓";
  if (a === "FLAT") return "→";
  return "·";
}

export function costLabel(c: ContextRow | undefined): string {
  if (!c) return "—";
  if (c.cost.cost_r == null) return c.cost.status === "STOP_WRONG_SIDE" ? "stop wrong side" : "—";
  return `${c.cost.cost_r.toFixed(2)} R`;
}
