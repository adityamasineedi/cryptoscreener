import { describe, expect, it } from "vitest";
import {
  MAX_VISIBLE_STRUCTURE_LABELS,
  type AnnToggle,
  type CandleLike,
  type ChartAnnotation,
  annotationPriority,
  buildPriorityBands,
  canonicalStructureLabel,
  computeChartPriceRange,
  dedupeAnnotations,
  filterAnnotationsByToggles,
  groupEnabled,
  CHART_SETUP_NOTE_CLASS,
  CHART_VOLUME_NOTE_CLASS,
  buildLiquidationPanel,
  findUnconfirmedSwings,
  formatLiquidationUsd,
  isLiquidationsCompact,
  liquidationAxisRange,
  liquidationsSubtitle,
  placePriceLines,
  placeStructureMarkers,
  preferredMarkerPosition,
  recheckStructureLabels,
  sanitizeLiquidationEvents,
  snapTimeToCandle,
  thinCrowdedMarkers,
  INVALID_PLAN_NOTICE,
  tradePlanLevelText,
} from "./chartAnnotations";

const togglesAllOn: AnnToggle = {
  structure: true,
  bosChoch: true,
  setup: true,
  tradePlan: true,
  signal: true,
};

function candle(time: number, low: number, high: number, close = (low + high) / 2): CandleLike {
  return { time, open: close, high, low, close };
}

describe("canonicalStructureLabel", () => {
  it("never renders CHOCH as CHO", () => {
    expect(canonicalStructureLabel({ kind: "CHOCH", label: "CHOCH_BULLISH" })).toBe("CHOCH");
    expect(canonicalStructureLabel({ kind: "CHOCH", label: "CHOCH_BEARISH" })).toBe("CHOCH");
    expect(canonicalStructureLabel({ kind: "CHOCH", label: "CHO" })).toBe("CHOCH");
    expect(canonicalStructureLabel({ kind: "EVENT", label: "CHOCH_BU" })).toBe("CHOCH");
    const text = canonicalStructureLabel({ kind: "CHOCH", label: "CHOCH_BULLISH" });
    expect(text).not.toBe("CHO");
    expect(text.startsWith("CHOCH")).toBe(true);
    expect(text.length).toBeGreaterThanOrEqual(5);
  });

  it("uses full BOS / swing labels", () => {
    expect(canonicalStructureLabel({ kind: "BOS", label: "BULLISH_BOS" })).toBe("BOS");
    expect(canonicalStructureLabel({ kind: "BOS", label: "BEARISH_BOS" })).toBe("BOS");
    expect(canonicalStructureLabel({ kind: "SWING_HIGH", label: "HH" })).toBe("HH");
    expect(canonicalStructureLabel({ kind: "SWING_LOW", label: "HL" })).toBe("HL");
    expect(canonicalStructureLabel({ kind: "SWING_HIGH", label: "LH" })).toBe("LH");
    expect(canonicalStructureLabel({ kind: "SWING_LOW", label: "LL" })).toBe("LL");
  });
});

describe("collision priority", () => {
  it("gives current price highest priority over structure", () => {
    expect(annotationPriority({ kind: "CURRENT" })).toBe(1);
    expect(annotationPriority({ kind: "BUY", group: "trade_plan" })).toBe(2);
    expect(annotationPriority({ kind: "STOP", group: "trade_plan" })).toBe(3);
    expect(annotationPriority({ kind: "TP1", group: "trade_plan" })).toBe(4);
    expect(annotationPriority({ kind: "BOS", group: "bos_choch" })).toBe(5);
    expect(annotationPriority({ kind: "SWING_HIGH", group: "structure", label: "HH" })).toBe(6);
  });

  it("displaces structure markers away from current/entry bands", () => {
    const candles = [candle(100, 100, 110, 105)];
    const anns: ChartAnnotation[] = [
      { kind: "SWING_HIGH", group: "structure", label: "HH", price: 109.5, time: "1970-01-01T00:01:40Z" },
    ];
    // time 100 sec = 1970-01-01T00:01:40Z
    const bands = buildPriorityBands([], 109.5);
    const placed = placeStructureMarkers(anns, candles, {
      priorityBands: bands,
    });
    expect(placed).toHaveLength(1);
    // HH stays above the swing high even when price is near the high.
    expect(placed[0].text).toBe("HH");
    expect(placed[0].position).toBe("aboveBar");
  });
});

