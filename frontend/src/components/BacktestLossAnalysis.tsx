import { moneyTone } from "../utils/moneyTone";
import { useEffect, useState, type ReactNode } from "react";
import {
  fetchBacktestLossAnalysis,
  type LossAnalysisResponse,
  type LossAnalysisTrade,
  type LossAnalysisWhatIf,
} from "../api/client";

/**
 * Loss analysis for one cell of a finished single-strategy backtest job.
 * Display only: it reads GET .../backtest/loss-analysis and never changes a
 * trade, starts a run, or sends paper/live/Telegram anything.
 */

/** Plain-English label and tooltip for each primary reason and flag the endpoint sends. */
export const LOSS_REASON_TEXT: Record<string, { label: string; tip: string }> = {
  FEES_FLIPPED: {
    label: "Fees turned a winner into a loss",
    tip: "Gross P&L was positive; fees made the trade a net loss.",
  },
  GAVE_BACK: {
    label: "Gave back an open profit",
    tip: "The trade moved meaningfully in favour before reversing to the stop.",
  },
  AGAINST_CONTEXT: {
    label: "Entered against trend/structure",
    tip: "At the signal bar the swing structure or trend pointed the other way.",
  },
  WRONG_RANGE_HALF: {
    label: "Entered in the wrong half of the range",
    tip: "A long entered in the upper half (or a short in the lower half) of the prior range.",
  },
  LATE_STOP: {
    label: "Setup failed early, stop hit late",
    tip: "Price went against the trade early and never recovered; the stop only filled much later.",
  },
  STOP_BEFORE_PROGRESS: {
    label: "Stopped before any real progress",
    tip: "The stop was hit before the trade made meaningful progress toward TP1.",
  },
  INSUFFICIENT_DATA: {
    label: "Not enough data to classify",
    tip: "The bars needed to classify this loss were not available.",
  },
  CHASED_ENTRY: {
    label: "Chased entry",
    tip: "The fill was well beyond the signal level (entered after the move had already run).",
  },
  TIGHT_STOP: {
    label: "Stop tighter than 1×ATR",
    tip: "The stop distance was less than one ATR at entry.",
  },
  WIDE_STOP: {
    label: "Wide stop",
    tip: "The stop distance was unusually wide for this cell (several ATR).",
  },
  GAP_EXIT: {
    label: "Gap exit",
    tip: "The exit filled on a bar that opened away from the previous close.",
  },
  GAP_THROUGH_STOP: {
    label: "Gapped through the stop",
    tip: "The bar opened beyond the stop, so the fill was worse than the stop price.",
  },
};

function reasonText(code: string): { label: string; tip: string } {
  return LOSS_REASON_TEXT[code] ?? { label: code, tip: code };
}

