import { useEffect, useState } from "react";
import {
  fetchDiscoveryRunRanking,
  fetchLatestDiscoveryRun,
  type DiscoveryRankingResponse,
  type DiscoveryRankingRow,
} from "../api/client";

type PanelState =
  | { status: "loading" }
  | { status: "empty" }
  | { status: "error"; message: string }
  | { status: "ready"; payload: DiscoveryRankingResponse };

type DiscoveryTrade = {
  symbol?: string | null;
  combination_id?: string | null;
  direction?: string | null;
  entry_time?: string | null;
  signal_time?: string | null;
  entry?: number | null;
  entry_price?: number | null;
  stop?: number | null;
  stop_price?: number | null;
  target?: number | null;
  target_price?: number | null;
  tp1?: number | null;
  outcome?: string | null;
  r?: number | null;
  r_multiple?: number | null;
};

type TradesState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; rows: DiscoveryTrade[] };

function sentNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function metricText(value: number | null | undefined): string {
  return sentNumber(value) ? String(value) : "—";
}

function factorText(row: DiscoveryRankingRow): string {
  if (row.oos_profit_factors != null) {
    if (row.oos_profit_factors.length === 0) return "—";
    return row.oos_profit_factors.map((value) => metricText(value)).join(", ");
  }
  return metricText(row.oos_profit_factor);
}

type PatternSymbol = {
  symbol?: string;
  cluster_count?: number | null;
  clusters?: unknown;
  experimental?: boolean;
  promotion_status?: string | null;
  is_strategy?: boolean;
};

type PatternsSummary = {
  experimental?: boolean;
  promotion_status?: string | null;
  symbols?: PatternSymbol[];
  by_symbol?: Record<string, PatternSymbol>;
};

function readPatterns(payload: DiscoveryRankingResponse): PatternsSummary | PatternSymbol[] | null {
  const value = (payload as DiscoveryRankingResponse & { patterns?: unknown }).patterns;
  if (value == null) return null;
  if (Array.isArray(value)) return value as PatternSymbol[];
  if (typeof value === "object") return value as PatternsSummary;
  return null;
}

function patternSymbols(patterns: PatternsSummary | PatternSymbol[]): PatternSymbol[] {
  if (Array.isArray(patterns)) return patterns;
  if (Array.isArray(patterns.symbols)) return patterns.symbols;
  const bySymbol = patterns.by_symbol;
  if (bySymbol && typeof bySymbol === "object") {
    return Object.entries(bySymbol).map(([symbol, entry]) => ({
      ...entry,
      symbol: entry?.symbol || symbol,
    }));
  }
  return [];
}

function isExperimental(flag: boolean | undefined, status: string | null | undefined): boolean {
  return flag === true || status === "EXPERIMENTAL";
}

function clusterCountText(entry: PatternSymbol): string {
  if (sentNumber(entry.cluster_count)) return String(entry.cluster_count);
  return "—";
}

function researchPassedText(row: DiscoveryRankingRow): string | null {
  if (row.research_passed === undefined) return null;
  if (row.research_passed === true) return "research_passed: true";
  if (row.research_passed === false) return "research_passed: false";
  return "research_passed: —";
}

type EmbargoSummary = {
  excluded_train_indices?: unknown;
  embargo_indices?: unknown;
  disclaimer?: unknown;
};

function embargoDisclaimerText(value: unknown): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  return value;
}