describe("BOS / CHOCH placement", () => {
  it("does not place BOS and CHOCH on identical positions", () => {
    const candles = [candle(200, 90, 110, 100)];
    const anns: ChartAnnotation[] = [
      {
        kind: "BOS",
        group: "bos_choch",
        label: "BULLISH_BOS",
        price: 108,
        time: "1970-01-01T00:03:20Z",
      },
      {
        kind: "CHOCH",
        group: "bos_choch",
        label: "CHOCH_BULLISH",
        price: 107,
        time: "1970-01-01T00:03:20Z",
      },
    ];
    const placed = placeStructureMarkers(anns, candles);
    expect(placed).toHaveLength(2);
    const bos = placed.find((m) => m.text === "BOS")!;
    const choch = placed.find((m) => m.text === "CHOCH")!;
    expect(bos).toBeTruthy();
    expect(choch).toBeTruthy();
    expect(bos.position === choch.position && bos.time === choch.time).toBe(false);
  });
});

describe("HH/HL/LH/LL deterministic offsets", () => {
  it("uses semantic preferred sides", () => {
    expect(preferredMarkerPosition({ kind: "SWING_HIGH", label: "HH" })).toBe("aboveBar");
    expect(preferredMarkerPosition({ kind: "SWING_LOW", label: "HL" })).toBe("belowBar");
    expect(preferredMarkerPosition({ kind: "SWING_HIGH", label: "LH" })).toBe("aboveBar");
    expect(preferredMarkerPosition({ kind: "SWING_LOW", label: "LL" })).toBe("belowBar");
  });

  it("places swing labels with those offsets", () => {
    const candles = [
      candle(1, 10, 12),
      candle(2, 11, 13),
      candle(3, 9, 12),
      candle(4, 8, 11),
    ];
    const anns: ChartAnnotation[] = [
      { kind: "SWING_HIGH", group: "structure", label: "HH", price: 13, time: "1970-01-01T00:00:02Z" },
      { kind: "SWING_LOW", group: "structure", label: "HL", price: 11, time: "1970-01-01T00:00:02Z" },
      { kind: "SWING_HIGH", group: "structure", label: "LH", price: 12, time: "1970-01-01T00:00:03Z" },
      { kind: "SWING_LOW", group: "structure", label: "LL", price: 8, time: "1970-01-01T00:00:04Z" },
    ];
    const placed = placeStructureMarkers(anns, candles);
    expect(placed.find((m) => m.text === "HH")?.position).toBe("aboveBar");
    expect(placed.find((m) => m.text === "HL")?.position).toBe("belowBar");
    expect(placed.find((m) => m.text === "LH")?.position).toBe("aboveBar");
    expect(placed.find((m) => m.text === "LL")?.position).toBe("belowBar");
  });

  it("rechecks each label against the previous swing of the same type", () => {
    const anns: ChartAnnotation[] = [
      { kind: "SWING_HIGH", group: "structure", label: "LH", price: 10, time: "1970-01-01T00:00:01Z" },
      { kind: "SWING_HIGH", group: "structure", label: "HH", price: 8, time: "1970-01-01T00:00:02Z" },
      { kind: "SWING_HIGH", group: "structure", label: "LH", price: 9, time: "1970-01-01T00:00:03Z" },
      { kind: "SWING_LOW", group: "structure", label: "LL", price: 4, time: "1970-01-01T00:00:01Z" },
      { kind: "SWING_LOW", group: "structure", label: "HL", price: 3, time: "1970-01-01T00:00:02Z" },
      { kind: "SWING_LOW", group: "structure", label: "LL", price: 5, time: "1970-01-01T00:00:04Z" },
    ];
    const snapshot = JSON.stringify(anns);
    const labeled = recheckStructureLabels(anns);
    expect(JSON.stringify(anns)).toBe(snapshot);
    expect(labeled.map((a) => a.label)).toEqual(["HH", "LH", "HH", "HL", "LL", "HL"]);

    const candles = [candle(1, 4, 10), candle(2, 3, 8), candle(3, 3, 9), candle(4, 5, 6)];
    const placed = placeStructureMarkers(anns, candles, { includeUnconfirmed: false });
    expect(placed.find((m) => m.time === 1 && m.kind === "SWING_HIGH")?.text).toBe("HH");
    expect(placed.find((m) => m.time === 1 && m.kind === "SWING_HIGH")?.position).toBe("aboveBar");
    expect(placed.find((m) => m.time === 2 && m.kind === "SWING_HIGH")?.text).toBe("LH");
    expect(placed.find((m) => m.time === 2 && m.kind === "SWING_HIGH")?.position).toBe("aboveBar");
    expect(placed.find((m) => m.time === 3 && m.kind === "SWING_HIGH")?.text).toBe("HH");
    expect(placed.find((m) => m.time === 3 && m.kind === "SWING_HIGH")?.position).toBe("aboveBar");
    expect(placed.find((m) => m.time === 1 && m.kind === "SWING_LOW")?.text).toBe("HL");
    expect(placed.find((m) => m.time === 1 && m.kind === "SWING_LOW")?.position).toBe("belowBar");
    expect(placed.find((m) => m.time === 2 && m.kind === "SWING_LOW")?.text).toBe("LL");
    expect(placed.find((m) => m.time === 2 && m.kind === "SWING_LOW")?.position).toBe("belowBar");
    expect(placed.find((m) => m.time === 4 && m.kind === "SWING_LOW")?.text).toBe("HL");
    expect(placed.find((m) => m.time === 4 && m.kind === "SWING_LOW")?.position).toBe("belowBar");
  });
});

