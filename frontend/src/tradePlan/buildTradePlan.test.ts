import { describe, expect, it } from "vitest";
import {
  buildDataAvailable,
  buildMtfAlignment,
  buildTradePlan,
  classifyConditions,
  computePositionSize,
  derivePlanState,
  evidenceBucketFor,
  pickTarget,
  statusMessageFor,
} from "./buildTradePlan";

function cond(id: string, verdict: string, detail = "", label?: string) {
  return { id, label: label || id, verdict, detail };
}

const hardPass = [
  cond("mtf", "PASS", "STRONG_LONG"),
  cond("trend", "PASS", "BULLISH"),
  cond("bos", "PASS", "BULLISH_BOS"),
  cond("impulse", "PASS", "MODERATE"),
  cond("pullback", "PASS", "CONFIRMED"),
  cond("structure", "PASS", "intact"),
  cond("risk", "PASS"),
  cond("targets", "PASS"),
  cond("rr", "PASS"),
];

function conflictPayload(overrides: Record<string, unknown> = {}) {
  return {
    market_signal: "NEUTRAL",
    setup_state: {
      status: "CONFLICT",
      pullback: { pullback_state: "WAITING", reason: "Waiting for valid impulse after BOS" },
      retest: { retest: false, state: "WAITING", reason: "Waiting for pullback" },
    },
    analysis: { status: "CONFLICT", timeframe: "15m" },
    mtf: {
      MTF_ALIGNMENT: "MIXED",
      trends: { "4h": "BULLISH", "1h": "BULLISH", "15m": "BEARISH", "5m": "BEARISH" },
      roles: { "4h": "major", "1h": "primary", "15m": "setup", "5m": "entry" },
      reason: "Mixed / incomplete MTF picture",
    },
    trend: { "4h": "BULLISH", "1h": "BULLISH", "15m": "BEARISH", "5m": "BEARISH" },
    trade_plan: {
      entry: { status: "CONFLICT", entry_price: null, direction: null },
      stop: {
        final_stop: 84681.21209062,
        provisional: true,
        invalidation_reason: "Close above structural short stop / supply invalidation",
      },
      targets: [],
      risk_reward: {},
    },
    conditions: [
      cond("mtf", "PASS", "MIXED"),
      cond("trend", "PASS", "BEARISH"),
      cond("bos", "PASS", "BEARISH_BOS"),
      cond("impulse", "PASS", "MODERATE"),
      cond("pullback", "WAITING"),
      cond("retest", "WAITING"),
      cond("volume", "PASS"),
      cond("oi", "PASS", "available"),
      cond("liquidation", "PASS", "available"),
      cond("structure", "WAITING", "awaiting pullback"),
      cond("risk", "WAITING", "awaiting pullback"),
      cond("targets", "WAITING", "awaiting pullback"),
      cond("rr", "WAITING", "awaiting pullback"),
    ],
    market_signal_conditions: {
      htf_trend: "PASS",
      primary_trend: "PASS",
      mtf_alignment: "FAIL",
      choch: "N/A",
      supply_demand: "N/A",
      oi: "N/A",
      liquidation: "N/A",
      entry_setup: "FAIL",
    },
    data_dependencies: {
      "4h": "LIVE",
      "1h": "LIVE",
      "15m": "LIVE",
      "5m": "LIVE",
      OI: "LIVE",
      Liquidations: "LIVE",
    },
    ...overrides,
  };
}

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

  it("7. CONFLICT status message is CONFLICT — WAIT", () => {
    expect(
      derivePlanState({ setupStatus: "CONFLICT", marketSignal: "NEUTRAL", conditions: [] }),
    ).toBe("CONFLICT");
    expect(statusMessageFor("CONFLICT")).toBe("CONFLICT — WAIT");
  });

  it("8. INVALIDATED", () => {
    expect(
      derivePlanState({ setupStatus: "INVALIDATED", marketSignal: "BUY", conditions: [] }),
    ).toBe("INVALIDATED");
  });
});

