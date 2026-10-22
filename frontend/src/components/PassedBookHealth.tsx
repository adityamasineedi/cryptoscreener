import type { PassedRegimeMissedRow, PassedRegimeResponse } from "../api/client";
import { formatIst, formatUtc } from "../paper/zoneTime";
import { coinLabel } from "./SymbolAllowlistLines";

/** 45 -> "45s", 3360 -> "56m", 3900 -> "1h 5m". Missing -> "—". */
export function fmtLag(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

function fmtTime(iso: string | null | undefined): string {
  return formatIst(iso);
}

function count(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : String(value);
}

function missedWhat(row: PassedRegimeMissedRow): string {
  const coin = row.symbol ? coinLabel(row.symbol) : "—";
  if (row.outcome === "not_evaluated") {
    const bars = row.bars ?? 1;
    return `${coin} · ${bars} ${bars === 1 ? "bar" : "bars"}`;
  }
  return [coin, row.strategy_id, row.direction].filter(Boolean).join(" · ");
}

/**
 * Read-only health of the passed-book watcher: the last bar it finished and
 * how late, a STALE badge when evaluation stopped, and the bars and signals
 * it reached too late. Fields an older backend does not send show "—".
 */
export function PassedBookHealth({ snapshot }: { snapshot: PassedRegimeResponse }) {
  const beat = snapshot.heartbeat ?? null;
  const stale = beat?.stale === true;
  const notTradable = snapshot.not_tradable ?? [];
  const blocked = new Set(notTradable.map((row) => `${row.symbol}|${row.reason}`));
  // A coin that can never be evaluated is shown once (below), not as one "missed" row per hour.
  const missed = (snapshot.recent_missed ?? []).filter(
    (row) => !(row.outcome === "not_evaluated" && blocked.has(`${row.symbol ?? ""}|${row.reason ?? ""}`)),
  );
  const since = snapshot.missed_signals_since;
  const missedShown = since ? since.count : snapshot.missed_signals;
  const missedTitle = since
    ? `Since ${formatUtc(since.since)}. All time: ${count(snapshot.missed_signals)}`
    : "All time";
  const owed = Object.entries(beat?.pending_by_symbol ?? {}).filter(([, n]) => n > 0);
  const lastBar = beat?.last_eval_bar
    ? `${beat.last_eval_symbol ? `${coinLabel(beat.last_eval_symbol)} ` : ""}${fmtTime(beat.last_eval_bar)}`
    : "—";

  return (
    <div
      className={`mb-3 rounded border px-2 py-1.5 text-[11px] ${
        stale ? "border-red-500/50 bg-red-500/10" : "border-terminal-border/60 bg-black/20"
      }`}
      data-testid="passed-health"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-semibold text-terminal-text">Passed book health</span>
        {stale ? (
          <span
            className="rounded border border-red-500/60 bg-red-500/20 px-1.5 py-0.5 font-mono text-[10px] font-bold text-red-300"
            data-testid="passed-health-stale"
            title="No bar evaluated for over an hour plus the late-open bound"
          >
            STALE
          </span>
        ) : null}
        <span className="text-terminal-muted">
          Last evaluated bar{" "}
          <span
            className="font-mono text-terminal-text"
            data-testid="passed-health-last-bar"
            title={formatUtc(beat?.last_eval_bar)}
          >
            {lastBar}
          </span>
        </span>
        <span className="text-terminal-muted">
          lag{" "}
          <span className="font-mono text-terminal-text" data-testid="passed-health-lag">
            {fmtLag(beat?.last_eval_lag_seconds)}
          </span>
        </span>
        <span className="text-terminal-muted">
          evaluated{" "}
          <span
            className={`font-mono ${stale ? "text-red-300" : "text-terminal-text"}`}
            data-testid="passed-health-since"
          >
            {beat?.seconds_since_last_eval == null
              ? "—"
              : `${fmtLag(beat.seconds_since_last_eval)} ago`}
          </span>
        </span>
        <span className="text-terminal-muted" title={missedTitle}>
          Missed signals{since ? " since 2026-10-21 00:00 UTC" : ""}{" "}
          <span
            className={`font-mono ${missedShown ? "text-amber-300" : "text-terminal-text"}`}
            data-testid="passed-health-missed"
            title={missedTitle}
          >
            {count(missedShown)}
          </span>
        </span>
        <span className="text-terminal-muted">
          Unevaluated bars{" "}
          <span
            className={`font-mono ${snapshot.unevaluated_bars ? "text-amber-300" : "text-terminal-text"}`}
            data-testid="passed-health-unevaluated"
          >
            {count(snapshot.unevaluated_bars)}
          </span>
        </span>
        <span className="text-terminal-muted">
          Bars owed{" "}
          <span className="font-mono text-terminal-text" data-testid="passed-health-owed">
            {beat?.pending_bars == null
              ? "—"
              : owed.length
                ? `${beat.pending_bars} (${owed.map(([sym, n]) => `${coinLabel(sym)} ${n}`).join(", ")})`
                : String(beat.pending_bars)}
          </span>
        </span>
      </div>

      {notTradable.map((row) => (
        <div
          key={`${row.symbol}-${row.reason}`}
          className="mt-1 font-mono text-[10px] text-terminal-muted"
          data-testid="passed-health-not-tradable"
          title={`${row.bars} ${row.bars === 1 ? "bar" : "bars"} not evaluated (${row.reason}); last ${formatUtc(row.last)}`}
        >
          {coinLabel(row.symbol)} · <span className="text-amber-300">NOT TRADABLE: {row.label}</span>
        </div>
      ))}

      {missed.length ? (
        <details className="mt-1.5" data-testid="passed-health-missed-list">
          <summary className="cursor-pointer select-none text-terminal-muted">
            Recent missed · {missed.length}
          </summary>
          <ul className="mt-1 space-y-0.5 font-mono text-[10px]">
            {missed.map((row, index) => (
              <li
                key={`${row.recorded_at ?? ""}-${row.symbol ?? ""}-${row.strategy_id ?? ""}-${index}`}
                className="flex flex-wrap gap-x-3 text-terminal-text"
                data-testid="passed-health-missed-row"
              >
                <span className="text-terminal-muted" title={formatUtc(row.setup_bar_time)}>
                  {fmtTime(row.setup_bar_time)}
                </span>
                <span>{missedWhat(row)}</span>
                <span className="text-amber-300">{row.note || row.reason || "—"}</span>
                <span className="text-terminal-muted">lag {fmtLag(row.lag_seconds)}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
