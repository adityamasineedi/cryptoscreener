import { describe, expect, it } from "vitest";
import { applyChanceFilter, arrowGlyph, contextBySymbol, costLabel, DEFAULT_FILTER, sortChances } from "./chances";
import type { ContextRow } from "./client";

function ctxRow(symbol: string, costR: number | null, stopPct: number | null, agrees: boolean | null): ContextRow {
  return {
    symbol,
    direction: "LONG",
    cost: {
      status: costR == null ? "NO_LEVELS" : "OK",
      slippage_bps: 2,
      thin_coin: false,
      round_trip_pct: 0.14,
      stop_distance_pct: stopPct,
      cost_r: costR,
      cost_r_max: 0.25,
      cost_ok: costR == null ? null : costR <= 0.25,
      cost_too_high: costR == null ? null : costR > 0.25,
    },
    htf: {},
    htf_agrees: agrees,
    badge: { setup_type: "TREND_BOS", setup_label: "", verdict: "NO_EDGE_FOUND", text: "", detail: "", not_a_signal: "" },
  };
}

const rows = [{ symbol: "SOLUSDT" }, { symbol: "ETHUSDT" }, { symbol: "ARBUSDT" }, { symbol: "XRPUSDT" }];
const ctx = contextBySymbol([
  ctxRow("SOLUSDT", 0.467, 0.3, true),
  ctxRow("ETHUSDT", 0.07, 2.0, true),
  ctxRow("ARBUSDT", 0.2, 1.0, false),
  ctxRow("XRPUSDT", null, null, null),
]);

describe("Trade chances filters and sorts", () => {
  it("filters cost OK, HTF agrees and coin", () => {
    expect(applyChanceFilter(rows, ctx, DEFAULT_FILTER).map((r) => r.symbol)).toEqual(rows.map((r) => r.symbol));
    expect(applyChanceFilter(rows, ctx, { ...DEFAULT_FILTER, costOkOnly: true }).map((r) => r.symbol)).toEqual([
      "ETHUSDT",
      "ARBUSDT",
    ]);
    expect(applyChanceFilter(rows, ctx, { ...DEFAULT_FILTER, htfAgreesOnly: true }).map((r) => r.symbol)).toEqual([
      "SOLUSDT",
      "ETHUSDT",
    ]);
    expect(
      applyChanceFilter(rows, ctx, { costOkOnly: true, htfAgreesOnly: true, coin: "" }).map((r) => r.symbol),
    ).toEqual(["ETHUSDT"]);
    expect(applyChanceFilter(rows, ctx, { ...DEFAULT_FILTER, coin: "eth" }).map((r) => r.symbol)).toEqual(["ETHUSDT"]);
  });

  it("sorts by cost (low first), stop distance (wide first) and coin, unknown values last", () => {
    expect(sortChances(rows, ctx, "default")).toBe(rows);
    expect(sortChances(rows, ctx, "cost").map((r) => r.symbol)).toEqual(["ETHUSDT", "ARBUSDT", "SOLUSDT", "XRPUSDT"]);
    expect(sortChances(rows, ctx, "stop").map((r) => r.symbol)).toEqual(["ETHUSDT", "ARBUSDT", "SOLUSDT", "XRPUSDT"]);
    expect(sortChances(rows, ctx, "coin").map((r) => r.symbol)).toEqual(["ARBUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]);
  });

  it("labels", () => {
    expect(arrowGlyph("UP")).toBe("↑");
    expect(arrowGlyph("DOWN")).toBe("↓");
    expect(arrowGlyph(null)).toBe("·");
    expect(costLabel(ctx.get("ETHUSDT"))).toBe("0.07 R");
    expect(costLabel(undefined)).toBe("—");
  });
});
