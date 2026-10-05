import { Link } from "react-router-dom";
import type { V1WatcherViewRow } from "../types/market";
import { v1StatusClass, displayV1Status } from "../utils/screenerPresentation";
import type { ScreenerRow } from "../types/market";

function toneFromCode(code: string | null | undefined): ReturnType<typeof displayV1Status>["tone"] {
  const c = String(code || "").toUpperCase();
  if (c === "V1_ELIGIBLE") return "eligible";
  if (c === "V1_PAPER_OPEN") return "open";
  if (c === "DISCOVERY_ONLY") return "discovery";
  if (c === "V1_DATA_UNAVAILABLE") return "unavailable";
  if (c.startsWith("V1_BLOCKED")) return "blocked";
  if (c.startsWith("V1_WAITING")) return "waiting";
  return "unavailable";
}

export function V1WatcherPanel({ rows }: { rows: V1WatcherViewRow[] | null | undefined }) {
  const list = rows && rows.length > 0 ? rows : null;

  return (
    <section
      className="shrink-0 border-b border-terminal-border/70 bg-terminal-panel/40 px-2 py-2"
      data-testid="v1-watcher-panel"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="font-mono text-[11px] font-semibold tracking-wide text-terminal-text">
            COMBO_02 v1 WATCHER
          </h2>
          <p className="text-[10px] text-terminal-muted">
            BTCUSDT • ETHUSDT • SOLUSDT — 1h setup • 4h/1h HTF gate • Path A
          </p>
        </div>
        <Link
          to="/paper"
          className="text-[10px] text-sky-300 underline-offset-2 hover:underline"
        >
          Open paper blotter
        </Link>
      </div>
      <p className="mt-1 text-[10px] text-terminal-muted">
        Status snapshot from V1PaperWatcher / evaluate_combination_at_bar (1h). Never derived
        from the 15m screener table. Paper opens live in the blotter (v1 watcher + optional
        RESEARCH_15M); this panel does not place orders or Telegram by itself.
      </p>
      {!list ? (
        <div className="mt-2 font-mono text-[10px] text-amber-200/90" data-testid="v1-watcher-unavailable">
          V1 DATA UNAVAILABLE — backend offline or watcher not seeded yet. Restart API / wait for 1h OHLCV.
        </div>
      ) : (
        <div className="mt-2 grid gap-2 md:grid-cols-3">
          {list.map((r) => {
            const tone = toneFromCode(r.watcher_status_code);
            return (
              <div
                key={r.symbol}
                className="rounded border border-terminal-border/70 bg-black/20 px-2 py-1.5 font-mono text-[10px]"
                data-testid={`v1-watcher-card-${r.symbol}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-terminal-text">{r.symbol}</span>
                  <span className="text-terminal-muted">
                    {(r.tier || "—").toUpperCase()}
                    {r.risk_label ? ` • ${r.risk_label}` : ""}
                  </span>
                </div>
                <div className="mt-1 grid grid-cols-2 gap-x-2 gap-y-0.5 text-terminal-muted">
                  <span>1h: {r.trend_1h || "—"}</span>
                  <span>4h: {r.trend_4h || "—"}</span>
                  <span>HTF: {r.htf_alignment || "—"}</span>
                  <span>BOS: {r.bos_status || "—"}</span>
                </div>
                <div className={`mt-1 ${v1StatusClass(tone)}`}>
                  Watcher: {r.watcher_status || "V1 DATA UNAVAILABLE"}
                </div>
                <div className="mt-0.5 text-terminal-muted">
                  Paper: {r.paper_label || "no open v1 position"}
                </div>
                <div className="mt-0.5 text-[9px] text-terminal-muted/80">
                  Last 1h bar: {r.last_closed_1h_bar || "—"}
                  {r.last_evaluated_at_utc
                    ? ` · eval ${r.last_evaluated_at_utc}`
                    : ""}
                </div>
                {r.open_trade_id ? (
                  <Link
                    to="/paper"
                    className="mt-1 inline-block text-sky-300 underline-offset-2 hover:underline"
                  >
                    V1 paper trade open — view position
                  </Link>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** Compact link when a screener row has an open v1 paper trade. */
export function V1PaperOpenHint({ row }: { row: ScreenerRow }) {
  const hint = row.v1_paper_trade;
  if (!hint?.open) return null;
  return (
    <Link
      to={hint.href || "/paper"}
      className="mt-0.5 block truncate text-[9px] text-sky-300 underline-offset-2 hover:underline"
      title={hint.label || "V1 paper trade open"}
      onClick={(e) => e.stopPropagation()}
    >
      {hint.label || "V1 paper trade open — view position"}
    </Link>
  );
}
