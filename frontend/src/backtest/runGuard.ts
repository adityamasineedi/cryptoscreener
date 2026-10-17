import type { BacktestJob } from "../api/client";

/** The part of a start request the guard compares against the job that ran. */
export type RunRequest = {
  symbols: string[];
  timeframes: string[];
  direction?: string;
  strategy_id?: string;
  research_risk_override?: boolean;
};

const FROZEN_V1_STRATEGY = "COMBO_02_V1";
/** Native LONG+SHORT: one run holds trades in both directions whatever the direction. */
const NATIVE_TWO_SIDED_STRATEGY = "COMBO_02_V2_1_A";
/** Two-sided request direction for the COMBO_02_V2 family (backend TWO_SIDED_DIRECTION). */
export const TWO_SIDED_DIRECTION = "BOTH";
/**
 * The rest of the backend COMBO_V2_FAMILY. The backend walks the side a LONG or SHORT
 * request names, and both sides only for a "BOTH" request. The hedge book posts these
 * with direction BOTH; a LONG or SHORT request is a single-direction run.
 */
const TWO_SIDED_ON_REQUEST_FAMILY: ReadonlySet<string> = new Set(["COMBO_02_V2", "COMBO_02_V2_1B"]);

/** Direction the hedge book posts for a two-sided strategy: BOTH for V2/V2_1B, LONG for V2.1-A. */
export function hedgeRequestDirection(strategyId: string | null | undefined): "LONG" | "BOTH" {
  return strategyId && TWO_SIDED_ON_REQUEST_FAMILY.has(strategyId) ? TWO_SIDED_DIRECTION : "LONG";
}

/** True when the job holds LONG and SHORT trades: V2.1-A, or a V2/V2_1B job run as BOTH. */
export function isNativeTwoSidedJob(job: BacktestJob | null | undefined): boolean {
  const id = job?.strategy_id;
  if (!id) return false;
  if (id === NATIVE_TWO_SIDED_STRATEGY) return true;
  return TWO_SIDED_ON_REQUEST_FAMILY.has(id) && job?.direction === TWO_SIDED_DIRECTION;
}

/** A row or request direction as the screen shows it. BOTH reads LONG+SHORT. */
export function directionText(direction: string | null | undefined): string {
  if (!direction) return "—";
  return direction === TWO_SIDED_DIRECTION ? "LONG+SHORT" : direction;
}

function sameSet(a: string[], b: string[]): boolean {
  const left = new Set(a.map((x) => x.toUpperCase()));
  const right = new Set(b.map((x) => x.toUpperCase()));
  if (left.size !== right.size) return false;
  for (const x of left) if (!right.has(x)) return false;
  return true;
}

function isFrozenV1Request(request: RunRequest): boolean {
  return (
    (request.strategy_id ?? FROZEN_V1_STRATEGY) === FROZEN_V1_STRATEGY &&
    (request.direction ?? "LONG") === "LONG" &&
    !request.research_risk_override
  );
}

/**
 * Problems that mean `job` is not the run `request` asked for.
 * A field the job does not report is not compared.
 */
export function checkRunMatchesRequest(
  job: BacktestJob | null | undefined,
  request: RunRequest | null | undefined,
): string[] {
  if (!job || !request) return [];
  const problems: string[] = [];

  if (request.direction && job.direction && job.direction !== request.direction) {
    problems.push(
      `Direction: the form requested ${request.direction}, the job ran ${job.direction}.`,
    );
  }
  if (request.strategy_id && job.strategy_id && job.strategy_id !== request.strategy_id) {
    problems.push(
      `Strategy: the form requested ${request.strategy_id}, the job ran ${job.strategy_id}.`,
    );
  }
  if (job.symbols && !sameSet(job.symbols, request.symbols)) {
    problems.push(
      `Symbols: the form requested ${request.symbols.join(", ") || "none"}, the job ran ${job.symbols.join(", ") || "none"}.`,
    );
  }
  if (job.timeframes && !sameSet(job.timeframes, request.timeframes)) {
    problems.push(
      `Timeframes: the form requested ${request.timeframes.join(", ") || "none"}, the job ran ${job.timeframes.join(", ") || "none"}.`,
    );
  }

  if (!isFrozenV1Request(request)) {
    if (job.production_comparable === true) {
      problems.push(
        "The request is not the frozen COMBO_02 v1 LONG profile, but the job is marked production comparable.",
      );
    }
    if (job.research_only === false) {
      problems.push(
        "The request is not the frozen COMBO_02 v1 LONG profile, but the job is not marked research-only.",
      );
    }
  }
  return problems;
}

/** Problems inside one job: flags and directions that contradict each other. */
export function checkJobInternallyConsistent(
  job: BacktestJob | null | undefined,
): string[] {
  if (!job) return [];
  const problems: string[] = [];

  if (
    typeof job.production_comparable === "boolean" &&
    typeof job.research_only === "boolean" &&
    job.production_comparable === job.research_only
  ) {
    problems.push(
      `Job flags contradict: production_comparable and research_only are both ${job.production_comparable}.`,
    );
  }

  const rows = job.rows || [];
  if (!isNativeTwoSidedJob(job) && job.direction) {
    for (const row of rows) {
      const cell = `${row.symbol} ${row.timeframe}`;
      if (row.direction && row.direction !== job.direction) {
        problems.push(
          `${cell}: row direction is ${row.direction} in a ${job.direction} run.`,
        );
      }
      for (const trade of row.trades || []) {
        if (trade.direction && trade.direction !== job.direction) {
          problems.push(
            `${cell}: trade_no ${trade.trade_no ?? "?"} is ${trade.direction} in a ${job.direction} run.`,
          );
        }
      }
    }
  }

  if (job.production_comparable === false) {
    for (const row of rows) {
      if (row.production_comparable === true) {
        problems.push(
          `${row.symbol} ${row.timeframe}: row is marked production comparable inside a run that is not.`,
        );
      }
    }
  }
  return problems;
}

export function directionLabel(job: BacktestJob | null | undefined): string {
  if (isNativeTwoSidedJob(job)) return "LONG+SHORT (native)";
  return job?.direction || "—";
}

export function htfRequirementLabel(job: BacktestJob | null | undefined): string {
  if (isNativeTwoSidedJob(job)) {
    return "soft 4h + 15m confirm (v2 playbooks)";
  }
  if (job?.strategy_id === "CANDLE12_V3_EXEC_FIX") return "none (no HTF gate)";
  return job?.direction === "SHORT"
    ? "1h + 4h bearish alignment"
    : "1h + 4h bullish alignment";
}
