import { describe, expect, it } from "vitest";
import {
  buildTradePlan,
  classifyConditions,
  computePositionSize,
  derivePlanState,
  pickTarget,
  statusMessageFor,
} from "./buildTradePlan";

function cond(id: string, verdict: string, label?: string) {
  return { id, label: label || id, verdict, detail: "" };
}

const hardPass = [
  cond("mtf", "PASS"),
  cond("trend", "PASS"),
  cond("bos", "PASS"),
  cond("impulse", "PASS"),
  cond("pullback", "PASS"),
  cond("structure", "PASS"),
  cond("risk", "PASS"),
  cond("targets", "PASS"),
  cond("rr", "PASS"),
];

describe("derivePlanState", () => {
  it("1. WAITING", () => {
    expect(
      derivePlanState({ setupStatus: "WAITING", marketSignal: "BUY", conditions: [] }),
    ).toBe("WAITING");
  });

  it("2. NO_SETUP", () => {
    expect(
      derivePlanState({ setupStatus: "NO_SETUP", marketSignal: "NEUTRAL", conditions: [] }),
    ).toBe("NO_SETUP");
  });

  it("3. BUY + NO_SETUP → BUY_BIAS", () => {
    expect(
      derivePlanState({ setupStatus: "NO_SETUP", marketSignal: "BUY", conditions: [] }),
    ).toBe("BUY_BIAS");
    expect(
      derivePlanState({ setupStatus: "NO_SETUP", marketSignal: "STRONG_BUY", conditions: [] }),
    ).toBe("BUY_BIAS");
  });

  it("4. BUY + LONG_ENTRY_CANDIDATE", () => {
    const state = derivePlanState({
      setupStatus: "LONG_ENTRY_CANDIDATE",
      marketSignal: "BUY",
      conditions: [...hardPass, cond("retest", "WAITING")],
    });
    expect(state).toBe("LONG_ENTRY_CANDIDATE");
  });

  it("5. BUY + ENTRY_READY when hard PASS and retest PASS", () => {
    const state = derivePlanState({
      setupStatus: "LONG_ENTRY_CANDIDATE",
      marketSignal: "BUY",
      conditions: [...hardPass, cond("retest", "PASS")],
    });
    expect(state).toBe("ENTRY_READY");
  });

  it("6. SELL + SHORT_ENTRY_CANDIDATE", () => {
    expect(
      derivePlanState({
        setupStatus: "SHORT_ENTRY_CANDIDATE",
        marketSignal: "SELL",
        conditions: [...hardPass, cond("retest", "WAITING")],
      }),
    ).toBe("SHORT_ENTRY_CANDIDATE");
  });

  it("7. CONFLICT", () => {
    expect(
      derivePlanState({ setupStatus: "CONFLICT", marketSignal: "NEUTRAL", conditions: [] }),
    ).toBe("CONFLICT");
    expect(statusMessageFor("CONFLICT", { conflictDetail: "4H: BULLISH · 15M: BEARISH" })).toContain(
      "CONFLICT — WAIT",
    );
  });

  it("8. INVALIDATED", () => {
    expect(
      derivePlanState({ setupStatus: "INVALIDATED", marketSignal: "BUY", conditions: [] }),
    ).toBe("INVALIDATED");
  });
});

describe("missing soft data", () => {
  it("9. Missing OI → N/A not FAIL", () => {
    const list = classifyConditions([cond("oi", "FAIL", "OI")]);
    expect(list[0].verdict).toBe("N/A");
    expect(list[0].bucket).toBe("na");
  });

  it("10. Missing liquidation → N/A not FAIL", () => {
    const list = classifyConditions([cond("liquidation", "WAITING", "Liquidation")]);
    expect(list[0].verdict).toBe("N/A");
    expect(list[0].bucket).toBe("na");
  });

  it("11. Missing OHLCV → WAITING message", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: {
        analysis: { status: "WAITING", timeframe: "15m" },
        setup_state: { status: "WAITING" },
        data_dependencies: { ohlcv_15m: "WAITING" },
        trade_plan: { entry: {}, stop: {}, targets: [], risk_reward: {} },
        conditions: [],
      },
    });
    expect(plan.planState).toBe("WAITING");
    expect(plan.statusMessage).toMatch(/WAITING FOR 15M OHLCV/i);
    expect(plan.marketSignalDisplay).not.toMatch(/BUY NOW|SELL NOW/);
  });
});

