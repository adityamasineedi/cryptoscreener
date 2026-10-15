import type { StrategyTradeRow } from "../api/client";

/** Presentation-only replay. Does not change COMBO_02 entries, stops, or targets. */

export type ReplayCandle = {
  open_time: string;
  high: number;
  low: number;
};

export type ReplayHit = {
  kind: "SL" | "TP" | "OPEN";
  bars: number | null;
  r: number | null;
};

export type StopOutShortRow = {
  longStopTime: string;
  shortEntry: number;
  full: ReplayHit;
  oneR: ReplayHit;
  /** Best favorable move, in R, through the full-target exit bar. */
  mfeR: number | null;
};

export type StopOutShortReplay = {
  rows: StopOutShortRow[];
  fullR: number | null;
  oneRR: number | null;
  oneRWinners: number;
  oneRStops: number;
};

const EPS = 1e-9;

export function isLongStopOut(trade: StrategyTradeRow): boolean {
  const side = String(trade.direction || "LONG").toUpperCase();
  const outcome = String(trade.outcome || "").toUpperCase();
  return side === "LONG" && outcome.startsWith("SL");
}

function parseMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

function findFillIndex(candles: ReplayCandle[], exitTime: string | null | undefined): number {
  const target = parseMs(exitTime);
  if (target == null) return -1;
  let exact = -1;
  let prior = -1;
  for (let i = 0; i < candles.length; i += 1) {
    const ms = parseMs(candles[i].open_time);
    if (ms == null) continue;
    if (Math.abs(ms - target) < 1000) exact = i;
    if (ms <= target) prior = i;
  }
  return exact >= 0 ? exact : prior;
}

/** Risk multiple of a short target. Stop is above entry, so target R is (entry-target)/(stop-entry). */
function targetR(entry: number, stop: number, target: number): number {
  const risk = stop - entry;
  if (!(risk > EPS)) return 0;
  return (entry - target) / risk;
}

function walkManaged(
  candles: ReplayCandle[],
  entry: number,
  stop: number,
  target: number,
): { hit: ReplayHit; mfeR: number | null } {
  const risk = stop - entry;
  let mfe = 0;
  for (let i = 0; i < candles.length; i += 1) {
    const bar = candles[i];
    if (risk > EPS) mfe = Math.max(mfe, (entry - bar.low) / risk);
    const stopHit = bar.high >= stop - EPS;
    const targetHit = bar.low <= target + EPS;
    if (stopHit) return { hit: { kind: "SL", bars: i + 1, r: -1 }, mfeR: mfe };
    if (targetHit) {
      return {
        hit: { kind: "TP", bars: i + 1, r: targetR(entry, stop, target) },
        mfeR: mfe,
      };
    }
  }
  return { hit: { kind: "OPEN", bars: null, r: null }, mfeR: candles.length ? mfe : null };
}

/**
 * v1_skip_stop_bar: archived behaviour. The walk starts on the bar after the long
 *   stop, so a stop bar that already traded back through the short stop is missed.
 * v2_stop_bar_counts: corrected. The short exists from the long-stop fill, so the
 *   stop bar is walked first, stop before target (same as the LCSC study's
 *   CONSERVATIVE_STOP_BAR walk).
 */
export type ReplayVersion = "v1_skip_stop_bar" | "v2_stop_bar_counts";

/** Uncosted research only. Not a short book; never add these R to any result. */
export const STOP_OUT_REPLAY_LABEL = "UNCOSTED_RESEARCH_ONLY_DO_NOT_USE";

export function replayShortsAtLongStops(
  trades: StrategyTradeRow[],
  candles: ReplayCandle[],
  version: ReplayVersion = "v1_skip_stop_bar",
): StopOutShortReplay {
  const stops = trades.filter(isLongStopOut);
  const rows: StopOutShortRow[] = stops.map((trade) => {
    const entry = Number(trade.stop_price);
    const stop = Number(trade.entry_price);
    const risk = stop - entry;
    const fill = findFillIndex(candles, trade.exit_time);
    const firstBar = version === "v2_stop_bar_counts" ? fill : fill + 1;
    const after = fill >= 0 ? candles.slice(firstBar) : [];
    const longTarget = trade.tp1 == null ? null : Number(trade.tp1);
    const fullTarget =
      longTarget == null || !(risk > EPS)
        ? null
        : entry - (longTarget - Number(trade.entry_price));
    const oneRTarget = risk > EPS ? entry - risk : null;

    const openHit: ReplayHit = { kind: "OPEN", bars: null, r: null };
    const fullManaged =
      fullTarget == null ? null : walkManaged(after, entry, stop, fullTarget);
    const oneManaged =
      oneRTarget == null ? null : walkManaged(after, entry, stop, oneRTarget);

    return {
      longStopTime: trade.exit_time || trade.signal_time || "",
      shortEntry: entry,
      full: fullManaged?.hit ?? openHit,
      oneR: oneManaged?.hit ?? openHit,
      mfeR: fullManaged?.mfeR ?? oneManaged?.mfeR ?? null,
    };
  });

  const finishedFull = rows.map((row) => row.full.r).filter((r): r is number => r != null);
  const finishedOne = rows.map((row) => row.oneR.r).filter((r): r is number => r != null);
  return {
    rows,
    fullR: finishedFull.length ? finishedFull.reduce((sum, r) => sum + r, 0) : null,
    oneRR: finishedOne.length ? finishedOne.reduce((sum, r) => sum + r, 0) : null,
    oneRWinners: rows.filter((row) => row.oneR.kind === "TP").length,
    oneRStops: rows.filter((row) => row.oneR.kind === "SL").length,
  };
}

export function formatReplayR(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const rounded = Math.round(value * 100) / 100;
  const body = Number.isInteger(rounded) ? String(Math.abs(rounded)) : Math.abs(rounded).toFixed(2);
  if (rounded > 0) return `+${body}R`;
  if (rounded < 0) return `−${body}R`;
  return "0R";
}

export function formatReplayHit(hit: ReplayHit): string {
  if (hit.kind === "OPEN") return "Open";
  const noun = hit.bars === 1 ? "bar" : "bars";
  const where = hit.kind === "SL" ? "SL" : "TP";
  return `${where} in ${hit.bars} ${noun}, ${formatReplayR(hit.r)}`;
}

export function favorableStopNote(rows: StopOutShortRow[]): string | null {
  const moved = rows
    .map((row, index) => ({ index: index + 1, mfeR: row.mfeR, stopped: row.oneR.kind === "SL" }))
    .filter((row) => row.stopped && row.mfeR != null && row.mfeR > 1 + 1e-6);
  if (!moved.length) return null;
  const listed = moved
    .map((row) => String(row.index))
    .join(", ")
    .replace(/, ([^,]+)$/, " and $1");
  const which = moved.length === 1 ? `stop-out ${listed}` : `stop-outs ${listed}`;
  const amounts = moved
    .map((row) => `${(Math.round((row.mfeR as number) * 10) / 10).toFixed(1)}R`)
    .join(", ")
    .replace(/, ([^,]+)$/, " and $1");
  return `Price did move in favor after ${which}, by about ${amounts}, then came back through the short stop.`;
}

function countLabel(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function replaySummary(replay: StopOutShortReplay): string {
  const full = formatReplayR(replay.fullR);
  const one = formatReplayR(replay.oneRR);
  return `Holding for the original targets is ${full} on top of the longs that already lost. Taking only 1R on the short is ${one} (${countLabel(replay.oneRWinners, "winner", "winners")}, ${countLabel(replay.oneRStops, "stop", "stops")}).`;
}