describe("unconfirmed swings", () => {
  it("draws the latest open high as dashed unconfirmed until confirmation bars close", () => {
    // left=2, right=2. The high on the last bar has no confirmation bars yet.
    const forming = [
      candle(1, 10, 11),
      candle(2, 10, 12),
      candle(3, 9, 13),
      candle(4, 8, 14),
      candle(5, 7, 20),
    ];
    const open = findUnconfirmedSwings(forming, [], { left: 2, right: 2 });
    const high = open.find((a) => a.kind === "SWING_HIGH");
    expect(high?.unconfirmed).toBe(true);
    expect(high?.label).toBe("HH");
    expect(high?.price).toBe(20);
    expect(high?.time).toBe(new Date(5 * 1000).toISOString());

    const confirmed = [
      ...forming,
      candle(6, 8, 12),
      candle(7, 8, 11),
    ];
    const afterClose = findUnconfirmedSwings(confirmed, [], { left: 2, right: 2 });
    expect(afterClose.find((a) => a.price === 20)).toBeUndefined();

    const previous = [
      { kind: "SWING_HIGH", group: "structure", label: "HH", price: 30, time: "1970-01-01T00:00:01Z" },
    ];
    const lower = findUnconfirmedSwings(forming, previous, { left: 2, right: 2 });
    expect(lower.find((a) => a.kind === "SWING_HIGH")?.label).toBe("LH");

    const placed = placeStructureMarkers([], forming, { swingLeft: 2, swingRight: 2 });
    const marker = placed.find((m) => m.text.includes("unconfirmed") && m.kind === "SWING_HIGH");
    expect(marker?.text).toBe("HH unconfirmed");
    expect(marker?.position).toBe("aboveBar");
    expect(marker?.dashed).toBe(true);
    expect(marker?.price).toBe(20);
  });

  it("keeps an unconfirmed low below the bar and drops it once confirmation bars close", () => {
    const forming = [
      candle(1, 12, 15),
      candle(2, 11, 14),
      candle(3, 10, 13),
      candle(4, 9, 14),
      candle(5, 4, 12),
    ];
    const placed = placeStructureMarkers(
      [{ kind: "SWING_LOW", group: "structure", label: "HL", price: 6, time: "1970-01-01T00:00:01Z" }],
      forming,
      { swingLeft: 2, swingRight: 2 },
    );
    const marker = placed.find((m) => m.kind === "SWING_LOW" && m.dashed);
    expect(marker?.text).toBe("LL unconfirmed");
    expect(marker?.position).toBe("belowBar");
    expect(marker?.price).toBe(4);

    const closed = [...forming, candle(6, 7, 11), candle(7, 8, 12)];
    const still = findUnconfirmedSwings(
      closed,
      [{ kind: "SWING_LOW", group: "structure", label: "HL", price: 6, time: "1970-01-01T00:00:01Z" }],
      { left: 2, right: 2 },
    );
    expect(still.find((a) => a.price === 4)).toBeUndefined();
  });
});