describe("evidence categorization", () => {
  it("OI available is data bucket, not directional", () => {
    expect(
      evidenceBucketFor({ id: "oi", verdict: "PASS", detail: "available" }),
    ).toBe("data");
  });

  it("liquidation available is data bucket, not directional", () => {
    expect(
      evidenceBucketFor({ id: "liquidation", verdict: "PASS", detail: "available" }),
    ).toBe("data");
  });

  it("trend PASS is directional", () => {
    expect(evidenceBucketFor({ id: "trend", verdict: "PASS", detail: "BEARISH" })).toBe(
      "directional",
    );
  });

  it("pullback WAITING is missing", () => {
    expect(evidenceBucketFor({ id: "pullback", verdict: "WAITING" })).toBe("missing");
  });

  it("choch N/A is na not fail", () => {
    expect(evidenceBucketFor({ id: "choch", verdict: "N/A" })).toBe("na");
  });

  it("9. Missing OI → N/A not FAIL", () => {
    const list = classifyConditions([cond("oi", "FAIL", "", "OI")]);
    expect(list[0].verdict).toBe("N/A");
    expect(evidenceBucketFor(list[0])).toBe("na");
  });

  it("10. Missing liquidation → N/A not FAIL", () => {
    const list = classifyConditions([cond("liquidation", "WAITING", "", "Liquidation")]);
    expect(list[0].verdict).toBe("N/A");
  });
});

describe("CASE 1 — MTF conflict BTC-style", () => {
  it("MIXED MTF, CONFLICT setup, no entry/SL/TP/size; OI under data available", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: conflictPayload(),
    });
    expect(plan.marketSignalDisplay).toBe("NEUTRAL");
    expect(plan.planState).toBe("CONFLICT");
    expect(plan.statusMessage).toBe("CONFLICT — WAIT");
    expect(plan.mtfAlignment.display).toBe("MIXED");
    expect(plan.mtfAlignment.tone).toBe("mixed");
    expect(plan.mtfAlignment.summaryLines).toEqual([
      "4H BULLISH",
      "1H BULLISH",
      "15M BEARISH",
      "5M BEARISH",
    ]);
    expect(plan.mtfAlignment.conflictNote).toMatch(/bullish vs.*bearish/i);
    expect(plan.entry.display).toBe("—");
    expect(plan.stop.display).toBe("—");
    expect(plan.targets.every((t) => t.display === "—")).toBe(true);
    expect(plan.levelsActionable).toBe(false);
    expect(plan.directionalEvidence.map((c) => c.id)).toEqual(
      expect.arrayContaining(["trend", "bos", "impulse", "volume"]),
    );
    expect(plan.directionalEvidence.some((c) => c.id === "oi")).toBe(false);
    expect(plan.directionalEvidence.some((c) => c.id === "liquidation")).toBe(false);
    expect(plan.dataAvailable).toEqual(
      expect.arrayContaining([
        { id: "oi", label: "OI", status: "LIVE" },
        { id: "liquidation", label: "Liquidation", status: "LIVE" },
      ]),
    );
    expect(plan.missingEntryConfirmations.map((c) => c.id)).toEqual(
      expect.arrayContaining(["pullback", "retest", "structure", "risk", "targets", "rr"]),
    );
    expect(plan.failedEntryGates.some((c) => c.id === "entry_setup")).toBe(true);
    expect(plan.unavailableNotConfirmed.some((c) => c.id === "choch")).toBe(true);
    expect(plan.unavailableNotConfirmed.some((c) => c.id === "supply_demand")).toBe(true);
    expect(plan.failedEntryGates.some((c) => c.id === "choch")).toBe(false);
    expect(plan.provisionalInvalidation?.display).toMatch(/84,?681/);
    expect(plan.stop.price).toBeNull();
    expect(plan.lifecycleStep).toBe("SETUP");
    expect(plan.lifecycleWaitingNote).toMatch(/WAITING FOR CONFIRMATION/i);
    expect(plan.currentStage).toBe("SETUP / WAIT");
    const size = computePositionSize({
      accountSize: 500000,
      riskPercent: 0.5,
      entry: plan.entry.price,
      stop: plan.stop.price,
    });
    expect(size.valid).toBe(false);
    expect(size.error).toMatch(/Entry\/SL unavailable/i);
  });
});

