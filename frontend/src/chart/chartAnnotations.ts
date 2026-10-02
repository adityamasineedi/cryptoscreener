/**
 * Presentation-only helpers for Coin Detail chart overlays.
 * Does not alter signal/structure event data — only how it is rendered.
 */

export const MAX_VISIBLE_STRUCTURE_LABELS = 20;
export const CHART_RIGHT_OFFSET_BARS = 12;
export const STRUCTURE_PAD_PCT = 0.05;
/** Max structure/setup markers kept inside the most recent N bars. */
export const RECENT_BAR_LABEL_BUDGET = 5;

export type AnnGroup =
  | "structure"
  | "bos_choch"
  | "setup"
  | "trade_plan"
  | "signal";

export type ChartAnnotation = {
  kind?: string;
  group?: string;
  price?: number | null;
  time?: string | null;
  label?: string | null;
  direction?: string | null;
};

export type AnnToggle = {
  structure: boolean;
  bosChoch: boolean;
  setup: boolean;
  tradePlan: boolean;
  signal: boolean;
};

export type CandleLike = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type MarkerPosition = "aboveBar" | "belowBar";

export type PlacedMarker = {
  time: number;
  position: MarkerPosition;
  color: string;
  shape: "circle" | "arrowUp" | "arrowDown" | "square";
  text: string;
  /** 0 = most prominent (recent); higher = subtler */
  ageRank: number;
  priority: number;
  eventKey: string;
  group: AnnGroup;
  kind: string;
  /** Original event price — never moved for collision */
  price: number;
};

export type PlacedPriceLine = {
  price: number;
  title: string;
  color: string;
  lineWidth: number;
  lineStyle: number;
  axisLabelVisible: boolean;
  priority: number;
  kind: string;
  group: AnnGroup;
};

export type PriorityBand = {
  price: number;
  kind: string;
  priority: number;
};

/** Canonical structure / BOS / CHOCH display labels. Never "CHO". */
export function canonicalStructureLabel(a: ChartAnnotation): string {
  const kind = String(a.kind || "").toUpperCase();
  const label = String(a.label || "").toUpperCase();
  const raw = `${kind} ${label}`;

  if (kind === "CHOCH" || label.includes("CHOCH")) return "CHOCH";
  if (kind === "BOS" || /\bBOS\b/.test(label) || label.endsWith("_BOS")) return "BOS";

  if (label === "HH" || label.includes("HIGHER_HIGH")) return "HH";
  if (label === "HL" || label.includes("HIGHER_LOW")) return "HL";
  if (label === "LH" || label.includes("LOWER_HIGH")) return "LH";
  if (label === "LL" || label.includes("LOWER_LOW")) return "LL";

  // Swing kinds without label — leave empty rather than inventing
  if (kind === "SWING_HIGH" || kind === "SWING_LOW") {
    if (["HH", "HL", "LH", "LL"].includes(label)) return label;
    return label.slice(0, 4) || kind.replace("SWING_", "");
  }

  if (kind === "IMPULSE") return "IMP";
  if (kind === "PULLBACK") return "PB";

  // Never emit truncated CHOCH fragments
  if (raw.includes("CHO") && !raw.includes("CHOCH") && kind !== "CHOCH") {
    /* ignore ambiguous fragments */
  }

  const candidate = label || kind;
  if (candidate === "CHO" || candidate.startsWith("CHOCH_")) return "CHOCH";
  if (candidate.startsWith("BULLISH_B") || candidate.startsWith("BEARISH_B")) return "BOS";
  return candidate;
}

export function annotationPriority(a: ChartAnnotation): number {
  const kind = String(a.kind || "").toUpperCase();
  const group = String(a.group || "");
  if (kind === "CURRENT" || kind === "LAST_PRICE" || kind === "MARK_PRICE") return 1;
  if (group === "trade_plan") {
    if (kind === "BUY" || kind === "SELL" || kind === "ENTRY") return 2;
    if (kind === "SL" || kind === "STOP") return 3;
    if (kind === "TAKE_PROFIT" || kind.startsWith("TP")) return 4;
    return 2;
  }
  if (kind === "BOS" || kind === "CHOCH") return 5;
  if (group === "structure") return 6;
  if (group === "setup") return 7;
  if (group === "signal") return 2;
  return 8;
}

