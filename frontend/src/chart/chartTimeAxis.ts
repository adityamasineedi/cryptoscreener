import { TickMarkType, type Time } from "lightweight-charts";

/** Intraday TFs need hour:minute on the x-axis; daily can stay date-only. */
export function isIntradayTimeframe(tf: string): boolean {
  const t = String(tf || "").toLowerCase();
  return t.endsWith("m") || t.endsWith("h");
}

function asUnixSeconds(time: Time): number | null {
  if (typeof time === "number" && Number.isFinite(time)) return time;
  if (typeof time === "string") {
    const n = Date.parse(time);
    return Number.isFinite(n) ? Math.floor(n / 1000) : null;
  }
  if (time && typeof time === "object" && "year" in time) {
    const d = Date.UTC(time.year, time.month - 1, time.day);
    return Math.floor(d / 1000);
  }
  return null;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** Crosshair / legend time label. */
export function formatChartCrosshairTime(time: Time, timeframe: string): string {
  const sec = asUnixSeconds(time);
  if (sec == null) return "";
  const d = new Date(sec * 1000);
  const date = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  if (!isIntradayTimeframe(timeframe)) {
    return `${date} ${d.getUTCFullYear()}`;
  }
  return `${date} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC`;
}

/**
 * Tick labels for the bottom time axis.
 * Avoid bare day-of-month repeats ("30","30","1","1") on 1h charts —
 * show HH:mm for time ticks and "Sep 30" / "Oct 1" for day boundaries.
 */
export function formatChartTickMark(
  time: Time,
  tickMarkType: TickMarkType,
  timeframe: string,
): string {
  const sec = asUnixSeconds(time);
  if (sec == null) return "";
  const d = new Date(sec * 1000);
  const month = MONTHS[d.getUTCMonth()];
  const day = d.getUTCDate();
  const hm = `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;

  switch (tickMarkType) {
    case TickMarkType.Year:
      return String(d.getUTCFullYear());
    case TickMarkType.Month:
      // On intraday charts a Month tick is usually the month boundary —
      // "Oct 2026" reads like a year jump between Sep 30 and Oct 2.
      if (isIntradayTimeframe(timeframe)) {
        return `${month} ${day}`;
      }
      return `${month} ${d.getUTCFullYear()}`;
    case TickMarkType.DayOfMonth:
      // Always include month so "30" / "1" are not ambiguous across month change
      return `${month} ${day}`;
    case TickMarkType.Time:
    case TickMarkType.TimeWithSeconds:
      if (!isIntradayTimeframe(timeframe)) {
        return `${month} ${day}`;
      }
      // At midnight, prefer date so the day boundary is obvious
      if (d.getUTCHours() === 0 && d.getUTCMinutes() === 0) {
        return `${month} ${day}`;
      }
      return hm;
    default:
      return isIntradayTimeframe(timeframe) ? hm : `${month} ${day}`;
  }
}

export function timeScaleOptionsForTimeframe(timeframe: string) {
  const intraday = isIntradayTimeframe(timeframe);
  return {
    timeVisible: intraday,
    secondsVisible: false,
    tickMarkFormatter: (time: Time, tickMarkType: TickMarkType) =>
      formatChartTickMark(time, tickMarkType, timeframe),
  };
}

export function localizationForTimeframe(timeframe: string) {
  return {
    timeFormatter: (time: Time) => formatChartCrosshairTime(time, timeframe),
  };
}

type TimedPoint = { time: Time };

/**
 * Lightweight Charts requires series data strictly ascending by time.
 * Backend/memory merges can return out-of-order or duplicate open_times —
 * sort ASC and keep the last bar for each timestamp.
 */
export function ensureAscendingByTime<T extends TimedPoint>(rows: T[]): T[] {
  if (rows.length <= 1) return rows;
  const indexed = rows.map((row, i) => ({
    row,
    i,
    t: asUnixSeconds(row.time),
  }));
  indexed.sort((a, b) => {
    const ta = a.t ?? Number.POSITIVE_INFINITY;
    const tb = b.t ?? Number.POSITIVE_INFINITY;
    if (ta !== tb) return ta - tb;
    return a.i - b.i;
  });
  const out: T[] = [];
  for (const item of indexed) {
    if (item.t == null) continue;
    if (out.length && asUnixSeconds(out[out.length - 1].time) === item.t) {
      out[out.length - 1] = item.row;
    } else {
      out.push(item.row);
    }
  }
  return out;
}

