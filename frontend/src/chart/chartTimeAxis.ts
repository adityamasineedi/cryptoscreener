import { TickMarkType, type Time } from "lightweight-charts";

/** Chart axis / crosshair display zone. Candle unix times stay UTC. */
export type ChartTimeZone = "UTC" | "IST";

export const CHART_TIMEZONE_KEY = "cs.charts.timezone";
export const DEFAULT_CHART_TIMEZONE: ChartTimeZone = "IST";

export function chartTimeZoneId(tz: ChartTimeZone): string {
  return tz === "IST" ? "Asia/Kolkata" : "UTC";
}

export function chartTimeZoneLabel(tz: ChartTimeZone): string {
  return tz === "IST" ? "IST" : "UTC";
}

export function readStoredChartTimeZone(): ChartTimeZone {
  try {
    const v = localStorage.getItem(CHART_TIMEZONE_KEY);
    if (v === "IST" || v === "UTC") return v;
  } catch {
    /* ignore */
  }
  return DEFAULT_CHART_TIMEZONE;
}

export function writeStoredChartTimeZone(tz: ChartTimeZone): void {
  try {
    localStorage.setItem(CHART_TIMEZONE_KEY, tz);
  } catch {
    /* ignore */
  }
}

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

type ZoneParts = {
  year: number;
  month: number; // 1-12
  day: number;
  hours: number;
  minutes: number;
};

function partsInZone(d: Date, tz: ChartTimeZone): ZoneParts {
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: chartTimeZoneId(tz),
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
  });
  const parts = fmt.formatToParts(d);
  const get = (type: Intl.DateTimeFormatPartTypes): number => {
    const raw = parts.find((p) => p.type === type)?.value;
    return raw != null ? Number(raw) : 0;
  };
  return {
    year: get("year"),
    month: get("month"),
    day: get("day"),
    hours: get("hour"),
    minutes: get("minute"),
  };
}

/** Crosshair / legend time label. */
export function formatChartCrosshairTime(
  time: Time,
  timeframe: string,
  timeZone: ChartTimeZone = DEFAULT_CHART_TIMEZONE,
): string {
  const sec = asUnixSeconds(time);
  if (sec == null) return "";
  const p = partsInZone(new Date(sec * 1000), timeZone);
  const date = `${p.day} ${MONTHS[p.month - 1]}`;
  if (!isIntradayTimeframe(timeframe)) {
    return `${date} ${p.year}`;
  }
  return `${date} ${pad2(p.hours)}:${pad2(p.minutes)} ${chartTimeZoneLabel(timeZone)}`;
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
  timeZone: ChartTimeZone = DEFAULT_CHART_TIMEZONE,
): string {
  const sec = asUnixSeconds(time);
  if (sec == null) return "";
  const p = partsInZone(new Date(sec * 1000), timeZone);
  const month = MONTHS[p.month - 1];
  const day = p.day;
  const hm = `${pad2(p.hours)}:${pad2(p.minutes)}`;

  switch (tickMarkType) {
    case TickMarkType.Year:
      return String(p.year);
    case TickMarkType.Month:
      // On intraday charts a Month tick is usually the month boundary —
      // "Oct 2026" reads like a year jump between Sep 30 and Oct 2.
      if (isIntradayTimeframe(timeframe)) {
        return `${month} ${day}`;
      }
      return `${month} ${p.year}`;
    case TickMarkType.DayOfMonth:
      // Always include month so "30" / "1" are not ambiguous across month change
      return `${month} ${day}`;
    case TickMarkType.Time:
    case TickMarkType.TimeWithSeconds:
      if (!isIntradayTimeframe(timeframe)) {
        return `${month} ${day}`;
      }
      // At midnight in the display zone, prefer date so the day boundary is obvious
      if (p.hours === 0 && p.minutes === 0) {
        return `${month} ${day}`;
      }
      return hm;
    default:
      return isIntradayTimeframe(timeframe) ? hm : `${month} ${day}`;
  }
}

export function timeScaleOptionsForTimeframe(
  timeframe: string,
  timeZone: ChartTimeZone = DEFAULT_CHART_TIMEZONE,
) {
  const intraday = isIntradayTimeframe(timeframe);
  return {
    timeVisible: intraday,
    secondsVisible: false,
    tickMarkFormatter: (time: Time, tickMarkType: TickMarkType) =>
      formatChartTickMark(time, tickMarkType, timeframe, timeZone),
  };
}

export function localizationForTimeframe(
  timeframe: string,
  timeZone: ChartTimeZone = DEFAULT_CHART_TIMEZONE,
) {
  return {
    timeFormatter: (time: Time) => formatChartCrosshairTime(time, timeframe, timeZone),
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