export function groupEnabled(group: string | undefined, ann: AnnToggle): boolean {
  if (group === "structure") return ann.structure;
  if (group === "bos_choch") return ann.bosChoch;
  if (group === "setup") return ann.setup;
  if (group === "trade_plan") return ann.tradePlan;
  if (group === "signal") return ann.signal;
  return false;
}

/** Deduplicate by symbol + timeframe + event type + event timestamp. */
export function dedupeAnnotations(
  annotations: ChartAnnotation[],
  ctx: { symbol: string; timeframe: string },
): ChartAnnotation[] {
  const seen = new Set<string>();
  const out: ChartAnnotation[] = [];
  for (const a of annotations) {
    const kind = String(a.kind || a.label || "");
    const t = a.time != null ? String(a.time) : `p:${a.price}`;
    const key = `${ctx.symbol}|${ctx.timeframe}|${kind}|${t}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(a);
  }
  return out;
}

export function colorForKind(kind?: string, ageRank = 0): string {
  const k = String(kind || "").toUpperCase();
  const mute = ageRank > 6;
  if (k === "BUY" || k === "ENTRY" || k === "IMPULSE") return mute ? "#2a9d6a" : "#3dd68c";
  if (k === "SELL") return mute ? "#b0555c" : "#f07178";
  if (k === "TAKE_PROFIT" || k.startsWith("TP")) return mute ? "#4a8fc4" : "#6cb6ff";
  if (k === "SL" || k === "STOP") return mute ? "#b0555c" : "#f07178";
  if (k === "HH" || k === "SWING_HIGH") return mute ? "#4a8fc4" : "#6cb6ff";
  if (k === "HL") return mute ? "#2a9d6a" : "#3dd68c";
  if (k === "LH") return mute ? "#b0555c" : "#f07178";
  if (k === "LL" || k === "SWING_LOW") return mute ? "#8a5a3a" : "#e6a07b";
  if (k === "BOS") return mute ? "#4a8fc4" : "#6cb6ff";
  if (k === "CHOCH") return mute ? "#b8964a" : "#e6c07b";
  if (k === "PULLBACK") return mute ? "#b8964a" : "#e6c07b";
  return mute ? "#5c6575" : "#8b95a8";
}

export function tradePlanTitle(a: ChartAnnotation): string {
  const k = String(a.kind || "").toUpperCase();
  if (k === "BUY") return "BUY";
  if (k === "SELL") return "SELL";
  if (k === "ENTRY") {
    const d = String(a.direction || "").toUpperCase();
    if (d === "LONG") return "BUY";
    if (d === "SHORT") return "SELL";
    return "ENTRY";
  }
  if (k === "TAKE_PROFIT" || k.startsWith("TP")) {
    const tp = String(a.label || k).toUpperCase();
    if (tp.startsWith("TP") && tp !== "TAKE_PROFIT") return tp;
    if (k.startsWith("TP")) return k;
    return "TAKE PROFIT";
  }
  if (k === "SL" || k === "STOP") return "STOP";
  if (a.group === "signal") {
    return String(a.label || "")
      .replace(/_/g, " ")
      .slice(0, 14);
  }
  return canonicalStructureLabel(a);
}

/**
 * Preferred marker side from structure semantics.
 * Bullish HH/BOS → above; HL → below; bearish LH → below; LL → above (under lows visually via belowBar for LL).
 */
export function preferredMarkerPosition(a: ChartAnnotation): MarkerPosition {
  const kind = String(a.kind || "").toUpperCase();
  const label = canonicalStructureLabel(a);
  const direction = String(a.direction || a.label || "").toUpperCase();

  if (label === "HH") return "aboveBar";
  if (label === "HL") return "belowBar";
  if (label === "LH") return "belowBar";
  if (label === "LL") return "belowBar";

  if (kind === "SWING_HIGH") return "aboveBar";
  if (kind === "SWING_LOW") return "belowBar";

  if (kind === "BOS") {
    if (direction.includes("BEAR")) return "belowBar";
    return "aboveBar";
  }

  if (kind === "CHOCH") {
    // Distinct from BOS: bullish CHOCH below, bearish above
    if (direction.includes("BEAR")) return "aboveBar";
    return "belowBar";
  }

  if (kind === "BUY" || (kind === "ENTRY" && direction.includes("LONG"))) {
    return "belowBar";
  }
  if (kind === "SELL" || (kind === "ENTRY" && direction.includes("SHORT"))) {
    return "aboveBar";
  }

  if (kind === "SL" || kind === "STOP") return "belowBar";
  return "aboveBar";
}

function markerShape(a: ChartAnnotation, position: MarkerPosition): PlacedMarker["shape"] {
  const kind = String(a.kind || "").toUpperCase();
  if (kind === "BUY" || (kind === "ENTRY" && String(a.direction || "").toUpperCase() === "LONG")) {
    return "arrowUp";
  }
  if (kind === "SELL" || (kind === "ENTRY" && String(a.direction || "").toUpperCase() === "SHORT")) {
    return "arrowDown";
  }
  const label = canonicalStructureLabel(a);
  if (label === "BOS") return position === "aboveBar" ? "arrowUp" : "arrowDown";
  if (label === "CHOCH") return "square";
  if (label === "HH" || label === "LH") return "arrowDown";
  if (label === "HL" || label === "LL") return "arrowUp";
  return position === "aboveBar" ? "arrowDown" : "arrowUp";
}

export function annotationTimeSec(a: ChartAnnotation): number | null {
  if (a.time == null) return null;
  const t = Math.floor(new Date(String(a.time)).getTime() / 1000);
  return Number.isFinite(t) ? t : null;
}

/**
 * Snap an event timestamp onto the nearest candle open in the visible series.
 * Required when setup annotations are computed on 15m but the chart TF is 1h/4h/etc.
 * Returns null when the event is outside the loaded series window.
 */
export function snapTimeToCandle(
  timeSec: number,
  candles: CandleLike[],
): number | null {
  if (!candles.length || !Number.isFinite(timeSec)) return null;
  const times = candles.map((c) => Number(c.time)).filter((t) => Number.isFinite(t));
  if (!times.length) return null;
  if (times.includes(timeSec)) return timeSec;
  // Binary-search nearest open time
  let lo = 0;
  let hi = times.length - 1;
  if (timeSec < times[0] || timeSec > times[hi]) {
    // Allow slight overhang past last bar (open candle / event after last closed)
    const step = times.length > 1 ? Math.max(1, times[hi] - times[hi - 1]) : 60;
    if (timeSec > times[hi] && timeSec - times[hi] <= step) return times[hi];
    if (timeSec < times[0] && times[0] - timeSec <= step) return times[0];
    return null;
  }
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const v = times[mid];
    if (v === timeSec) return v;
    if (v < timeSec) lo = mid + 1;
    else hi = mid - 1;
  }
  const a = times[Math.max(0, hi)];
  const b = times[Math.min(times.length - 1, lo)];
  return Math.abs(timeSec - a) <= Math.abs(timeSec - b) ? a : b;
}

function candleAt(candles: CandleLike[], time: number): CandleLike | undefined {
  return candles.find((c) => Number(c.time) === time);
}

function priceNear(a: number, b: number, threshold: number): boolean {
  return Math.abs(a - b) <= threshold;
}

/**
 * Deterministic label placement: keeps event price fixed; only moves rendered label side/stack.
 * Higher-priority bands (current/entry/SL/TP) force structure labels to the opposite side when near.
 */
export function placeStructureMarkers(
  annotations: ChartAnnotation[],
  candles: CandleLike[],
  options?: {
    maxVisible?: number;
    priorityBands?: PriorityBand[];
    candleTimes?: Set<number>;
  },
): PlacedMarker[] {
  const maxVisible = options?.maxVisible ?? MAX_VISIBLE_STRUCTURE_LABELS;
  const bands = options?.priorityBands ?? [];
  const timeSet =
    options?.candleTimes ?? new Set(candles.map((c) => Number(c.time)));

  const candidates: Array<ChartAnnotation & { _snapTime: number }> = [];
  for (const a of annotations) {
    const g = String(a.group || "");
    // Structure / BOS / setup markers + BUY/SELL trade-plan markers (not SL/TP lines)
    const kind = String(a.kind || "").toUpperCase();
    const isTradeMarker =
      g === "trade_plan" && (kind === "BUY" || kind === "SELL" || kind === "ENTRY");
    if (g !== "structure" && g !== "bos_choch" && g !== "setup" && !isTradeMarker) {
      continue;
    }
    let t = annotationTimeSec(a);
    if (t == null && isTradeMarker && candles.length) {
      // Entry without timestamp → pin to latest bar so BUY/SELL still render
      t = Number(candles[candles.length - 1].time);
    }
    if (t == null) continue;
    const snapped = timeSet.has(t) ? t : snapTimeToCandle(t, candles);
    if (snapped == null) continue;
    const price = a.price != null ? Number(a.price) : NaN;
    if (!Number.isFinite(price)) continue;
    candidates.push({ ...a, _snapTime: snapped });
  }

  // Recent first for prominence / visible limit
  candidates.sort((a, b) => {
    const ta = a._snapTime;
    const tb = b._snapTime;
    if (tb !== ta) return tb - ta;
    return annotationPriority(a) - annotationPriority(b);
  });

  const limited = candidates.slice(0, maxVisible);

  // Slot key: time|position|stackIndex — prevent identical visual cells
  const occupied = new Map<string, string>();
  const placed: PlacedMarker[] = [];

  const span =
    candles.length > 0
      ? Math.max(...candles.map((c) => c.high)) - Math.min(...candles.map((c) => c.low))
      : 0;
  const prox = Math.max(span * 0.012, 1e-8);

  for (let i = 0; i < limited.length; i++) {
    const a = limited[i];
    const t = a._snapTime;
    const price = Number(a.price);
    const candle = candleAt(candles, t);
    let preferred = preferredMarkerPosition(a);
    const kind = String(a.kind || "").toUpperCase();
    const label =
      kind === "BUY" || kind === "SELL" || kind === "ENTRY"
        ? tradePlanTitle(a)
        : canonicalStructureLabel(a);
    const prio = annotationPriority(a);

    // Displace away from high-priority price labels near this candle
    for (const band of bands) {
      if (band.priority >= prio) continue;
      if (!priceNear(price, band.price, prox)) continue;
      if (!candle) continue;
      const nearHigh = priceNear(band.price, candle.high, prox) || band.price >= candle.close;
      const nearLow = priceNear(band.price, candle.low, prox) || band.price <= candle.close;
      if (nearHigh && preferred === "aboveBar") preferred = "belowBar";
      else if (nearLow && preferred === "belowBar") preferred = "aboveBar";
    }

    // CHOCH gets a distinct offset from BOS on the same bar
    if (label === "CHOCH") {
      const bosSameBar = placed.find(
        (m) => m.time === t && m.text === "BOS",
      );
      if (bosSameBar && preferred === bosSameBar.position) {
        preferred = preferred === "aboveBar" ? "belowBar" : "aboveBar";
      }
    }

    let position = preferred;
    let stack = 0;
    const tryOrder: MarkerPosition[] = [
      preferred,
      preferred === "aboveBar" ? "belowBar" : "aboveBar",
    ];

    let slot = "";
    for (const pos of tryOrder) {
      for (stack = 0; stack < 6; stack++) {
        slot = `${t}|${pos}|${stack}`;
        if (!occupied.has(slot)) {
          position = pos;
          break;
        }
      }
      if (slot && !occupied.has(slot)) break;
    }

    // Soft stack: encode stack in text padding for same-side siblings (visual separation cue)
    const text = stack > 0 ? `${label}` : label;
    occupied.set(slot || `${t}|${position}|0`, text);

    // Ensure BOS/CHOCH never share identical position cell
    if (label === "BOS" || label === "CHOCH") {
      const twin = placed.find(
        (m) =>
          m.time === t &&
          m.position === position &&
          (m.text === "BOS" || m.text === "CHOCH") &&
          m.text !== label,
      );
      if (twin) {
        position = position === "aboveBar" ? "belowBar" : "aboveBar";
        slot = `${t}|${position}|0`;
        occupied.set(slot, label);
      }
    }

    placed.push({
      time: t,
      position,
      color: colorForKind(label === "HH" || label === "HL" || label === "LH" || label === "LL" ? label : String(a.kind), i),
      shape: markerShape(a, position),
      text,
      ageRank: i,
      priority: prio,
      eventKey: `${String(a.kind)}|${t}|${price}`,
      group: String(a.group) as AnnGroup,
      kind: String(a.kind || ""),
      price,
    });
  }

  placed.sort((a, b) => a.time - b.time);
  return placed;
}

export function buildPriorityBands(
  annotations: ChartAnnotation[],
  currentPrice?: number | null,
): PriorityBand[] {
  const bands: PriorityBand[] = [];
  if (currentPrice != null && Number.isFinite(currentPrice)) {
    bands.push({ price: Number(currentPrice), kind: "CURRENT", priority: 1 });
  }
  for (const a of annotations) {
    if (a.group !== "trade_plan" && a.group !== "signal") continue;
    const price = a.price != null ? Number(a.price) : NaN;
    if (!Number.isFinite(price)) continue;
    bands.push({
      price,
      kind: String(a.kind || ""),
      priority: annotationPriority(a),
    });
  }
  return bands.sort((a, b) => a.priority - b.priority);
}

export function placePriceLines(
  annotations: ChartAnnotation[],
  ann: AnnToggle,
  options?: { suppressAxisNear?: PriorityBand[]; proximity?: number },
): PlacedPriceLine[] {
  const lines: PlacedPriceLine[] = [];
  const prox = options?.proximity ?? 0;
  const suppress = options?.suppressAxisNear ?? [];

  for (const a of annotations) {
    if (!groupEnabled(a.group, ann)) continue;
    const price = a.price != null ? Number(a.price) : NaN;
    if (!Number.isFinite(price)) continue;

    const kind = String(a.kind || "").toUpperCase();
    const isTrade = a.group === "trade_plan";
    const isSignal = a.group === "signal" && kind === "MARKET_SIGNAL";
    // BOS/CHOCH are marker-only — price lines expand the Y scale and clutter the axis
    if (kind === "BOS" || kind === "CHOCH") continue;
    if (!isTrade && !isSignal) continue;

    let title = tradePlanTitle(a);
    let axisLabelVisible = true;

    // Structure must yield to current/entry/SL/TP on the axis
    if (axisLabelVisible && suppress.length > 0 && prox > 0) {
      const prio = annotationPriority(a);
      for (const band of suppress) {
        if (band.priority < prio && priceNear(price, band.price, prox)) {
          axisLabelVisible = false;
          break;
        }
      }
    }

    lines.push({
      price,
      title,
      color: colorForKind(kind),
      lineWidth: isTrade ? 2 : 1,
      lineStyle: isTrade ? 0 : 2,
      axisLabelVisible,
      priority: annotationPriority(a),
      kind,
      group: String(a.group) as AnnGroup,
    });
  }

  return lines;
}

/**
 * After placement: drop lower-priority labels that collide near the latest candles
 * so CHOCH/BOS/current-price stay readable. Does not mutate source annotations.
 */
export function thinCrowdedMarkers(
  markers: PlacedMarker[],
  candles: CandleLike[],
  options?: { recentBars?: number; budget?: number; priceProximityPct?: number },
): PlacedMarker[] {
  if (markers.length === 0 || candles.length === 0) return markers;
  const recentBars = options?.recentBars ?? 24;
  const budget = options?.budget ?? RECENT_BAR_LABEL_BUDGET;
  const lastTime = Number(candles[candles.length - 1].time);
  // Approximate bar step from last two candles
  const step =
    candles.length > 1
      ? Math.max(1, Number(candles[candles.length - 1].time) - Number(candles[candles.length - 2].time))
      : 60;
  const recentCutoff = lastTime - recentBars * step;
  const span =
    Math.max(...candles.map((c) => c.high)) - Math.min(...candles.map((c) => c.low));
  const prox = Math.max(span * (options?.priceProximityPct ?? 0.02), 1e-8);

  const recent = markers
    .filter((m) => m.time >= recentCutoff)
    .sort((a, b) => a.priority - b.priority || b.time - a.time);
  const older = markers.filter((m) => m.time < recentCutoff);

  const keptRecent: PlacedMarker[] = [];
  const swingSeen = new Set<string>();
  for (const m of recent) {
    if (keptRecent.length >= budget) break;

    // At most one of each swing label in the recent window (newest/highest-prio wins via sort)
    if (["HH", "HL", "LH", "LL"].includes(m.text)) {
      if (swingSeen.has(m.text)) continue;
    }

    const collides = keptRecent.some(
      (k) =>
        (k.time === m.time && k.position === m.position) ||
        (Math.abs(k.time - m.time) <= step * 2 &&
          k.position === m.position &&
          priceNear(k.price, m.price, prox)),
    );
    if (collides && m.priority >= 6) continue;
    if (collides) {
      // Allow BOS/CHOCH through by dropping a lower-priority sibling already kept
      const victimIdx = keptRecent.findIndex(
        (k) =>
          k.priority > m.priority &&
          k.position === m.position &&
          (k.time === m.time || priceNear(k.price, m.price, prox)),
      );
      if (victimIdx >= 0) {
        const victim = keptRecent[victimIdx];
        if (["HH", "HL", "LH", "LL"].includes(victim.text)) {
          swingSeen.delete(victim.text);
        }
        keptRecent.splice(victimIdx, 1);
      } else if (m.priority > 5) continue;
    }
    if (["HH", "HL", "LH", "LL"].includes(m.text)) swingSeen.add(m.text);
    keptRecent.push(m);
  }

  // Older labels: keep subtle, but skip those that share a slot with a kept recent label
  const keptOlder = older.filter((m) => {
    return !keptRecent.some(
      (k) => k.time === m.time && k.position === m.position && k.text === m.text,
    );
  });

  return [...keptOlder, ...keptRecent].sort((a, b) => a.time - b.time);
}

export function computeChartPriceRange(
  candles: CandleLike[],
  options?: {
    /** Extra prices (entry/SL/TP) included only if near the candle range */
    extraPrices?: number[];
    padPct?: number;
    /** Include extras within this multiple of candle span */
    extraSlack?: number;
  },
): { chartMin: number; chartMax: number; visibleHigh: number; visibleLow: number; padding: number } {
  if (candles.length === 0) {
    return { chartMin: 0, chartMax: 1, visibleHigh: 1, visibleLow: 0, padding: 0 };
  }

  const visibleHigh = Math.max(...candles.map((c) => c.high));
  const visibleLow = Math.min(...candles.map((c) => c.low));
  let hi = visibleHigh;
  let lo = visibleLow;
  const span0 = Math.max(hi - lo, 0);
  const slack = options?.extraSlack ?? 0.25;
  const baseSpan = span0 > 0 ? span0 : Math.max(Math.abs(hi) * 0.01, 1e-8);

  for (const p of options?.extraPrices ?? []) {
    if (!Number.isFinite(p)) continue;
    // Avoid exploding vertical range with far-away levels
    if (p > hi + baseSpan * slack || p < lo - baseSpan * slack) continue;
    hi = Math.max(hi, p);
    lo = Math.min(lo, p);
  }

  const span = Math.max(hi - lo, 0);
  // Minimum tick-based padding adapts to asset price magnitude
  const minTickPad = Math.max(span * 0.002, Math.abs(hi) * 1e-6, 1e-8);
  const padPct = options?.padPct ?? STRUCTURE_PAD_PCT;
  const padding = Math.max(span * padPct, minTickPad);

  return {
    visibleHigh,
    visibleLow,
    padding,
    chartMin: lo - padding,
    chartMax: hi + padding,
  };
}

/** Scale margins for candle pane — tight top, room for volume overlay at bottom. */
export const CANDLE_SCALE_MARGINS = { top: 0.04, bottom: 0.14 } as const;

export function filterAnnotationsByToggles(
  annotations: ChartAnnotation[],
  ann: AnnToggle,
): ChartAnnotation[] {
  return annotations.filter((a) => groupEnabled(a.group, ann));
}

/** True when liquidations panel should use the compact footprint. */
export function isLiquidationsCompact(
  status: string,
  eventCount: number,
): boolean {
  if (eventCount > 0 && (status === "LIVE" || status === "STALE")) return false;
  return true;
}

export function liquidationsSubtitle(symbol: string | null, status: string): string {
  if (!symbol) return "SELECT A COIN";
  if (status === "UNAVAILABLE") return "DATA UNAVAILABLE";
  if (status === "STALE") return "WAITING FOR DATA";
  return "WAITING FOR DATA";
}

/** Assert presentation never fabricates liquidation notionals. */
export function sanitizeLiquidationEvents(
  events: Array<Record<string, unknown>>,
  status: string,
): Array<Record<string, unknown>> {
  if (status === "WAITING" || status === "UNAVAILABLE") return [];
  return events.filter((e) => {
    const n = Number(e.notional);
    return Number.isFinite(n) && n > 0;
  });
}