describe("levels from backend only", () => {
  it("12. Entry/SL/TP rendering uses payload values; missing TP3 is —", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: {
        market_signal: "BUY",
        setup_state: { status: "LONG_ENTRY_CANDIDATE" },
        analysis: { status: "LONG_ENTRY_CANDIDATE", timeframe: "15m", direction: "LONG" },
        trade_plan: {
          entry: {
            status: "LONG_ENTRY_CANDIDATE",
            direction: "LONG",
            entry_price: 83800,
          },
          stop: {
            final_stop: 83250,
            risk_per_unit: 550,
            invalidation_reason: "15M close breaks below structural low",
          },
          targets: [
            { name: "TP1", target_price: 84350, r_multiple: 1.0 },
            { name: "TP2", target_price: 84900, r_multiple: 2.0 },
          ],
          risk_reward: { TP1_R: 1.0, TP2_R: 2.0, TP3_R: null },
        },
        conditions: [...hardPass, cond("retest", "WAITING")],
      },
    });
    expect(plan.entry.price).toBe(83800);
    expect(plan.stop.price).toBe(83250);
    expect(plan.targets[0].price).toBe(84350);
    expect(plan.targets[1].price).toBe(84900);
    expect(plan.targets[2].display).toBe("—");
    expect(plan.invalidation).toContain("structural low");
    expect(pickTarget([], "TP3").display).toBe("—");
  });

  it("15. No frontend-created signal values — sourceOnly and prices match input", () => {
    const entry = 100;
    const sl = 90;
    const plan = buildTradePlan({
      symbol: "ETHUSDT",
      setupTab: {
        market_signal: "SELL",
        setup_state: { status: "NO_SETUP" },
        analysis: { status: "NO_SETUP", timeframe: "1h" },
        trade_plan: {
          entry: { entry_price: entry },
          stop: { final_stop: sl },
          targets: [],
          risk_reward: {},
        },
        conditions: [],
      },
    });
    expect(plan.sourceOnly).toBe(true);
    expect(plan.planState).toBe("SELL_BIAS");
    expect(plan.entry.price).toBe(entry);
    expect(plan.stop.price).toBe(sl);
    // UI must not invent TP
    expect(plan.targets.every((t) => t.price == null)).toBe(true);
  });
});

describe("position sizing & R:R", () => {
  it("13. Position sizing calculation", () => {
    const s = computePositionSize({
      accountSize: 500000,
      riskPercent: 0.5,
      entry: 83800,
      stop: 83250,
    });
    expect(s.maxRisk).toBe(2500);
    expect(s.riskPerUnit).toBe(550);
    expect(s.positionSize).toBeCloseTo(2500 / 550, 6);
    expect(s.formula).toBe("2500 / 550");
    expect(s.valid).toBe(true);
  });

  it("14. R:R calculation display from backend multiples", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: {
        market_signal: "BUY",
        setup_state: { status: "LONG_ENTRY_CANDIDATE" },
        analysis: { status: "LONG_ENTRY_CANDIDATE", timeframe: "15m" },
        trade_plan: {
          entry: { entry_price: 100, direction: "LONG" },
          stop: { final_stop: 90 },
          targets: [
            { name: "TP1", target_price: 110, r_multiple: 1 },
            { name: "TP2", target_price: 120, r_multiple: 2 },
            { name: "TP3", target_price: 133, r_multiple: 3.3 },
          ],
          risk_reward: { TP1_R: 1, TP2_R: 2, TP3_R: 3.3 },
        },
        conditions: [...hardPass, cond("retest", "PASS")],
      },
    });
    expect(plan.planState).toBe("ENTRY_READY");
    expect(plan.rrLines).toEqual(["TP1 1.0R", "TP2 2.0R", "TP3 3.3R"]);
  });
});