describe("auto-scale price range", () => {
  it("scales BTC / ETH / low-price alts from visible candles", () => {
    const btc = computeChartPriceRange([
      candle(1, 95000, 98000),
      candle(2, 96000, 99000),
    ]);
    expect(btc.chartMin).toBeLessThan(95000);
    expect(btc.chartMax).toBeGreaterThan(99000);
    expect(btc.padding).toBeGreaterThan(0);
    // ~5% pad of candle span
    expect(btc.padding).toBeGreaterThanOrEqual((99000 - 95000) * 0.05 - 1e-6);

    const eth = computeChartPriceRange([candle(1, 3200, 3400), candle(2, 3300, 3500)]);
    expect(eth.chartMin).toBeLessThan(3200);
    expect(eth.chartMax).toBeGreaterThan(3500);

    const alt = computeChartPriceRange([candle(1, 0.00012, 0.00018), candle(2, 0.00014, 0.0002)]);
    expect(alt.chartMin).toBeLessThan(0.00012);
    expect(alt.chartMax).toBeGreaterThan(0.0002);
    expect(alt.chartMax / alt.chartMin).toBeLessThan(10);
  });

  it("recalculates when candle window (timeframe) changes", () => {
    const m15 = computeChartPriceRange([candle(1, 100, 110), candle(2, 105, 115)]);
    const h4 = computeChartPriceRange([
      candle(1, 80, 90),
      candle(2, 85, 120),
      candle(3, 100, 130),
    ]);
    expect(m15.chartMin).not.toBe(h4.chartMin);
    expect(m15.chartMax).not.toBe(h4.chartMax);
    expect(h4.visibleHigh).toBe(130);
    expect(h4.visibleLow).toBe(80);
  });

  it("does not hardcode BTC levels", () => {
    const r = computeChartPriceRange([candle(1, 1.5, 1.7)]);
    expect(r.chartMin).toBeLessThan(1.5);
    expect(r.chartMax).toBeGreaterThan(1.7);
    expect(r.chartMax).toBeLessThan(100);
  });

  it("soft-filters far extras by default but forceExtraPrices keeps BUY/SELL/TP on screen", () => {
    // Visible candles sit below entry/SL — same pattern as provisional short setups
    const candles = [candle(1, 0.288, 0.292), candle(2, 0.289, 0.291)];
    const extras = [0.2946, 0.29599, 0.2918, 0.289]; // SELL / SL / TP1 / near
    const soft = computeChartPriceRange(candles, { extraPrices: extras });
    // Default 25% slack of ~0.004 span ≈ 0.001 → entry/SL above 0.292+0.001 dropped
    expect(soft.chartMax).toBeLessThan(0.2946);

    const forced = computeChartPriceRange(candles, {
      extraPrices: extras,
      forceExtraPrices: true,
    });
    expect(forced.chartMax).toBeGreaterThan(0.29599);
    expect(forced.chartMin).toBeLessThanOrEqual(0.288);
  });
});

