import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  assessProductionComparable,
  NEXT_OPEN_FILL_BOOK_NOTE,
  BACKTEST_CAP_SYMBOLS,
  BACKTEST_LARGE_SYMBOLS,
  BACKTEST_MID_SYMBOLS,
  BACKTEST_STRATEGY_OPTIONS,
  barsToApproxDays,
  filterBacktestCapSymbols,
  isBacktestCapSymbol,
  PASSED_STRATEGY_IDS,
  passedIdsForBook,
  strategyIdsForBook,
  formatBarsDuration,
  lastNDaysUtc,
  symbolRoleDisplay,
  timeframeRoleLabel,
  v1RiskForSymbol,
} from "./v1Config";

describe("v1Config lookback duration", () => {
  it("labels 5760 1h bars as ~240 days", () => {
    expect(barsToApproxDays(5760, "1h")).toBe(240);
    expect(formatBarsDuration(5760, "1h")).toContain("240 days");
  });

  it("labels 5760 15m bars as ~60 days", () => {
    expect(barsToApproxDays(5760, "15m")).toBe(60);
  });

  it("labels 5760 4h bars as ~960 days", () => {
    expect(barsToApproxDays(5760, "4h")).toBe(960);
  });

  it("builds a 365-day UTC window ending today", () => {
    const range = lastNDaysUtc(365, new Date("2026-10-12T18:00:00Z"));
    expect(range).toEqual({ start: "2025-10-12", end: "2026-10-12" });
  });
});

describe("v1Config timeframe roles", () => {
  it("labels 1h as v1 setup timeframe", () => {
    expect(timeframeRoleLabel("1h")).toBe("v1 setup timeframe");
  });
  it("labels 4h as HTF context", () => {
    expect(timeframeRoleLabel("4h")).toBe("HTF context");
  });
  it("labels 15m as research-only", () => {
    expect(timeframeRoleLabel("15m")).toBe("research-only");
  });
});

describe("v1Config risk and identity display", () => {
  it("BTC/ETH/SOL roles", () => {
    expect(symbolRoleDisplay("BTCUSDT")).toBe("BTCUSDT — v1 CORE");
    expect(symbolRoleDisplay("ETHUSDT")).toBe("ETHUSDT — v1 SECONDARY");
    expect(symbolRoleDisplay("SOLUSDT")).toBe("SOLUSDT — v1 SECONDARY");
  });

  it("v1 risk at $1000 principal", () => {
    expect(v1RiskForSymbol("BTCUSDT", 1000)).toEqual({
      riskPercent: 2,
      riskUsd: 20,
      role: "core",
    });
    expect(v1RiskForSymbol("ETHUSDT", 1000)?.riskUsd).toBe(20);
    expect(v1RiskForSymbol("SOLUSDT", 1000)?.riskUsd).toBe(20);
  });

  it("exact frozen config is not production-comparable while the screen shows the next-open book", () => {
    const r = assessProductionComparable({
      symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
      timeframes: ["1h"],
      direction: "LONG",
      researchRiskOverride: false,
      leverage: 2,
      takerFeePct: 0.04,
      makerFeePct: 0.02,
    });
    expect(r.productionComparable).toBe(false);
    // Every frozen-profile check passes; only the fill book differs.
    expect(r.reasons).toEqual(["next_open_fill_book"]);
    expect(r.note).toBe(NEXT_OPEN_FILL_BOOK_NOTE);
    expect(r.note).toBe("next-open fill book; v1 production profile was signal-close");
  });

  it("non-v1 strategies carry no fill-book note", () => {
    const r = assessProductionComparable({
      symbols: ["BTCUSDT"],
      timeframes: ["1h"],
      direction: "SHORT",
      researchRiskOverride: true,
      leverage: 2,
      takerFeePct: 0.04,
      makerFeePct: 0.02,
      strategyId: "COMBO_02_V2",
    });
    expect(r.reasons).not.toContain("next_open_fill_book");
    expect(r.note).toBeNull();
  });

  it("risk override / 4h setup is research-only", () => {
    expect(
      assessProductionComparable({
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        direction: "LONG",
        researchRiskOverride: true,
        leverage: 2,
        takerFeePct: 0.04,
        makerFeePct: 0.02,
      }).productionComparable
    ).toBe(false);
    expect(
      assessProductionComparable({
        symbols: ["BTCUSDT"],
        timeframes: ["4h"],
        direction: "LONG",
        researchRiskOverride: false,
        leverage: 2,
        takerFeePct: 0.04,
        makerFeePct: 0.02,
      }).reasons
    ).toContain("setup_timeframe_mismatch");
  });
});

const CAP_BOOK_RELATIVE = join("backend", "app", "research", "passed_cap_book.py");

