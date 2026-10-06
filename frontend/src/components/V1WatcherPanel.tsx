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
      className="v1-watcher-panel flex shrink-0 flex-col overflow-hidden border-b border-terminal-border/70 bg-terminal-panel/40 px-2 py-1"
      data-testid="v1-watcher-panel"
      title="Status snapshot from V1PaperWatcher / evaluate_combination_at_bar (1h). Never derived from the 15m screener table. Paper opens live in the blotter (v1 watcher + optional RESEARCH_15M); this panel does not place orders or Telegram by itself."
    >
      <div className="flex shrink-0 flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
        <div className="min-w-0">
          <h2 className="font-mono text-[11px] font-semibold tracking-wide text-terminal-text">
            COMBO_02 v1 WATCHER
          </h2>
          <p className="truncate text-[10px] text-terminal-muted">
            BTC • ETH • SOL — 1h + HTF Path A
            {list ? ` · ${list.length}` : ""}
            <span className="text-terminal-muted/70"> · not from 15m table</span>
          </p>
        </div>
        <Link
          to="/paper"
          className="shrink-0 text-[10px] text-sky-300 underline-offset-2 hover:underline"
        >
          Paper blotter
        </Link>
      </div>
      {!list ? (
        <div className="mt-1 shrink-0 font-mono text-[10px] text-amber-200/90" data-testid="v1-watcher-unavailable">
          V1 DATA UNAVAILABLE — backend offline or watcher not seeded yet. Restart API / wait for 1h OHLCV.
        </div>
      ) : (
        <div
          className="v1-watcher-scroll mt-1 min-h-0 flex-1 overflow-y-auto overscroll-contain pr-0.5"
          data-testid="v1-watcher-scroll"
        >
          {/* 2-up grid keeps two cards readable; 3rd (SOL) scrolls if needed */}
          <div className="grid grid-cols-2 gap-1.5">
            {list.map((r) => {
              const tone = toneFromCode(r.watcher_status_code);
              return (
                <div
                  key={r.symbol}
                  className="rounded border border-terminal-border/70 bg-black/20 px-1.5 py-1 font-mono text-[10px] leading-tight"
                  data-testid={`v1-watcher-card-${r.symbol}`}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-semibold text-terminal-text">{r.symbol}</span>
                    <span className="truncate text-[9px] text-terminal-muted">
                      {(r.tier || "—").toUpperCase()}
                      {r.risk_label ? ` • ${r.risk_label}` : ""}
                    </span>
                  </div>
                  <div className="mt-0.5 grid grid-cols-2 gap-x-1.5 gap-y-0 text-[9px] text-terminal-muted">
                    <span>1h: {r.trend_1h || "—"}</span>
                    <span>4h: {r.trend_4h || "—"}</span>
                    <span>HTF: {r.htf_alignment || "—"}</span>
                    <span>BOS: {r.bos_status || "—"}</span>
                  </div>
                  <div className={`mt-0.5 truncate ${v1StatusClass(tone)}`}>
                    {r.watcher_status || "V1 DATA UNAVAILABLE"}
                  </div>
                  <div className="mt-0.5 truncate text-[9px] text-terminal-muted">
                    Paper: {r.paper_label || "no open v1"}
                    {r.last_closed_1h_bar ? ` · ${r.last_closed_1h_bar}` : ""}
                  </div>
                  {r.open_trade_id ? (
                    <Link
                      to="/paper"
                      className="mt-0.5 inline-block text-[9px] text-sky-300 underline-offset-2 hover:underline"
                    >
                      View v1 position
                    </Link>
                  ) : null}
                </div>
              );
            })}
          </div>
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