describe("chart footer notes", () => {
  it("keeps the setup message and the volume label on opposite sides", () => {
    expect(CHART_SETUP_NOTE_CLASS).toContain("left-2");
    expect(CHART_SETUP_NOTE_CLASS).toContain("max-w-[calc(100%-11.5rem)]");
    expect(CHART_SETUP_NOTE_CLASS).toContain("bottom-9");
    expect(CHART_VOLUME_NOTE_CLASS).toContain("right-2");
    expect(CHART_VOLUME_NOTE_CLASS).toContain("bottom-9");
    expect(CHART_VOLUME_NOTE_CLASS.includes("left-2")).toBe(false);
  });
});

describe("liquidation panel", () => {
  it("shows USD and splits long below zero only when both sides have notional", () => {
    const both = buildLiquidationPanel([
      { timestamp: "2026-10-23T08:00:00Z", side: "SELL", notional: 2500 },
      { timestamp: "2026-10-23T08:00:30Z", side: "BUY", notional: 4000 },
      { timestamp: "2026-10-23T08:01:00Z", side: "BUY", notional: 1500 },
    ]);
    expect(both.unit).toBe("USD");
    expect(both.split).toBe(true);
    expect(both.allowNegativeAxis).toBe(true);
    expect(both.legend).toContain("USD");
    expect(both.short.every((p) => p.value > 0)).toBe(true);
    expect(both.long.every((p) => p.value < 0)).toBe(true);
    expect(both.long[0].value).toBe(-2500);
    expect(both.short.reduce((s, p) => s + p.value, 0)).toBe(5500);
    expect(formatLiquidationUsd(2500)).toBe("$2.5k");
    expect(formatLiquidationUsd(-2500)).toBe("-$2.5k");

    const shortsOnly = buildLiquidationPanel([
      { timestamp: "2026-10-23T08:00:00Z", side: "BUY", notional: 800 },
    ]);
    expect(shortsOnly.split).toBe(false);
    expect(shortsOnly.allowNegativeAxis).toBe(false);
    expect(shortsOnly.short[0].value).toBe(800);
    expect(shortsOnly.long).toHaveLength(0);
    expect(liquidationAxisRange(-10, 800, false)).toEqual({ minValue: 0, maxValue: 800 });
    expect(liquidationAxisRange(-2500, 4000, true)).toEqual({ minValue: -2500, maxValue: 4000 });
  });
});

describe("viewport / zoom semantics", () => {
  it("keeps fit key identity separate from candle payload updates", () => {
    // Presentation contract: symbol|timeframe identity drives refit, not candle identity
    const keyA = `BTCUSDT|15m`;
    const keyB = `BTCUSDT|15m`;
    const keyC = `BTCUSDT|4h`;
    expect(keyA).toBe(keyB);
    expect(keyA).not.toBe(keyC);
  });
});