/** Walks up from this test file, then from the vitest cwd, to the repo root. */
function readBackendCapBook(): string {
  const starts = [dirname(fileURLToPath(import.meta.url)), process.cwd()];
  for (const start of starts) {
    let dir = start;
    for (;;) {
      const candidate = join(dir, CAP_BOOK_RELATIVE);
      if (existsSync(candidate)) return readFileSync(candidate, "utf8");
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  throw new Error(`${CAP_BOOK_RELATIVE} not found above ${starts.join(" or ")}`);
}

function parsePythonTuple(source: string, name: string): string[] {
  const match = new RegExp(`^${name}\\s*=\\s*\\(([^)]*)\\)`, "m").exec(source);
  if (!match) throw new Error(`${name} = (...) not found in passed_cap_book.py`);
  return [...match[1].matchAll(/["']([^"']+)["']/g)].map((m) => m[1]);
}

describe("v1Config working strategy order", () => {
  it("lists COMBO_02_V1 first as the only production candidate", () => {
    expect(BACKTEST_STRATEGY_OPTIONS[0].id).toBe("COMBO_02_V1");
    expect(BACKTEST_STRATEGY_OPTIONS[0].status).toBe("Production candidate");
    expect(
      BACKTEST_STRATEGY_OPTIONS.filter((o) => o.status === "Production candidate").map((o) => o.id)
    ).toEqual(["COMBO_02_V1"]);
  });

  it("lists every research id after COMBO_02_V1", () => {
    const rest = BACKTEST_STRATEGY_OPTIONS.slice(1);
    expect(rest.length).toBeGreaterThan(0);
    for (const opt of rest) {
      expect(`${opt.id}:${opt.status}`).toBe(`${opt.id}:Research`);
    }
  });

  it("does not mark a passed strategy as working", () => {
    expect(PASSED_STRATEGY_IDS.length).toBeGreaterThan(0);
    expect(PASSED_STRATEGY_IDS).not.toContain("COMBO_02_V1");
    for (const id of PASSED_STRATEGY_IDS) {
      const opt = BACKTEST_STRATEGY_OPTIONS.find((o) => o.id === id);
      expect(opt, id).toBeDefined();
      expect(`${id}:${opt?.status}`).toBe(`${id}:Research`);
    }
  });
});

describe("v1Config trade books", () => {
  it("puts the two-sided COMBO_02_V2 family only in the hedge book", () => {
    for (const book of ["long", "short"] as const) {
      expect(strategyIdsForBook(book)).not.toContain("COMBO_02_V2");
      expect(strategyIdsForBook(book)).not.toContain("COMBO_02_V2_1B");
      expect(passedIdsForBook(book)).not.toContain("COMBO_02_V2");
      expect(passedIdsForBook(book)).not.toContain("COMBO_02_V2_1B");
    }
    expect([...strategyIdsForBook("hedge")]).toEqual(["COMBO_02_V2_1_A", "COMBO_02_V2", "COMBO_02_V2_1B"]);
    expect([...passedIdsForBook("hedge")]).toEqual(["COMBO_02_V2_1_A"]);
  });
});

describe("v1Config backtest cap book", () => {
  it("matches LARGE and MID in backend passed_cap_book.py, order included", () => {
    const source = readBackendCapBook();
    const large = parsePythonTuple(source, "LARGE");
    const mid = parsePythonTuple(source, "MID");
    expect(large.length).toBe(14);
    expect(mid.length).toBe(13);
    expect([...BACKTEST_LARGE_SYMBOLS]).toEqual(large);
    expect([...BACKTEST_MID_SYMBOLS]).toEqual(mid);
    expect([...BACKTEST_CAP_SYMBOLS]).toEqual([...large, ...mid]);
  });

  it("has 14 large and 13 mid symbols with no overlap and no duplicates", () => {
    expect(BACKTEST_LARGE_SYMBOLS.length).toBe(14);
    expect(BACKTEST_MID_SYMBOLS.length).toBe(13);
    expect(BACKTEST_CAP_SYMBOLS.length).toBe(27);
    expect(new Set<string>(BACKTEST_CAP_SYMBOLS).size).toBe(27);
    const mid = new Set<string>(BACKTEST_MID_SYMBOLS);
    expect(BACKTEST_LARGE_SYMBOLS.filter((s) => mid.has(s))).toEqual([]);
    expect([...BACKTEST_CAP_SYMBOLS]).toEqual([...BACKTEST_LARGE_SYMBOLS, ...BACKTEST_MID_SYMBOLS]);
  });

  it("accepts every large and mid symbol", () => {
    expect(BACKTEST_CAP_SYMBOLS.filter((s) => !isBacktestCapSymbol(s))).toEqual([]);
    expect(isBacktestCapSymbol(" injusdt ")).toBe(true);
    expect(isBacktestCapSymbol("btcusdt")).toBe(true);
  });

  it("rejects a symbol outside both lists", () => {
    for (const sym of ["WIFUSDT", "PEPEUSDT", "", "USDT", "INJ", "BTCUSDC", "wifusdt"]) {
      expect(`${sym}:${isBacktestCapSymbol(sym)}`).toBe(`${sym}:false`);
    }
  });

  it("does not treat an unknown symbol as mid cap", () => {
    // Backend cap_group() returns "mid" for anything outside LARGE. Not copied here.
    expect(BACKTEST_LARGE_SYMBOLS as readonly string[]).not.toContain("WIFUSDT");
    expect(BACKTEST_MID_SYMBOLS as readonly string[]).not.toContain("WIFUSDT");
    expect(isBacktestCapSymbol("WIFUSDT")).toBe(false);
    expect(filterBacktestCapSymbols(["WIFUSDT"])).toEqual({ allowed: [], rejected: ["WIFUSDT"] });
  });

  it("splits a mixed, lower-case, duplicated list in input order", () => {
    expect(
      filterBacktestCapSymbols([
        "wifusdt",
        "injusdt",
        " BTCUSDT ",
        "INJUSDT",
        "pepeusdt",
        "btcusdt",
        "WIFUSDT",
        "",
        "renderusdt",
      ])
    ).toEqual({
      allowed: ["INJUSDT", "BTCUSDT", "RENDERUSDT"],
      rejected: ["WIFUSDT", "PEPEUSDT"],
    });
    expect(filterBacktestCapSymbols([])).toEqual({ allowed: [], rejected: [] });
  });
});
