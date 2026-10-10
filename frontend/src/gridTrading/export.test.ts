import { describe, expect, it } from "vitest";
import { bookToJson, equityToCsv, fillsToCsv } from "./export";

describe("grid export", () => {
  it("writes fills, equity, and a combined json book", () => {
    const fills = [{ time: 1, side: "buy", slot: 0, level: 100, price: 100, qty: 1, fee: 0.1, reason: null }];
    const curve = [{ time: 1, equity: 1000, drawdown: 0 }];
    expect(fillsToCsv(fills)).toContain("buy");
    expect(fillsToCsv(fills).split("\n")).toHaveLength(2);
    expect(equityToCsv(curve)).toBe("time,equity,drawdown\n1,1000,0");
    expect(JSON.parse(bookToJson(fills, curve)).fills[0].side).toBe("buy");
  });
});