function readEmbargo(payload: DiscoveryRankingResponse): EmbargoSummary | null {
  const value = (payload as DiscoveryRankingResponse & { embargo?: unknown }).embargo;
  if (value == null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as EmbargoSummary;
}

function indexCountText(value: unknown): string {
  if (Array.isArray(value)) return String(value.length);
  return "—";
}

function blankText(value: string | number | null | undefined): string {
  if (value == null) return "";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  return value;
}

function firstPresent<T>(...values: Array<T | null | undefined>): T | null | undefined {
  for (const value of values) {
    if (value != null) return value;
  }
  return null;
}

function tradeRows(body: unknown): DiscoveryTrade[] {
  if (Array.isArray(body)) return body as DiscoveryTrade[];
  if (body && typeof body === "object" && Array.isArray((body as { trades?: unknown }).trades)) {
    return (body as { trades: DiscoveryTrade[] }).trades;
  }
  return [];
}

async function fetchDiscoveryTrades(runId: string, signal: AbortSignal): Promise<DiscoveryTrade[]> {
  const path = `/api/research/market-structure-discovery-v1/${encodeURIComponent(runId)}/trades`;
  const base = import.meta.env.VITE_API_URL || "";
  const res = await fetch(`${base}${path}`, { signal });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status}`);
  }
  return tradeRows(await res.json());
}

export function DiscoveryRankingPanel() {
  const [state, setState] = useState<PanelState>({ status: "loading" });
  const [trades, setTrades] = useState<TradesState>({ status: "idle" });

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    setTrades({ status: "idle" });
    fetchLatestDiscoveryRun({ signal: controller.signal })
      .then(async (latest) => {
        if (!latest.run_id) {
          setState({ status: "empty" });
          return;
        }
        const payload = await fetchDiscoveryRunRanking(latest.run_id, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setState({ status: "ready", payload });
        setTrades({ status: "loading" });
        try {
          const rows = await fetchDiscoveryTrades(latest.run_id, controller.signal);
          if (controller.signal.aborted) return;
          setTrades({ status: "ready", rows });
        } catch (reason) {
          if ((reason as Error)?.name === "AbortError" || controller.signal.aborted) return;
          const message = reason instanceof Error ? reason.message : String(reason);
          setTrades({ status: "error", message });
        }
      })
      .catch((reason) => {
        if ((reason as Error)?.name === "AbortError" || controller.signal.aborted) return;
        const message = reason instanceof Error ? reason.message : String(reason);
        if (message.includes("/ranking") && message.includes("404")) {
          setState({ status: "empty" });
          return;
        }
        setState({ status: "error", message });
      });
    return () => controller.abort();
  }, []);

  return (
    <div data-testid="discovery-ranking-panel">
      <div className="text-[11px] uppercase tracking-[0.18em] text-amber-300">
        Research only — read only
      </div>
      <h2 className="mt-1 font-display text-xl text-terminal-text">Market structure discovery</h2>
      <p className="mt-1 max-w-4xl text-sm text-terminal-muted">
        Stored research ranking. Rows stay experimental and cannot open paper, live, or Telegram
        actions.
      </p>

      {state.status === "loading" ? (
        <p className="mt-4 text-sm text-terminal-muted">Loading discovery ranking…</p>
      ) : null}
      {state.status === "error" ? (
        <p className="mt-4 text-sm text-red-300">{state.message}</p>
      ) : null}
      {state.status === "empty" ? (
        <p className="mt-4 text-sm text-terminal-muted" data-testid="discovery-ranking-empty">
          no discovery run
        </p>
      ) : null}
      {state.status === "ready" ? (
        <>
          <RankingTable payload={state.payload} />
          <TradesTable state={trades} />
          <PatternSummary payload={state.payload} />
          <EmbargoSummaryView payload={state.payload} />
        </>
      ) : null}
    </div>
  );
}

function PatternSummary({ payload }: { payload: DiscoveryRankingResponse }) {
  const patterns = readPatterns(payload);
  if (patterns == null) {
    return (
      <p className="mt-4 text-sm text-terminal-muted" data-testid="discovery-pattern-summary">
        no pattern summary
      </p>
    );
  }
  const symbols = patternSymbols(patterns);
  const summaryFlag = Array.isArray(patterns)
    ? false
    : isExperimental(patterns.experimental, patterns.promotion_status);

  return (
    <div className="mt-4" data-testid="discovery-pattern-summary">
      <h3 className="text-[11px] uppercase tracking-[0.18em] text-terminal-muted">Pattern summary</h3>
      {summaryFlag ? <p className="mt-2 text-sm text-amber-300">EXPERIMENTAL</p> : null}
      {symbols.length > 0 ? (
        <ul className="mt-2 space-y-1 text-sm">
          {symbols.map((entry, index) => {
            const experimental = isExperimental(entry.experimental, entry.promotion_status);
            return (
              <li key={`${entry.symbol ?? "symbol"}-${index}`} className="font-mono">
                <span>{entry.symbol || "—"}</span>
                <span className="text-terminal-muted"> · clusters </span>
                <span>{clusterCountText(entry)}</span>
                {experimental ? <span className="ml-2 text-amber-300">EXPERIMENTAL</span> : null}
                {entry.is_strategy === false ? (
                  <span className="ml-2 text-terminal-muted">not a strategy</span>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

function EmbargoSummaryView({ payload }: { payload: DiscoveryRankingResponse }) {
  const embargo = readEmbargo(payload);
  if (embargo == null) {
    return (
      <p className="mt-4 text-sm text-terminal-muted" data-testid="discovery-embargo-summary">
        no embargo summary
      </p>
    );
  }

  const disclaimer = embargoDisclaimerText(embargo.disclaimer);

  return (
    <div className="mt-4" data-testid="discovery-embargo-summary">
      <h3 className="text-[11px] uppercase tracking-[0.18em] text-terminal-muted">Embargo summary</h3>
      <p className="mt-2 text-sm font-mono">
        <span>excluded training-bar count: {indexCountText(embargo.excluded_train_indices)}</span>
      </p>
      <p className="text-sm font-mono">
        <span>embargo bar count: {indexCountText(embargo.embargo_indices)}</span>
      </p>
      {disclaimer ? (
        <p className="mt-2 text-sm text-terminal-muted" data-testid="discovery-embargo-disclaimer">
          {disclaimer}
        </p>
      ) : null}
      <p className="mt-2 text-sm text-amber-300">EXPERIMENTAL</p>
      <p className="text-sm text-terminal-muted">not approved</p>
    </div>
  );
}

function TradesTable({ state }: { state: TradesState }) {
  if (state.status === "idle" || state.status === "loading") {
    return (
      <p className="mt-4 text-sm text-terminal-muted" data-testid="discovery-trades">
        Loading trades…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p className="mt-4 text-sm text-red-300" data-testid="discovery-trades">
        {state.message}
      </p>
    );
  }
  if (state.rows.length === 0) {
    return (
      <p className="mt-4 text-sm text-terminal-muted" data-testid="discovery-trades">
        no trades
      </p>
    );
  }

  return (
    <div className="mt-4" data-testid="discovery-trades">
      <h3 className="text-[11px] uppercase tracking-[0.18em] text-terminal-muted">Trades</h3>
      <div className="mt-2 overflow-x-auto rounded border border-terminal-border">
        <table className="min-w-full text-left text-xs">
          <thead className="text-terminal-muted">
            <tr>
              <th className="px-2 py-1.5 font-medium">Symbol</th>
              <th className="px-2 py-1.5 font-medium">Combination</th>
              <th className="px-2 py-1.5 font-medium">Direction</th>
              <th className="px-2 py-1.5 font-medium">Entry time</th>
              <th className="px-2 py-1.5 font-medium">Entry</th>
              <th className="px-2 py-1.5 font-medium">Stop</th>
              <th className="px-2 py-1.5 font-medium">Target</th>
              <th className="px-2 py-1.5 font-medium">Outcome</th>
              <th className="px-2 py-1.5 font-medium">R</th>
              <th className="px-2 py-1.5 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {state.rows.map((row, index) => {
              const entryTime = firstPresent(row.entry_time, row.signal_time);
              const entry = firstPresent(row.entry, row.entry_price);
              const stop = firstPresent(row.stop, row.stop_price);
              const target = firstPresent(row.target, row.target_price, row.tp1);
              const rValue = firstPresent(row.r, row.r_multiple);
              return (
                <tr
                  key={`${row.combination_id ?? "trade"}-${row.symbol ?? ""}-${index}`}
                  className="border-t border-terminal-border"
                >
                  <td className="px-2 py-1.5 font-mono">{blankText(row.symbol)}</td>
                  <td className="px-2 py-1.5 font-mono">{blankText(row.combination_id)}</td>
                  <td className="px-2 py-1.5 font-mono">{blankText(row.direction)}</td>
                  <td className="px-2 py-1.5 font-mono">{blankText(entryTime)}</td>
                  <td className="px-2 py-1.5 font-mono" data-testid="discovery-trade-entry">
                    {blankText(entry)}
                  </td>
                  <td className="px-2 py-1.5 font-mono" data-testid="discovery-trade-stop">
                    {blankText(stop)}
                  </td>
                  <td className="px-2 py-1.5 font-mono" data-testid="discovery-trade-target">
                    {blankText(target)}
                  </td>
                  <td className="px-2 py-1.5 font-mono" data-testid="discovery-trade-outcome">
                    {blankText(row.outcome)}
                  </td>
                  <td className="px-2 py-1.5 font-mono" data-testid="discovery-trade-r">
                    {blankText(rValue)}
                  </td>
                  <td className="px-2 py-1.5">
                    <div>EXPERIMENTAL</div>
                    <div className="text-terminal-muted">not approved</div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RankingTable({ payload }: { payload: DiscoveryRankingResponse }) {
  const rows = payload.rows || [];
  const showSharpe = rows.some((row) => sentNumber(row.sharpe));
  const showSortino = rows.some((row) => sentNumber(row.sortino));
  const showProfit = rows.some((row) => sentNumber(row.profit));

  return (
    <>
      {payload.disclaimer ? (
        <p className="mt-4 text-sm text-terminal-muted" data-testid="discovery-ranking-disclaimer">
          {payload.disclaimer}
        </p>
      ) : null}
      <div className="mt-4 overflow-x-auto rounded border border-terminal-border">
        <table className="min-w-full text-left text-xs">
          <thead className="text-terminal-muted">
            <tr>
              <th className="px-2 py-1.5 font-medium">Rank</th>
              <th className="px-2 py-1.5 font-medium">Symbol</th>
              <th className="px-2 py-1.5 font-medium">Combination</th>
              <th className="px-2 py-1.5 font-medium">Direction</th>
              <th className="px-2 py-1.5 font-medium">Sample size</th>
              <th className="px-2 py-1.5 font-medium">Walk-forward windows</th>
              <th className="px-2 py-1.5 font-medium">Out-of-sample profit factors</th>
              <th className="px-2 py-1.5 font-medium">Status</th>
              {showSharpe ? <th className="px-2 py-1.5 font-medium">Sharpe</th> : null}
              {showSortino ? <th className="px-2 py-1.5 font-medium">Sortino</th> : null}
              {showProfit ? <th className="px-2 py-1.5 font-medium">Profit</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.combination_id ?? "row"}-${row.symbol ?? ""}-${index}`} className="border-t border-terminal-border">
                <td className="px-2 py-1.5 font-mono">{metricText(row.rank)}</td>
                <td className="px-2 py-1.5 font-mono">{row.symbol || "—"}</td>
                <td className="px-2 py-1.5 font-mono">{row.combination_id || "—"}</td>
                <td className="px-2 py-1.5 font-mono">{row.direction || "—"}</td>
                <td className="px-2 py-1.5 font-mono">{metricText(row.sample_size)}</td>
                <td className="px-2 py-1.5 font-mono">{metricText(row.walk_forward_windows)}</td>
                <td className="px-2 py-1.5 font-mono">{factorText(row)}</td>
                <td className="px-2 py-1.5">
                  <div>EXPERIMENTAL</div>
                  <div className="text-terminal-muted">not approved</div>
                  {researchPassedText(row) ? (
                    <div data-testid="discovery-research-passed">{researchPassedText(row)}</div>
                  ) : null}
                </td>
                {showSharpe ? <td className="px-2 py-1.5 font-mono">{metricText(row.sharpe)}</td> : null}
                {showSortino ? <td className="px-2 py-1.5 font-mono">{metricText(row.sortino)}</td> : null}
                {showProfit ? <td className="px-2 py-1.5 font-mono">{metricText(row.profit)}</td> : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
