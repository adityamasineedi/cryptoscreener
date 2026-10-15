/** Read-only map of the backtest page. Does not create signals or trades. */

type Props = {
  strategyLabel: string;
  symbols: number;
  ohlcvBars: number | null;
  runs: number;
  trades: number;
};

function Box({
  title,
  detail,
  className,
}: {
  title: string;
  detail: string;
  className: string;
}) {
  return (
    <div
      className={`flex h-[88px] flex-col items-center justify-center rounded-sm border border-[#3d6cb5] bg-[#0c2348] px-3 text-center ${className}`}
    >
      <div className="font-mono text-sm font-semibold tracking-wide text-white">
        {title}
      </div>
      <div className="mt-1 font-mono text-[10px] text-sky-200/80">{detail}</div>
    </div>
  );
}

function Rel({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-1 py-1">
      <div className="h-4 w-px bg-slate-300" />
      <span className="rounded bg-black px-1.5 py-0.5 font-mono text-[10px] text-white">
        {label}
      </span>
      <div className="flex flex-col items-center text-slate-200">
        <span className="text-[10px] leading-none">○</span>
        <span className="text-xs leading-none">⋀</span>
      </div>
    </div>
  );
}

export function BacktestDataMap({
  strategyLabel,
  symbols,
  ohlcvBars,
  runs,
  trades,
}: Props) {
  const bars =
    ohlcvBars == null ? "Postgres bars" : `${ohlcvBars.toLocaleString()} bars`;
  return (
    <section
      aria-label="Backtest data map"
      className="mb-4 shrink-0 overflow-x-auto rounded border border-terminal-border/80 bg-[#1c1f24] p-4"
    >
      <div className="grid min-w-[720px] grid-cols-3 gap-x-8 gap-y-1">
        <Box
          title="OHLCV"
          detail={`${symbols} symbol${symbols === 1 ? "" : "s"} · ${bars}`}
          className=""
        />
        <Box title="STRATEGIES" detail={strategyLabel} className="" />
        <Box
          title="RESEARCH_EXPERIMENTS"
          detail={runs ? "this screen's run" : "no run yet"}
          className=""
        />

        <Rel label="produces" />
        <Rel label="generates" />
        <div className="flex flex-col items-center gap-1 py-1">
          <div className="h-4 w-px bg-slate-300" />
          <div className="flex gap-1">
            <span className="rounded bg-black px-1.5 py-0.5 font-mono text-[10px] text-white">
              evaluated_by
            </span>
            <span className="rounded bg-black px-1.5 py-0.5 font-mono text-[10px] text-white">
              tracks
            </span>
          </div>
          <div className="flex flex-col items-center text-slate-200">
            <span className="text-[10px] leading-none">○</span>
            <span className="text-xs leading-none">⋀</span>
          </div>
        </div>

        <Box
          title="FEATURE_EVENTS"
          detail="closed-bar analytics only"
          className=""
        />
        <Box
          title="LIVE_SIGNALS"
          detail="not written from this page"
          className=""
        />
        <Box
          title="BACKTEST_RUNS"
          detail={runs ? `${runs} loaded` : "run backtest to fill"}
          className=""
        />

        <div />
        <div />
        <div className="flex flex-col items-center">
          <Rel label="contains" />
          <Box
            title="BACKTEST_TRADES"
            detail={trades ? `${trades.toLocaleString()} trades` : "none yet"}
            className="w-full"
          />
        </div>
      </div>
    </section>
  );
}