describe("CASE 2 / 3 — aligned MTF", () => {
  it("all BULLISH → ALIGNED BULLISH", () => {
    const mtf = buildMtfAlignment({
      mtf: {
        MTF_ALIGNMENT: "STRONG_LONG",
        trends: { "4h": "BULLISH", "1h": "BULLISH", "15m": "BULLISH", "5m": "BULLISH" },
        roles: { "4h": "major", "1h": "primary", "15m": "setup", "5m": "entry" },
      },
      trend: {},
      planState: "BUY_BIAS",
    });
    expect(mtf.display).toBe("ALIGNED BULLISH");
    expect(mtf.tone).toBe("aligned");
  });

  it("all BEARISH → ALIGNED BEARISH", () => {
    const mtf = buildMtfAlignment({
      mtf: {
        MTF_ALIGNMENT: "STRONG_SHORT",
        trends: { "4h": "BEARISH", "1h": "BEARISH", "15m": "BEARISH", "5m": "BEARISH" },
        roles: { "4h": "major", "1h": "primary", "15m": "setup", "5m": "entry" },
      },
      trend: {},
      planState: "SELL_BIAS",
    });
    expect(mtf.display).toBe("ALIGNED BEARISH");
    expect(mtf.tone).toBe("aligned");
  });
});

describe("CASE 4 / 5 — availability ≠ directional", () => {
  it("OI available appears under DATA AVAILABLE only", () => {
    const items = buildDataAvailable({
      conditions: classifyConditions([cond("oi", "PASS", "available", "OI")]),
      deps: { OI: "LIVE" },
    });
    expect(items).toEqual([{ id: "oi", label: "OI", status: "LIVE" }]);
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: conflictPayload({
        conditions: [cond("oi", "PASS", "available"), cond("trend", "PASS", "BEARISH")],
        market_signal_conditions: { oi: "N/A" },
      }),
    });
    expect(plan.dataAvailable.some((d) => d.id === "oi")).toBe(true);
    expect(plan.directionalEvidence.some((c) => c.id === "oi")).toBe(false);
    expect(plan.confirmations.some((c) => c.id === "oi")).toBe(false);
  });

  it("Liquidation available appears under DATA AVAILABLE only", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: conflictPayload({
        conditions: [
          cond("liquidation", "PASS", "available"),
          cond("trend", "PASS", "BEARISH"),
        ],
      }),
    });
    expect(plan.dataAvailable.some((d) => d.id === "liquidation" && d.status === "LIVE")).toBe(
      true,
    );
    expect(plan.directionalEvidence.some((c) => c.id === "liquidation")).toBe(false);
  });
});

describe("CASE 6 / 7 — missing vs N/A", () => {
  it("Pullback missing under MISSING ENTRY CONFIRMATIONS", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: conflictPayload(),
    });
    const pb = plan.missingEntryConfirmations.find((c) => c.id === "pullback");
    expect(pb).toBeTruthy();
    expect(pb?.detail).toMatch(/Waiting for valid/i);
  });

  it("CHOCH not confirmed under UNAVAILABLE / NOT FAILED", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: conflictPayload(),
    });
    expect(plan.unavailableNotConfirmed.some((c) => c.id === "choch")).toBe(true);
    expect(plan.failedEntryGates.some((c) => c.id === "choch")).toBe(false);
  });
});

describe("CASE 8 / 9 — levels + provisional stop", () => {
  it("Entry unavailable → Entry/SL/TP/R:R are —", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: conflictPayload(),
    });
    expect(plan.entry.display).toBe("—");
    expect(plan.stop.display).toBe("—");
    expect(plan.rrLines).toEqual([]);
  });

  it("Provisional structural stop is not active SL; size unavailable", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: conflictPayload(),
    });
    expect(plan.provisionalInvalidation).not.toBeNull();
    expect(plan.stop.price).toBeNull();
    expect(
      computePositionSize({
        accountSize: 500000,
        riskPercent: 0.5,
        entry: plan.entry.price,
        stop: plan.stop.price,
      }).valid,
    ).toBe(false);
  });
});