function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function money(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${v.toFixed(2)}`;
}

/** Fees as a cost, like the blotter: always "-$x". */
function feeMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return `-$${Math.abs(v).toFixed(2)}`;
}

/** A 0..1 share as a percent. */
function share(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

/** A value already in percent units (the endpoint's *_pct fields). */
function pctUnits(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v) || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

function fmtTime(v: string | null | undefined): string {
  if (!v) return "—";
  return v.replace("T", " ").replace(/\+00:00$/, " UTC").replace(/Z$/, " UTC");
}

/** Gain green, loss red, $0.00 neutral grey (owner D206). */
function moneyCls(v: number | null | undefined): string {
  return moneyTone(v);
}

const PAGE_SIZE = 50;

type FetchState =
  | { key: string; status: "loading" }
  | { key: string; status: "ok"; data: LossAnalysisResponse }
  | { key: string; status: "error"; error: string };

export type BacktestLossAnalysisProps = {
  /** The finished job the viewed cell belongs to. Analysis is keyed by job + cell. */
  jobId: string;
  symbol: string;
  timeframe: string;
  /** Open (and fetching) — kept by the parent so it survives a cell change. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Select this trade in the blotter / chart overlay. */
  onSelectTrade: (trade: LossAnalysisTrade) => void;
  isTradeSelected?: (trade: LossAnalysisTrade) => boolean;
};

export function BacktestLossAnalysis({
  jobId,
  symbol,
  timeframe,
  open,
  onOpenChange,
  onSelectTrade,
  isTradeSelected,
}: BacktestLossAnalysisProps) {
  const key = `${jobId}|${symbol}|${timeframe}`;
  const [state, setState] = useState<FetchState | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!open) {
      setState(null);
      return;
    }
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    let alive = true;
    setState({ key, status: "loading" });
    fetchBacktestLossAnalysis(symbol, timeframe, { signal: ctrl?.signal })
      .then((data) => {
        if (!alive) return;
        const otherCell = data.symbol !== symbol || data.timeframe !== timeframe;
        const otherJob = data.job_id != null && data.job_id !== jobId;
        if (otherCell || otherJob) {
          setState({
            key,
            status: "error",
            error: `The server answered for ${data.symbol} ${data.timeframe}${
              otherJob ? ` (job ${data.job_id})` : ""
            }, not ${symbol} ${timeframe} of job ${jobId}. Not shown.`,
          });
          return;
        }
        setState({ key, status: "ok", data });
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setState({
          key,
          status: "error",
          error: err instanceof Error ? err.message : String(err),
        });
      });
    return () => {
      alive = false;
      ctrl?.abort();
    };
  }, [open, key, jobId, symbol, timeframe, reload]);

  // Never render a state that belongs to another job or cell.
  const current = state && state.key === key ? state : null;

  return (
    <section
      className="mt-3 rounded border border-terminal-border/60 p-2"
      data-testid="backtest-loss-analysis"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[12px] font-semibold text-terminal-text">
          Loss analysis{" "}
          <span className="font-mono text-[10px] font-normal text-terminal-muted">
            {symbol} · {timeframe} · research only, display only
          </span>
        </h3>
        <div className="flex gap-2">
          {open && current?.status !== "loading" ? (
            <button
              type="button"
              className="rounded border border-terminal-border px-2 py-0.5 text-[11px] text-terminal-muted hover:text-terminal-text"
              onClick={() => setReload((n) => n + 1)}
            >
              Refresh
            </button>
          ) : null}
          <button
            type="button"
            className="rounded border border-terminal-accent/50 px-2 py-0.5 text-[11px] text-terminal-accent hover:bg-terminal-accent/10"
            aria-expanded={open}
            onClick={() => onOpenChange(!open)}
          >
            {open ? "Hide loss analysis" : "Analyze losses"}
          </button>
        </div>
      </div>
      {!open ? (
        <p className="mt-1 text-[11px] text-terminal-muted">
          Reads why this cell&apos;s losing trades lost. It changes no trade and starts nothing.
        </p>
      ) : !current || current.status === "loading" ? (
        <p className="mt-2 text-[11px] text-terminal-muted" data-testid="loss-analysis-loading">
          Analyzing losses for {symbol} {timeframe}…
        </p>
      ) : current.status === "error" ? (
        <div
          className="mt-2 rounded border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-200"
          data-testid="loss-analysis-error"
          role="alert"
        >
          Loss analysis not available: {current.error}
        </div>
      ) : (
        <LossAnalysisBody
          data={current.data}
          onSelectTrade={onSelectTrade}
          isTradeSelected={isTradeSelected}
        />
      )}
    </section>
  );
}

function LossAnalysisBody({
  data,
  onSelectTrade,
  isTradeSelected,
}: {
  data: LossAnalysisResponse;
  onSelectTrade: (trade: LossAnalysisTrade) => void;
  isTradeSelected?: (trade: LossAnalysisTrade) => boolean;
}) {
  const s = data.summary;
  const lossDollars = s.by_reason.reduce((sum, r) => sum + Math.abs(Math.min(0, r.net_usd ?? 0)), 0);
  return (
    <div className="mt-2 space-y-3 text-[11px]">
      <div className="font-mono text-[10px] text-terminal-muted">
        {data.strategy_id || "—"} · {data.direction || "—"} · {fmtTime(data.period.start)} →{" "}
        {fmtTime(data.period.end)}
        {data.hindsight_bars != null ? ` · hindsight window ${data.hindsight_bars} bars` : ""}
      </div>

      <div
        className="grid grid-cols-2 gap-2 rounded border border-terminal-border/60 bg-black/20 p-2 term-md:grid-cols-5"
        data-testid="loss-analysis-summary"
      >
        <Stat label="Losers" value={String(s.losers)} sub={`of ${s.trades} trades`} cls="text-red-400" />
        <Stat
          label="Winners"
          value={String(s.winners)}
          sub={s.scratches ? `${s.scratches} scratch${s.scratches === 1 ? "" : "es"}` : undefined}
          cls="text-emerald-400"
        />
        <Stat label="Net" value={money(s.net_usd)} sub={`gross ${money(s.gross_usd)}`} cls={moneyCls(s.net_usd)} />
        <Stat label="Fees" value={feeMoney(s.fees_usd)} cls="text-terminal-muted" />
        <Stat
          label="Hindsight"
          value={`${s.held_would_have_won.count} · ${money(s.held_would_have_won.net_usd)}`}
          sub="losses that would have won if held"
          cls="text-amber-200"
          title="Hindsight: price later reached TP1 after the stop. Uses bars after the exit; not knowable at the time."
        />
      </div>

      <div className="grid gap-3 term-md:grid-cols-2">
        <div>
          <SectionTitle>Losses by reason</SectionTitle>
          <table className="w-full border-collapse text-left" data-testid="loss-analysis-by-reason">
            <thead className="text-[10px] uppercase tracking-wide text-terminal-muted">
              <tr>
                <th className="px-1 py-1">Reason</th>
                <th className="px-1 py-1">n</th>
                <th className="px-1 py-1">Net</th>
                <th className="px-1 py-1" title="Share of the losing dollars">% of loss $</th>
              </tr>
            </thead>
            <tbody>
              {!s.by_reason.length ? (
                <tr>
                  <td colSpan={4} className="px-1 py-1 text-terminal-muted">
                    No losing trades
                  </td>
                </tr>
              ) : null}
              {s.by_reason.map((r) => {
                const shareValue =
                  r.share_of_loss ??
                  (lossDollars > 0 && r.net_usd != null ? Math.abs(Math.min(0, r.net_usd)) / lossDollars : null);
                return (
                  <tr key={r.reason} className="border-t border-terminal-border/40">
                    <td className="px-1 py-1">
                      <ReasonChip code={r.reason} />
                    </td>
                    <td className="px-1 py-1 font-mono">{r.count}</td>
                    <td className={`px-1 py-1 font-mono ${moneyCls(r.net_usd)}`}>{money(r.net_usd)}</td>
                    <td className="px-1 py-1 font-mono">
                      <div className="flex items-center gap-1">
                        <div className="h-1.5 w-16 rounded bg-white/5">
                          <div
                            className="h-1.5 rounded bg-red-400/70"
                            style={{ width: `${Math.max(0, Math.min(1, shareValue ?? 0)) * 100}%` }}
                          />
                        </div>
                        {share(shareValue)}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {(s.non_discriminating_flags ?? []).length ? (
            <ul
              className="mt-1 space-y-0.5 text-[10px] text-terminal-muted"
              data-testid="loss-analysis-non-discriminating"
            >
              {(s.non_discriminating_flags ?? []).map((f) => (
                <li key={f.flag} title={f.flag}>
                  {reasonText(f.flag).label} is not used as a reason here: also present on{" "}
                  {share(f.winners_rate)} of winners ({share(f.losers_rate)} of losers)
                  {f.reason ? ` / ${f.reason}` : ""}.
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div>
          <SectionTitle>Losses by market context</SectionTitle>
          <table className="w-full border-collapse text-left" data-testid="loss-analysis-by-context">
            <thead className="text-[10px] uppercase tracking-wide text-terminal-muted">
              <tr>
                <th className="px-1 py-1">Structure at signal</th>
                <th className="px-1 py-1">n</th>
                <th className="px-1 py-1">Net</th>
              </tr>
            </thead>
            <tbody>
              {!s.by_context.length ? (
                <tr>
                  <td colSpan={3} className="px-1 py-1 text-terminal-muted">
                    —
                  </td>
                </tr>
              ) : null}
              {s.by_context.map((c) => (
                <tr key={c.structure} className="border-t border-terminal-border/40">
                  <td className="px-1 py-1 font-mono">{c.structure}</td>
                  <td className="px-1 py-1 font-mono">{c.count}</td>
                  <td className={`px-1 py-1 font-mono ${moneyCls(c.net_usd)}`}>{money(c.net_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {s.loss_weeks.length ? (
            <div className="mt-1 font-mono text-[10px] text-terminal-muted">
              Worst weeks:{" "}
              {s.loss_weeks
                .slice(0, 5)
                .map((w) => `${w.week} (${w.count}, ${money(w.net_usd)})`)
                .join(" · ")}
            </div>
          ) : null}
        </div>
      </div>

      <LossList
        title={`Losing trades (${data.losses.length})`}
        testId="loss-analysis-losses"
        trades={data.losses}
        empty="No losing trades in this cell."
        onSelectTrade={onSelectTrade}
        isTradeSelected={isTradeSelected}
      />

      {data.fee_flipped.length ? (
        <LossList
          title={`Fee-flipped trades (${data.fee_flipped.length}) — positive gross, negative net`}
          testId="loss-analysis-fee-flipped"
          trades={data.fee_flipped}
          empty=""
          onSelectTrade={onSelectTrade}
          isTradeSelected={isTradeSelected}
        />
      ) : null}

      <WhatIfTable whatIfs={data.what_ifs} />

      <div data-testid="loss-analysis-suggestions">
        <SectionTitle>Suggestions — research only</SectionTitle>
        {data.frozen_book ? (
          <p
            className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-amber-100"
            data-testid="loss-analysis-frozen-note"
          >
            {data.suggestions_note || "This book is frozen. Diagnostics only; no suggestions."}
          </p>
        ) : !data.suggestions.length ? (
          <p className="text-terminal-muted">{data.suggestions_note || "No suggestions for this cell."}</p>
        ) : (
          <ol className="list-decimal space-y-1 pl-5">
            {data.suggestions.map((sg, i) => (
              <li key={`${sg.rule}-${i}`} className="text-terminal-text">
                <span>{sg.label}</span>{" "}
                <span className="font-mono text-[10px] text-terminal-muted">
                  · {reasonText(sg.reason_bucket).label} · {sg.trades_affected ?? "—"} trades ·{" "}
                  <span className={moneyCls(sg.net_change_usd)}>{money(sg.net_change_usd)}</span> · winners lost{" "}
                  {sg.winners_lost ?? "—"}
                </span>{" "}
                <span className="rounded border border-amber-500/40 px-1 text-[9px] uppercase text-amber-200">
                  research only
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>

      <p className="text-[10px] text-terminal-muted" data-testid="loss-analysis-disclaimer">
        {data.disclaimer}
      </p>
    </div>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <div className="mb-1 text-[10px] uppercase tracking-wide text-terminal-muted">{children}</div>;
}

function Stat({
  label,
  value,
  sub,
  cls,
  title,
}: {
  label: string;
  value: string;
  sub?: string;
  cls?: string;
  title?: string;
}) {
  return (
    <div title={title}>
      <div className="text-[10px] uppercase tracking-wide text-terminal-muted">{label}</div>
      <div className={`font-mono text-sm ${cls ?? "text-terminal-text"}`}>{value}</div>
      {sub ? <div className="font-mono text-[10px] text-terminal-muted">{sub}</div> : null}
    </div>
  );
}

function ReasonChip({ code, flag = false }: { code: string; flag?: boolean }) {
  const t = reasonText(code);
  return (
    <span
      className={`inline-block rounded border px-1 text-[10px] ${
        flag
          ? "border-terminal-border text-terminal-muted"
          : "border-red-500/40 bg-red-500/10 text-red-200"
      }`}
      title={`${code}: ${t.tip}`}
      data-testid={flag ? "loss-flag-chip" : "loss-reason-chip"}
    >
      {t.label}
    </span>
  );
}

function LossList({
  title,
  testId,
  trades,
  empty,
  onSelectTrade,
  isTradeSelected,
}: {
  title: string;
  testId: string;
  trades: LossAnalysisTrade[];
  empty: string;
  onSelectTrade: (trade: LossAnalysisTrade) => void;
  isTradeSelected?: (trade: LossAnalysisTrade) => boolean;
}) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(trades.length / PAGE_SIZE));
  const safePage = Math.min(page, pages - 1);
  const shown = trades.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);
  return (
    <div data-testid={testId}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <SectionTitle>{title}</SectionTitle>
        {pages > 1 ? (
          <div className="flex items-center gap-1 font-mono text-[10px] text-terminal-muted">
            <button
              type="button"
              className="rounded border border-terminal-border px-1 disabled:opacity-40"
              disabled={safePage === 0}
              onClick={() => setPage(safePage - 1)}
            >
              Prev
            </button>
            <span>
              {safePage + 1}/{pages}
            </span>
            <button
              type="button"
              className="rounded border border-terminal-border px-1 disabled:opacity-40"
              disabled={safePage >= pages - 1}
              onClick={() => setPage(safePage + 1)}
            >
              Next
            </button>
          </div>
        ) : null}
      </div>
      {!trades.length ? <p className="text-terminal-muted">{empty}</p> : null}
      <ul className="space-y-1">
        {shown.map((t, i) => (
          <LossItem
            key={`${t.trade_id ?? ""}-${t.trade_no ?? ""}-${t.entry_time ?? ""}-${i}`}
            t={t}
            selected={isTradeSelected?.(t) ?? false}
            onSelect={() => onSelectTrade(t)}
          />
        ))}
      </ul>
    </div>
  );
}

function LossItem({
  t,
  selected,
  onSelect,
}: {
  t: LossAnalysisTrade;
  selected: boolean;
  onSelect: () => void;
}) {
  const m = t.metrics;
  const h = t.hindsight;
  return (
    <li
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      title="Show this trade in the blotter and on the chart"
      data-testid="loss-item"
      className={`cursor-pointer rounded border px-2 py-1.5 ${
        selected
          ? "border-terminal-accent/50 bg-terminal-accent/10"
          : "border-terminal-border/50 hover:bg-white/[0.03]"
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono">
        <span className="text-terminal-muted">#{t.trade_no ?? t.trade_id ?? "—"}</span>
        <span className="whitespace-nowrap">
          {fmtTime(t.entry_time)} → {fmtTime(t.exit_time)}
        </span>
        <span className="text-terminal-muted">{t.direction || ""}</span>
        <span>{t.outcome || "—"}</span>
        <span className={moneyCls(t.net_usd)}>{money(t.net_usd)}</span>
        <span className={moneyCls(t.r_net)}>{num(t.r_net)} R</span>
        <ReasonChip code={t.primary_reason} />
        {(t.flags ?? []).map((f) => (
          <ReasonChip key={f} code={f} flag />
        ))}
      </div>
      {t.explanation ? <div className="mt-0.5 text-terminal-text">{t.explanation}</div> : null}
      <div className="mt-0.5 font-mono text-[10px] text-terminal-muted" data-testid="loss-metrics">
        MFE to TP1 {pctUnits(m?.mfe_pct_to_tp1)} · MFE {num(m?.mfe_r_before_exit)} R · stop{" "}
        {num(m?.stop_distance_atr)}×ATR · hold {m?.hold_bars ?? "—"} bars · fees {num(m?.fees_r)} R
      </div>
      {h?.reached_tp1_after_exit ? (
        <div className="mt-0.5 text-[10px] text-amber-200" data-testid="loss-hindsight">
          Hindsight: price reached TP1 {h.bars_after_exit ?? "—"} bars after the stop
          {h.stop_needed_r != null ? `; a stop of ${num(h.stop_needed_r)} R would have held` : ""}.
        </div>
      ) : null}
    </li>
  );
}