describe("WAITING without entry", () => {
  it("null entry_price is not shown as 0; provisional stop not sized", () => {
    const plan = buildTradePlan({
      symbol: "LTCUSDT",
      setupTab: {
        market_signal: "NEUTRAL",
        setup_state: { status: "WAITING" },
        analysis: { status: "WAITING", timeframe: "15m", direction: "LONG" },
        trade_plan: {
          entry: { status: "WAITING", direction: "LONG", entry_price: null },
          stop: {
            final_stop: 69.76661736,
            risk_per_unit: 0.55,
            provisional: true,
            invalidation_reason: "Close below structural long stop",
          },
          targets: [
            { name: "TP1", target_price: 71.42, r_multiple: 2 },
            { name: "TP2", target_price: 71.98, r_multiple: 3 },
            { name: "TP3", target_price: 72.53, r_multiple: 4 },
          ],
          risk_reward: { TP1_R: 2, TP2_R: 3, TP3_R: 4 },
        },
        conditions: [
          cond("mtf", "FAIL"),
          cond("trend", "PASS"),
          cond("bos", "PASS"),
          cond("impulse", "PASS"),
          cond("pullback", "WAITING"),
          cond("structure", "WAITING"),
          cond("risk", "WAITING"),
          cond("targets", "WAITING"),
          cond("rr", "WAITING"),
        ],
        market_signal_conditions: {
          mtf_alignment: "PASS",
          htf_trend: "FAIL",
          primary_trend: "FAIL",
          choch: "N/A",
          supply_demand: "N/A",
        },
        data_dependencies: { Liquidations: "WAITING", "15m": "LIVE" },
      },
    });
    expect(plan.entry.price).toBeNull();
    expect(plan.entry.display).toBe("—");
    expect(plan.stop.price).toBeNull();
    expect(plan.riskPerUnit).toBeNull();
    expect(plan.direction).toBeNull();
    expect(plan.statusMessage).not.toMatch(/Liquidations/i);
    // Entry-engine mtf wins; market mtf_alignment must not duplicate as ✓
    expect(plan.confirmations.some((c) => c.id === "mtf_alignment")).toBe(false);
    expect(plan.failures.some((c) => c.id === "mtf")).toBe(true);
    expect(plan.dataNotes.some((n) => /provisional/i.test(n))).toBe(true);
    const size = computePositionSize({
      accountSize: 500000,
      riskPercent: 0.5,
      entry: plan.entry.price,
      stop: plan.stop.price,
    });
    expect(size.valid).toBe(false);
  });

  it("dedupes mtf vs mtf_alignment", () => {
    const list = classifyConditions(
      [cond("mtf", "FAIL", "MTF")],
      { mtf_alignment: "PASS", choch: "N/A" },
    );
    expect(list.filter((c) => c.id === "mtf" || c.id === "mtf_alignment")).toHaveLength(1);
    expect(list.find((c) => c.id === "mtf")?.verdict).toBe("FAIL");
    expect(list.find((c) => c.id === "choch")?.detail).toMatch(/not confirmed/i);
  });
});

describe("semantic distinction", () => {
  it("MARKET SIGNAL BUY with NO_SETUP stays BUY_BIAS not ENTRY_READY", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: {
        market_signal: "BUY",
        setup_state: { status: "NO_SETUP" },
        analysis: { status: "NO_SETUP", timeframe: "15m" },
        trade_plan: { entry: {}, stop: {}, targets: [], risk_reward: {} },
        conditions: [cond("bos", "FAIL")],
      },
    });
    expect(plan.marketSignalDisplay).toBe("BUY");
    expect(plan.planState).toBe("BUY_BIAS");
    expect(plan.statusMessage).toMatch(/NO DEFINED ENTRY/i);
  });

  it("does not emit BUY NOW / SELL NOW", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: {
        market_signal: "STRONG_BUY",
        setup_state: { status: "LONG_ENTRY_CANDIDATE" },
        analysis: { status: "LONG_ENTRY_CANDIDATE", timeframe: "15m" },
        trade_plan: {
          entry: { entry_price: 1, direction: "LONG" },
          stop: { final_stop: 0.9 },
          targets: [{ name: "TP1", target_price: 1.2, r_multiple: 2 }],
          risk_reward: { TP1_R: 2 },
        },
        conditions: [...hardPass, cond("retest", "PASS")],
      },
    });
    expect(plan.statusMessage).not.toMatch(/BUY NOW|SELL NOW/);
    expect(plan.planState).toBe("ENTRY_READY");
  });
});
