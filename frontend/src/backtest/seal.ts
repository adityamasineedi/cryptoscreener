/**
 * The research seal on the Backtest tab (owner D202 / "D184"), mirroring backend
 * app/services/seal.py (SEALS.md S0, constitution C3a): no backtest reads OHLCV after
 * 2026-07-14 23:59 UTC. The backend enforces it; this module only says so on screen and
 * stops a request the backend would refuse.
 */

/** Last UTC day any backtest may read (inclusive end day). */
export const SEAL_LAST_DAY = "2026-07-14";

/** Exact text of the backend's HTTP 400 for a calendar end (or start) after the seal. */
export const SEALED_MESSAGE = "Sealed: data after 2026-07-14 is reserved for the holdout (SEALS.md)";

/** Banner at the top of the Backtest tab. */
export const BACKTEST_TAB_BANNER =
  "In-sample backtest. No random baseline or validation. Decisions come from the research results page.";

/** Label of the Profit checker result (window 20 Sep–10 Oct 2026, inside the seal). */
export const PROFIT_CHECKER_SEALED_LABEL =
  "Inside the sealed holdout window; already used before the seal (SEALS.md). Not evidence.";

/** Label on a form field the run ignores. */
export const NOT_USED_LABEL = "not used by this run";

/** Backtest-tab default costs (owner D144): taker 0.05%, maker 0.02% of notional. */
export const TAB_TAKER_FEE_PCT = 0.05;
export const TAB_MAKER_FEE_PCT = 0.02;

/** Owner-only dev mode (D212): switch label, red banner, run label and the disabled hint. */
export const DEV_MODE_SWITCH_LABEL = "Dev mode: allow data after 2026-07-14";
export const DEV_MODE_BANNER = "Uses holdout data; results are not evidence";
export const DEV_MODE_LABEL = "DEV MODE (holdout data)";
export const DEV_MODE_DISABLED_HINT = "owner-only: set BACKTEST_DEV_MODE_ALLOWED=true in .env";

/** True when a UTC day (YYYY-MM-DD) lies after the seal. Empty is not past it. */
export function isPastSeal(day: string | null | undefined): boolean {
  return Boolean(day) && String(day).slice(0, 10) > SEAL_LAST_DAY;
}

/** UTC calendar day shifted by whole days. */
function shiftUtcDay(day: string, days: number): string {
  const ms = Date.parse(`${day}T00:00:00Z`) + days * 24 * 60 * 60 * 1000;
  return new Date(ms).toISOString().slice(0, 10);
}

/** "Last 365 days" ends at the seal, not today. */
export function last365DaysToSeal(): { start: string; end: string } {
  return { start: shiftUtcDay(SEAL_LAST_DAY, -365), end: SEAL_LAST_DAY };
}

/** "2026-07-14 23:00 UTC" from an ISO bar time. */
export function barLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  return String(iso).slice(0, 16).replace("T", " ") + " UTC";
}