describe("CASE 10 / 11 — market signal ≠ entry", () => {
  it("Market Signal BUY + Setup CONFLICT does not become ENTRY_READY", () => {
    const plan = buildTradePlan({
      symbol: "BTCUSDT",
      setupTab: conflictPayload({ market_signal: "BUY" }),
    });
    expect(plan.marketSignalDisplay).toBe("BUY");
    expect(plan.planState).toBe("CONFLICT");
    expect(plan.planState).not.toBe("ENTRY_READY");
    expect(plan.levelsActionable).toBe(false);
  });

  it("Market Signal SELL + Setup WAITING does not auto-enter", () => {
    const plan = buildTradePlan({
      symbol: "ETHUSDT",
      setupTab: {
        market_signal: "SELL",
        setup_state: { status: "WAITING" },
        analysis: { status: "WAITING", timeframe: "15m" },
        trade_plan: { entry: { entry_price: null }, stop: {}, targets: [], risk_reward: {} },
        conditions: [],
        data_dependencies: { "15m": "WAITING FOR OHLCV" },
      },
    });
    expect(plan.marketSignalDisplay).toBe("SELL");
    expect(plan.planState).toBe("WAITING");
    expect(plan.entry.display).toBe("—");
    expect(plan.planState).not.toBe("ENTRY_READY");
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
          cond("trend", "PASS", "BULLISH"),
          cond("bos", "PASS", "BULLISH_BOS"),
          cond("impulse", "PASS", "MODERATE"),
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
    expect(plan.directionalEvidence.some((c) => c.id === "mtf_alignment")).toBe(false);
    // mtf FAIL is MTF-section, not failed entry gate list
    expect(plan.failedEntryGates.some((c) => c.id === "mtf")).toBe(false);
    expect(plan.provisionalInvalidation).not.toBeNull();
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
      [cond("mtf", "FAIL", "", "MTF")],
      { mtf_alignment: "PASS", choch: "N/A" },
    );
    expect(list.filter((c) => c.id === "mtf" || c.id === "mtf_alignment")).toHaveLength(1);
    expect(list.find((c) => c.id === "mtf")?.verdict).toBe("FAIL");
    expect(list.find((c) => c.id === "choch")?.detail).toMatch(/not confirmed/i);
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

describe("live-shape fixtures from API", () => {
  it("ETH-like WAITING + STALE OI stays non-directional", () => {
    const plan = buildTradePlan({
      symbol: "ETHUSDT",
      setupTab: {
        market_signal: "NEUTRAL",
        setup_state: { status: "WAITING" },
        analysis: { status: "WAITING", timeframe: "15m" },
        mtf: {
          MTF_ALIGNMENT: "MIXED",
          trends: { "4h": "NEUTRAL", "1h": "NEUTRAL", "15m": "BEARISH", "5m": "BEARISH" },
          roles: { "4h": "major", "1h": "primary", "15m": "setup", "5m": "entry" },
        },
        trade_plan: {
          entry: { entry_price: null },
          stop: { provisional: true, final_stop: 1 },
          targets: [],
          risk_reward: {},
        },
        conditions: [
          { id: "oi", label: "OI", verdict: "PASS", detail: "available" },
          { id: "liquidation", label: "Liquidation", verdict: "PASS", detail: "available" },
          { id: "pullback", label: "Pullback", verdict: "WAITING", detail: "" },
        ],
        data_dependencies: { OI: "STALE", Liquidations: "LIVE", "15m": "LIVE" },
      },
    });
    expect(plan.planState).toBe("WAITING");
    expect(plan.dataAvailable).toEqual(
      expect.arrayContaining([
        { id: "oi", label: "OI", status: "STALE" },
        { id: "liquidation", label: "Liquidation", status: "LIVE" },
      ]),
    );
    expect(plan.directionalEvidence.some((c) => c.id === "oi")).toBe(false);
    expect(plan.entry.display).toBe("—");
  });

  it("AVAX-like SELL + WAITING does not auto ENTRY_READY", () => {
    const plan = buildTradePlan({
      symbol: "AVAXUSDT",
      setupTab: {
        market_signal: "SELL",
        setup_state: { status: "WAITING" },
        analysis: { status: "WAITING", timeframe: "15m" },
        mtf: {
          MTF_ALIGNMENT: "MIXED",
          trends: { "4h": "BEARISH", "1h": "NEUTRAL", "15m": "BEARISH", "5m": "BEARISH" },
          roles: { "4h": "major", "1h": "primary", "15m": "setup", "5m": "entry" },
        },
        trade_plan: { entry: { entry_price: null }, stop: {}, targets: [], risk_reward: {} },
        conditions: [],
        data_dependencies: { OI: "LIVE", Liquidations: "LIVE" },
      },
    });
    expect(plan.marketSignalDisplay).toBe("SELL");
    expect(plan.planState).toBe("WAITING");
    expect(plan.planState).not.toBe("ENTRY_READY");
    expect(plan.entry.display).toBe("—");
  });
});