function WhatIfTable({ whatIfs }: { whatIfs: LossAnalysisWhatIf[] }) {
  return (
    <div>
      <SectionTitle>What-ifs — research only, in-sample</SectionTitle>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left" data-testid="loss-analysis-what-ifs">
          <thead className="text-[10px] uppercase tracking-wide text-terminal-muted">
            <tr>
              <th className="px-1 py-1">What if</th>
              <th className="px-1 py-1">Trades</th>
              <th className="px-1 py-1">Net</th>
              <th className="px-1 py-1">Change vs actual</th>
              <th className="px-1 py-1">Winners lost</th>
            </tr>
          </thead>
          <tbody>
            {!whatIfs.length ? (
              <tr>
                <td colSpan={5} className="px-1 py-1 text-terminal-muted">
                  —
                </td>
              </tr>
            ) : null}
            {whatIfs.map((w) => (
              <tr
                key={w.id}
                className="border-t border-terminal-border/40"
                title={w.caveat || undefined}
                data-testid="what-if-row"
              >
                <td className="px-1 py-1">
                  {w.label}
                  {w.caveat && w.computable ? <span className="ml-1 text-terminal-muted">ⓘ</span> : null}
                </td>
                {w.computable ? (
                  <>
                    <td className="px-1 py-1 font-mono">{w.trades ?? "—"}</td>
                    <td className={`px-1 py-1 font-mono ${moneyCls(w.net_usd)}`}>{money(w.net_usd)}</td>
                    <td className={`px-1 py-1 font-mono ${moneyCls(w.delta_usd)}`}>{money(w.delta_usd)}</td>
                    <td className="px-1 py-1 font-mono">{w.winners_lost ?? "—"}</td>
                  </>
                ) : (
                  <td colSpan={4} className="px-1 py-1 text-terminal-muted" data-testid="what-if-not-computable">
                    Not computable{w.caveat ? `: ${w.caveat}` : ""}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