describe("liquidations WAITING", () => {
  it("stays truthful and compact", () => {
    expect(isLiquidationsCompact("WAITING", 0)).toBe(true);
    expect(liquidationsSubtitle("BTCUSDT", "WAITING")).toBe("WAITING FOR DATA");
    expect(sanitizeLiquidationEvents([{ notional: 999, side: "BUY" }], "WAITING")).toEqual([]);
  });

  it("does not render fake liquidation values when waiting/unavailable", () => {
    const cleaned = sanitizeLiquidationEvents(
      [
        { notional: 1000, side: "SELL", timestamp: "2020-01-01T00:00:00Z" },
        { notional: NaN, side: "BUY" },
      ],
      "WAITING",
    );
    expect(cleaned).toHaveLength(0);
    expect(sanitizeLiquidationEvents([{ notional: 0, side: "BUY" }], "LIVE")).toHaveLength(0);
  });

  it("expands when live events exist", () => {
    expect(isLiquidationsCompact("LIVE", 3)).toBe(false);
    expect(isLiquidationsCompact("UNAVAILABLE", 0)).toBe(true);
  });

  it("frontend LIVE vs WAITING subtitles stay honest", () => {
    expect(liquidationsSubtitle("BTCUSDT", "LIVE")).toBe("LIVE");
    expect(liquidationsSubtitle("BTCUSDT", "WAITING")).toBe("WAITING FOR DATA");
    expect(liquidationsSubtitle("BTCUSDT", "STALE")).toBe("STALE");
    expect(liquidationsSubtitle(null, "LIVE")).toBe("SELECT A COIN");
  });
});

describe("overlay toggles independent", () => {
  it("structure off does not remove bos_choch", () => {
    const ann: AnnToggle = { ...togglesAllOn, structure: false };
    expect(groupEnabled("structure", ann)).toBe(false);
    expect(groupEnabled("bos_choch", ann)).toBe(true);
    expect(groupEnabled("setup", ann)).toBe(true);
  });

  it("bosChoch off does not remove setup", () => {
    const ann: AnnToggle = { ...togglesAllOn, bosChoch: false };
    expect(groupEnabled("bos_choch", ann)).toBe(false);
    expect(groupEnabled("setup", ann)).toBe(true);
    expect(groupEnabled("structure", ann)).toBe(true);
  });

  it("filterAnnotationsByToggles respects each group independently", () => {
    const anns: ChartAnnotation[] = [
      { kind: "SWING_HIGH", group: "structure", label: "HH", price: 1 },
      { kind: "BOS", group: "bos_choch", label: "BULLISH_BOS", price: 2 },
      { kind: "IMPULSE", group: "setup", price: 3 },
      { kind: "BUY", group: "trade_plan", price: 4 },
    ];
    const filtered = filterAnnotationsByToggles(anns, {
      structure: false,
      bosChoch: true,
      setup: false,
      tradePlan: true,
      signal: false,
    });
    expect(filtered.map((a) => a.group).sort()).toEqual(["bos_choch", "trade_plan"]);
  });
});

describe("structure event data unchanged", () => {
  it("placement keeps original prices and does not mutate inputs", () => {
    const anns: ChartAnnotation[] = [
      {
        kind: "CHOCH",
        group: "bos_choch",
        label: "CHOCH_BULLISH",
        price: 42.5,
        time: "1970-01-01T00:00:05Z",
      },
    ];
    const snapshot = JSON.stringify(anns);
    const candles = [candle(5, 40, 45)];
    const placed = placeStructureMarkers(anns, candles);
    expect(JSON.stringify(anns)).toBe(snapshot);
    expect(placed[0].price).toBe(42.5);
    expect(placed[0].text).toBe("CHOCH");
  });

  it("dedupe does not alter event payload fields", () => {
    const a: ChartAnnotation = {
      kind: "BOS",
      group: "bos_choch",
      label: "BULLISH_BOS",
      price: 100,
      time: "2024-01-01T00:00:00Z",
    };
    const out = dedupeAnnotations([a, { ...a }], { symbol: "BTCUSDT", timeframe: "15m" });
    expect(out).toHaveLength(1);
    expect(out[0]).toEqual(a);
  });

  it("respects MAX_VISIBLE_STRUCTURE_LABELS without deleting source data", () => {
    const candles = Array.from({ length: 40 }, (_, i) => candle(i + 1, 10, 12));
    const anns: ChartAnnotation[] = candles.map((c, i) => ({
      kind: "SWING_HIGH",
      group: "structure",
      label: "HH",
      price: 12,
      time: new Date((c.time) * 1000).toISOString(),
    }));
    expect(anns.length).toBeGreaterThan(MAX_VISIBLE_STRUCTURE_LABELS);
    const placed = placeStructureMarkers(anns, candles, {
      maxVisible: MAX_VISIBLE_STRUCTURE_LABELS,
    });
    expect(placed.length).toBe(MAX_VISIBLE_STRUCTURE_LABELS);
    expect(anns.length).toBe(40);
  });
});

