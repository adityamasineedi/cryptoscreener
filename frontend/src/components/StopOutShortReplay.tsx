import { useEffect, useRef, useState } from "react";
import { fetchResearchOhlcvCandles, type StrategyTradeRow } from "../api/client";
import {
  favorableStopNote,
  formatReplayHit,
  isLongStopOut,
  replayShortsAtLongStops,
  replaySummary,
  STOP_OUT_REPLAY_LABEL,
  type StopOutShortReplay as ReplayResult,
} from "../backtest/stopOutShortReplay";

type Props = {
  trades: StrategyTradeRow[];
  symbol: string;
  timeframe: string;
  periodEnd?: string | null;
  /** Holdout-guarded strategy (daily / 4h trend books): candles stop before 2026-07-15. */
  holdout?: boolean;
};

function formatStopTime(value: string): string {
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return value || "—";
  const date = new Date(ms);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = date.getUTCDate();
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${day} ${months[date.getUTCMonth()]} ${hh}:${mm}`;
}

function formatEntry(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const digits = Math.abs(value) >= 100 ? 2 : Math.abs(value) >= 10 ? 3 : 5;
  return value.toFixed(digits);
}

export function StopOutShortReplay({ trades, symbol, timeframe, periodEnd, holdout = false }: Props) {
  const stops = trades.filter(isLongStopOut);
  const [replay, setReplay] = useState<ReplayResult | null>(null);
  const [archived, setArchived] = useState<ReplayResult | null>(null);
  const [status, setStatus] = useState<"IDLE" | "LOADING" | "OK" | "ERROR">("IDLE");
  const [error, setError] = useState<string | null>(null);

  const stopKey = stops
    .map((trade) =>
      [trade.exit_time, trade.entry_price, trade.stop_price, trade.tp1].join("|"),
    )
    .join(";");
  // Each job poll hands over new trade arrays; refetch only when the stop-outs change.
  const tradesRef = useRef(trades);
  tradesRef.current = trades;

  useEffect(() => {
    const stopTrades = tradesRef.current.filter(isLongStopOut);
    const start = stopTrades[0]?.exit_time || stopTrades[0]?.signal_time || "";
    if (!stopTrades.length || !start) {
      setReplay(null);
      setStatus("IDLE");
      return;
    }
    let alive = true;
    setStatus("LOADING");
    setError(null);
    const end = periodEnd || stopTrades[stopTrades.length - 1]?.exit_time || start;
    (async () => {
      try {
        const data = await fetchResearchOhlcvCandles({
          symbol,
          timeframe,
          start,
          end,
          pad_bars: 8,
          holdout,
        });
        if (!alive) return;
        if (data.status !== "OK" || !data.candles.length) {
          setReplay(null);
          setStatus("ERROR");
          setError(data.reason || "No candles for the stop-out replay");
          return;
        }
        setReplay(replayShortsAtLongStops(stopTrades, data.candles, "v2_stop_bar_counts"));
        setArchived(replayShortsAtLongStops(stopTrades, data.candles, "v1_skip_stop_bar"));
        setStatus("OK");
      } catch (err) {
        if (!alive) return;
        setReplay(null);
        setStatus("ERROR");
        setError(err instanceof Error ? err.message : "Stop-out replay failed");
      }
    })();
    return () => {
      alive = false;
    };
  }, [symbol, timeframe, periodEnd, stopKey, holdout]);

  if (!stops.length) return null;

  const note = replay ? favorableStopNote(replay.rows) : null;

  return (
    <details
      data-testid="stop-out-replay"
      data-label={STOP_OUT_REPLAY_LABEL}
      className="mt-4 rounded border border-rose-500/30 bg-rose-500/5 p-3"
    >
      <summary className="cursor-pointer text-sm font-semibold text-terminal-text">
        Stop-out short replay · {STOP_OUT_REPLAY_LABEL}
      </summary>
      <div
        data-testid="stop-out-replay-warning"
        className="mt-2 max-w-3xl rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-100"
      >
        Not a tradeable short book. The replay has no entry rule of its own and charges no
        fees, slippage or funding. Fully costed, the stop-reversal short is negative: in the
        LCSC study (2026-10-15) every costed variant lost on BTC and ETH (ETH 1R −20.0R,
        −30.4R when the stop bar counts). Do not add these R to any result.
      </div>
      <p className="mt-1 max-w-3xl text-[11px] text-terminal-muted">
        COMBO_02 long rules are unchanged. This only replays a short at each long stop.
        The short fills at the long stop. Its stop sits at the long entry, so the risk
        distance is the same. The full target is the long target mirrored below the short
        entry. The 1R target is one risk distance below the short entry. If the stop and a
        target are both touched in one bar, the stop counts. The walk starts on the
        long-stop bar itself (replay v2); the archived v1 replay started one bar later.
      </p>
      {status === "LOADING" ? (
        <div className="mt-2 font-mono text-[11px] text-terminal-muted">Replaying stop-outs…</div>
      ) : null}
      {status === "ERROR" ? (
        <div className="mt-2 font-mono text-[11px] text-rose-200">{error}</div>
      ) : null}
      {replay ? (
        <>
          <div className="mt-3 min-w-0 overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse text-left text-[11px]">
              <thead className="text-[10px] uppercase tracking-wide text-terminal-muted">
                <tr>
                  <th className="px-2 py-1">Long stop</th>
                  <th className="px-2 py-1">Short entry</th>
                  <th className="px-2 py-1">Full target</th>
                  <th className="px-2 py-1">1R target</th>
                </tr>
              </thead>
              <tbody>
                {replay.rows.map((row) => (
                  <tr key={`${row.longStopTime}-${row.shortEntry}`} className="border-t border-terminal-border/40">
                    <td className="px-2 py-1.5 font-mono">{formatStopTime(row.longStopTime)}</td>
                    <td className="px-2 py-1.5 font-mono">{formatEntry(row.shortEntry)}</td>
                    <td className="px-2 py-1.5 font-mono">{formatReplayHit(row.full)}</td>
                    <td className="px-2 py-1.5 font-mono">{formatReplayHit(row.oneR)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-terminal-text">Uncosted (v2): {replaySummary(replay)}</p>
          {archived ? (
            <p className="mt-1 text-[11px] text-terminal-muted">
              Archived v1 replay (skipped the stop bar): {replaySummary(archived)}
            </p>
          ) : null}
          {note ? <p className="mt-1 text-[11px] text-terminal-muted">{note}</p> : null}
        </>
      ) : null}
    </details>
  );
}