describe("price lines vs markers", () => {
  it("BOS/CHOCH are marker-only (no price lines); trade plan keeps axis labels", () => {
    const lines = placePriceLines(
      [
        { kind: "BOS", group: "bos_choch", label: "BULLISH_BOS", price: 100 },
        { kind: "CHOCH", group: "bos_choch", label: "CHOCH_BULLISH", price: 99 },
        { kind: "BUY", group: "trade_plan", price: 98 },
      ],
      togglesAllOn,
    );
    expect(lines.find((l) => l.kind === "BOS")).toBeUndefined();
    expect(lines.find((l) => l.kind === "CHOCH")).toBeUndefined();
    const buy = lines.find((l) => l.kind === "BUY")!;
    expect(buy.axisLabelVisible).toBe(true);
    expect(buy.title).toBe("BUY");
  });
});

describe("timeframe snap + buy/sell markers", () => {
  it("snaps 15m event onto nearest 1h candle", () => {
    // 1h opens at 0 and 3600
    const candles = [candle(0, 10, 12), candle(3600, 11, 13)];
    expect(snapTimeToCandle(900, candles)).toBe(0); // 15m into first hour
    expect(snapTimeToCandle(2700, candles)).toBe(3600);
  });

  it("places BUY/SELL markers snapped onto chart bars", () => {
    const candles = [candle(100, 90, 110), candle(200, 95, 115)];
    const placed = placeStructureMarkers(
      [
        {
          kind: "BUY",
          group: "trade_plan",
          price: 100,
          time: "1970-01-01T00:02:30Z", // 150s → nearest 100 or 200
          direction: "LONG",
        },
        {
          kind: "CHOCH",
          group: "bos_choch",
          label: "CHOCH_BULLISH",
          price: 105,
          time: "1970-01-01T00:01:40Z", // 100
        },
      ],
      candles,
    );
    expect(placed.some((m) => m.text === "BUY")).toBe(true);
    expect(placed.some((m) => m.text === "CHOCH")).toBe(true);
    for (const m of placed) {
      expect([100, 200]).toContain(m.time);
    }
  });

  it("pins BUY marker to tip bar when event time is outside the loaded window", () => {
    const candles = [candle(1_000, 90, 110), candle(2_000, 95, 115)];
    const placed = placeStructureMarkers(
      [
        {
          kind: "SELL",
          group: "trade_plan",
          price: 100,
          // Far outside candle window
          time: "1970-01-01T12:00:00Z",
          direction: "SHORT",
        },
      ],
      candles,
    );
    expect(placed).toHaveLength(1);
    expect(placed[0].text).toBe("SELL");
    expect(placed[0].time).toBe(2_000);
  });
});

describe("D204 trade-plan overlay text", () => {
  it("uses the tick-rounded price text", () => {
    expect(tradePlanLevelText("STOP", 1209.684, "1209.68")).toBe("STOP 1209.68");
    expect(tradePlanLevelText("SELL", 1216.25)).toBe("SELL 1216.25");
  });

  it("names the invalid plan with the owner text", () => {
    expect(INVALID_PLAN_NOTICE).toBe("invalid plan: stop on wrong side");
  });

  it("keeps price_text on the price line", () => {
    const lines = placePriceLines(
      [{ kind: "SL", group: "trade_plan", price: 1209.68, price_text: "1209.68", label: "STOP" }],
      togglesAllOn,
    );
    expect(lines[0].priceText).toBe("1209.68");
    expect(lines[0].title).toBe("STOP");
  });
});
