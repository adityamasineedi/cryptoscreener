import { useEffect, useMemo, useState } from "react";
import {
  fetchCapBookAgents,
  type CapBookAgentsResponse,
  type CapBookRow,
} from "../api/client";
import { PROFIT_CHECKER_SEALED_LABEL, SEALED_MESSAGE } from "../backtest/seal";
import { isBlacklistedStrategySide } from "../backtest/strategyBlacklist";
import { moneyTone } from "../utils/moneyTone";
import {
  RESEARCH_RESULTS_PAGE_PATH,
  SCORECARD_SNAPSHOT_DATE,
  SCORECARD_SNAPSHOT_SOURCE,
  hasBacktestLookaheadD3,
  researchVerdictFor,
} from "../research/scorecardSnapshot";

/** User-facing text for the internal "PASS" verdict (owner D201). */
export const PASS_LABEL = "net > $0 (window)";

type LiveRow = CapBookRow & { shard: string; agentName: string };

function compactSymbol(symbol: string): string {
  return symbol.replace(/USDT$/, "");
}

function netMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  // Zero is neither gain nor loss (owner D206): "$0.00", no sign.
  if (Math.round(value * 100) === 0) return "$0.00";
  return `${value > 0 ? "+" : ""}$${value.toFixed(2)}`;
}

/**
 * Sticky table header (owner D206): a solid background above the rows, so no row shows
 * through or sits under it while the table scrolls.
 */
const STICKY_HEAD = "sticky top-0 z-20 bg-terminal-bg text-[10px] uppercase tracking-wide text-terminal-muted shadow-[0_1px_0_0_rgba(255,255,255,0.12)]";
const STICKY_TH = "bg-terminal-bg px-2 py-2";
/**
 * A column kept in view at the table's right edge (owner D206: least invasive way to see
 * Combined net / Best cell without scrolling sideways; the column order is unchanged).
 */
const PIN_RIGHT_TH = "sticky right-0 z-30 bg-terminal-bg px-2 py-2 shadow-[-1px_0_0_0_rgba(255,255,255,0.12)]";
const PIN_RIGHT_TD = "sticky right-0 z-10 bg-terminal-bg shadow-[-1px_0_0_0_rgba(255,255,255,0.12)]";

function seconds(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "0.0s";
  return `${value.toFixed(1)}s`;
}

function passedCell(row: { pnl_usd_net: number | null; sample_size: number }): boolean {
  return Number(row.pnl_usd_net) > 0 && Number(row.sample_size) >= 5;
}

type StrategyCheck = {
  combinationId: string;
  timeframe: string;
  direction: string;
  trades: number;
  net: number;
  coinsUp: number;
  coinsDown: number;
  verdict: "PASS" | "FAIL";
  /** Per-coin trades and net, used only to spot identical results. */
  signature: string;
  /** "<strategy> <tf> <side>" of the first row with identical results, if any. */
  duplicateOf: string | null;
};

type CoinVerdict = "PASS" | "LOSS" | "FAILED";

type CoinCheck = {
  symbol: string;
  cap: string;
  trades: number;
  net1h: number;
  net4h: number;
  net: number;
  passCells: number;
  best: string;
  verdict: CoinVerdict;
};

function coinVerdict(net: number, trades: number): CoinVerdict {
  if (net < 0) return "LOSS";
  if (net > 0 && trades >= 5) return "PASS";
  return "FAILED";
}

function strategyLabel(row: Pick<StrategyCheck, "combinationId" | "timeframe" | "direction">): string {
  return `${row.combinationId} ${row.timeframe} ${row.direction}`;
}

type StrategyAccumulator = Omit<StrategyCheck, "signature" | "duplicateOf"> & { cells: string[] };

function strategyChecks(rows: LiveRow[]): StrategyCheck[] {
  const grouped = new Map<string, StrategyAccumulator>();
  for (const row of rows) {
    const key = `${row.combination_id}|${row.timeframe}|${row.direction}`;
    const current: StrategyAccumulator = grouped.get(key) ?? {
      combinationId: row.combination_id,
      timeframe: row.timeframe,
      direction: row.direction,
      trades: 0,
      net: 0,
      coinsUp: 0,
      coinsDown: 0,
      verdict: "FAIL",
      cells: [],
    };
    const net = Number(row.pnl_usd_net) || 0;
    const trades = Number(row.sample_size) || 0;
    current.trades += trades;
    current.net += net;
    if (trades > 0 && net > 0) current.coinsUp += 1;
    if (trades > 0 && net < 0) current.coinsDown += 1;
    if (trades > 0) current.cells.push(`${compactSymbol(row.symbol)}:${trades}:${net.toFixed(6)}`);
    grouped.set(key, current);
  }
  const sorted: StrategyCheck[] = [...grouped.values()]
    .map(({ cells, ...row }) => ({
      ...row,
      verdict: row.net > 0 && row.trades >= 5 ? "PASS" as const : "FAIL" as const,
      signature: cells.sort().join("|"),
      duplicateOf: null,
    }))
    .sort((a, b) => Number(b.verdict === "PASS") - Number(a.verdict === "PASS") || b.net - a.net);
  // Identical per-coin trades and net: keep the first row as shown and mark the later ones.
  // Rows with no trades all look identical, so they are not marked.
  const firstBySignature = new Map<string, string>();
  for (const row of sorted) {
    if (!row.trades) continue;
    const first = firstBySignature.get(row.signature);
    if (first) row.duplicateOf = first;
    else firstBySignature.set(row.signature, strategyLabel(row));
  }
  return sorted;
}

function coinChecks(rows: LiveRow[]): CoinCheck[] {
  const grouped = new Map<string, CoinCheck & { bestNet: number }>();
  for (const row of rows) {
    const symbol = compactSymbol(row.symbol);
    const current = grouped.get(symbol) ?? {
      symbol,
      cap: row.cap_group,
      trades: 0,
      net1h: 0,
      net4h: 0,
      net: 0,
      passCells: 0,
      best: "—",
      bestNet: Number.NEGATIVE_INFINITY,
      verdict: "FAILED",
    };
    const net = Number(row.pnl_usd_net) || 0;
    const trades = Number(row.sample_size) || 0;
    current.trades += trades;
    current.net += net;
    if (row.timeframe === "1h") current.net1h += net;
    if (row.timeframe === "4h") current.net4h += net;
    if (passedCell(row)) current.passCells += 1;
    if (trades > 0 && net > current.bestNet) {
      current.bestNet = net;
      current.best = `${row.combination_id} ${row.timeframe} ${row.direction} ${netMoney(net)}`;
    }
    grouped.set(symbol, current);
  }
  return [...grouped.values()]
    .map(({ bestNet: _bestNet, ...row }) => ({
      ...row,
      verdict: coinVerdict(row.net, row.trades),
    }))
    .sort((a, b) => b.net - a.net);
}

export function CapBookAgentsPanel() {
  const [snapshot, setSnapshot] = useState<CapBookAgentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shardFilter, setShardFilter] = useState("all");
  const [coinFilter, setCoinFilter] = useState("all");
  const [timeframeFilter, setTimeframeFilter] = useState("all");
  const [sideFilter, setSideFilter] = useState("all");
  const [strategyFilter, setStrategyFilter] = useState("all");
  const [passedOnly, setPassedOnly] = useState(true);
  const [showFailedSides, setShowFailedSides] = useState(false);
  const [showCells, setShowCells] = useState(false);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;

    const poll = async () => {
      if (controller) return;
      controller = new AbortController();
      try {
        const next = await fetchCapBookAgents(controller.signal);
        if (!active) return;
        setSnapshot(next);
        setError(null);
        const finished = next.shards.length > 0 && next.shards.every((shard) => shard.status.toUpperCase() === "OK");
        if (finished) window.clearInterval(interval);
      } catch (err) {
        if (!active || (err instanceof DOMException && err.name === "AbortError")) return;
        setError(err instanceof Error ? err.message : "Cap book monitor unavailable");
      } finally {
        controller = null;
      }
    };

    void poll();
    const interval = window.setInterval(() => void poll(), 3000);
    return () => {
      active = false;
      window.clearInterval(interval);
      controller?.abort();
    };
  }, []);

  const rows = useMemo<LiveRow[]>(
    () =>
      (snapshot?.shards || []).flatMap((shard) =>
        shard.rows
          .filter((row) => !isBlacklistedStrategySide(row.combination_id, row.timeframe, row.direction))
          .map((row) => ({
          ...row,
          shard: shard.shard,
          agentName: shard.agent_name,
        })),
      ),
    [snapshot],
  );
  const coins = useMemo(
    () => Array.from(new Set(rows.map((row) => compactSymbol(row.symbol)))).sort(),
    [rows],
  );
  const strategiesInBook = useMemo(
    () => Array.from(new Set(rows.map((row) => row.combination_id))).sort(),
    [rows],
  );
  const filteredRows = rows.filter(
    (row) =>
      (shardFilter === "all" || row.shard === shardFilter) &&
      (coinFilter === "all" || compactSymbol(row.symbol) === coinFilter) &&
      (timeframeFilter === "all" || row.timeframe === timeframeFilter) &&
      (sideFilter === "all" || row.direction === sideFilter) &&
      (strategyFilter === "all" || row.combination_id === strategyFilter) &&
      (!passedOnly || passedCell(row)),
  );
  const scopedRows = useMemo(
    () => rows.filter((row) => strategyFilter === "all" || row.combination_id === strategyFilter),
    [rows, strategyFilter],
  );
  const strategies = useMemo(() => strategyChecks(scopedRows), [scopedRows]);
  const coinTotals = useMemo(() => coinChecks(scopedRows), [scopedRows]);
  const passedStrategies = strategies.filter((row) => row.verdict === "PASS");
  const visibleStrategies = showFailedSides ? strategies : passedStrategies;
  const visibleCells = filteredRows.slice(0, 80);
  const bookFinished = (snapshot?.shards.length ?? 0) > 0 && (snapshot?.shards ?? []).every((shard) => shard.status.toUpperCase() === "OK");
  const passedCoins = coinTotals.filter((row) => row.verdict === "PASS");
  const lossCoins = coinTotals.filter((row) => row.verdict === "LOSS").sort((a, b) => a.net - b.net);
  const failedCoins = coinTotals.filter((row) => row.verdict === "FAILED");
  const duplicateCount = strategies.filter((row) => row.duplicateOf).length;

  return (
    <section
      className="mb-4 rounded border border-terminal-accent/30 bg-terminal-accent/[0.025] p-3"
      data-testid="cap-book-agents"
    >
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-terminal-text">Cap book agents</h2>
          <p className="mt-1 text-[11px] text-terminal-muted">
            {bookFinished ? "Finished book. The cell list stays closed so this page stays light." : "Read-only research files. Refreshes every 3 seconds until every shard is finished."}
          </p>
        </div>
        <div className="text-right font-mono text-[10px] text-terminal-muted" aria-live="polite">
          {error ? <span className="text-red-400">{error} · showing last snapshot</span> : null}
          {!error && snapshot?.timestamp ? `Updated ${new Date(snapshot.timestamp).toLocaleTimeString()}` : null}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {(snapshot?.shards || []).map((shard) => {
          const status = shard.status.toUpperCase();
          return (
            <div key={shard.shard} className="rounded border border-terminal-border/80 px-2 py-1 font-mono text-[11px] text-terminal-text">
              {shard.agent_name}
              <span className="text-terminal-muted"> · {status} · </span>
              <span data-testid={`shard-${shard.shard}-cells`}>{shard.cells_finished}</span>
              <span className="text-terminal-muted"> cells</span>
            </div>
          );
        })}
        {!snapshot ? <div className="py-2 text-xs text-terminal-muted">Loading agent snapshots…</div> : null}
      </div>

      <div className="mt-4" data-testid="profit-checker">
        <h3 className="text-sm font-semibold text-terminal-text">Profit checker</h3>
        <div
          role="note"
          data-testid="profit-checker-sealed"
          className="mt-2 rounded border border-red-500/50 bg-red-500/10 px-2 py-1.5 text-[11px] font-semibold text-red-200"
        >
          {snapshot?.sealed_label ?? PROFIT_CHECKER_SEALED_LABEL}
        </div>
        <button
          type="button"
          disabled
          data-testid="profit-checker-rerun"
          title={`${snapshot?.rerun_refused_reason ?? SEALED_MESSAGE}. ${snapshot?.sealed_label ?? PROFIT_CHECKER_SEALED_LABEL}`}
          className="mt-2 cursor-not-allowed rounded border border-terminal-border px-2 py-1 text-[11px] text-terminal-muted opacity-50"
        >
          Re-run profit checker (disabled: {snapshot?.rerun_refused_reason ?? SEALED_MESSAGE})
        </button>
        <div
          role="note"
          data-testid="profit-checker-banner"
          className="mt-2 rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1.5 text-[11px] font-semibold text-amber-200"
        >
          Short-window in-sample screen. Not evidence. See the research results page.{" "}
          <span className="font-normal text-amber-100/80">
            (<code className="font-mono">{RESEARCH_RESULTS_PAGE_PATH}</code> in the research worktree)
          </span>
        </div>
        <p className="mt-1 text-[11px] text-terminal-muted">
          Whole window, one $1,000 account per coin. 1h is 20 Sep–10 Oct 2026. 4h is 31 Aug–10 Oct 2026.
          A strategy side is labelled “{PASS_LABEL}” when its combined net is above $0 and it has at least 5 trades.
          Loss coins finished the window below $0. Failed coins took fewer than 5 trades, so they are not a measured loss.
          Worst measured sides are blacklisted and stay hidden.
          {strategies.length ? ` ${passedStrategies.length} of ${strategies.length} strategy sides ${PASS_LABEL}. ${lossCoins.length} loss coins, ${failedCoins.length} failed coins.` : ""}
          {duplicateCount ? ` ${duplicateCount} strategy sides repeat an earlier row's per-coin results and are marked duplicate.` : ""}
        </p>
        <button
          type="button"
          className="mt-2 rounded border border-terminal-border px-2 py-1 text-[11px] text-terminal-muted"
          onClick={() => setShowFailedSides((open) => !open)}
        >
          {showFailedSides ? "Hide failed strategy sides" : `Show failed strategy sides (${strategies.length - passedStrategies.length})`}
        </button>
        <div className="mt-2 max-h-72 overflow-auto rounded border border-terminal-border/60">
          <table className="w-full min-w-[1100px] border-collapse text-left text-[11px]">
            <thead className={STICKY_HEAD} data-testid="profit-checker-head">
              <tr>
                <th className={STICKY_TH}>Check</th>
                <th className={STICKY_TH}>Strategy</th>
                <th className={STICKY_TH}>TF</th>
                <th className={STICKY_TH}>Side</th>
                <th className={STICKY_TH}>Trades</th>
                <th className={STICKY_TH}>Coins up</th>
                <th className={STICKY_TH}>Coins down</th>
                <th className={PIN_RIGHT_TH} data-testid="pinned-combined-net-head">Combined net</th>
                <th className={STICKY_TH}>Research verdict</th>
                <th className={STICKY_TH}>Notes</th>
              </tr>
            </thead>
            <tbody data-testid="profit-checker-rows">
              {visibleStrategies.map((row) => (
                <tr key={`${row.combinationId}-${row.timeframe}-${row.direction}`} className="border-t border-terminal-border/40">
                  <td className="px-2 py-1.5"><Verdict verdict={row.verdict} /></td>
                  <td className="px-2 py-1.5 font-mono">{row.combinationId}</td>
                  <td className="px-2 py-1.5 font-mono">{row.timeframe}</td>
                  <td className="px-2 py-1.5 font-mono">{row.direction}</td>
                  <td className="px-2 py-1.5 font-mono">{row.trades}</td>
                  <td className="px-2 py-1.5 font-mono">{row.coinsUp}</td>
                  <td className="px-2 py-1.5 font-mono">{row.coinsDown}</td>
                  <td className={`${PIN_RIGHT_TD} px-2 py-1.5 font-mono ${moneyTone(row.net)}`} data-testid="combined-net">{netMoney(row.net)}</td>
                  <td className="px-2 py-1.5" data-testid="research-verdict"><ResearchVerdictCell combinationId={row.combinationId} direction={row.direction} /></td>
                  <td className="px-2 py-1.5" data-testid="strategy-notes"><StrategyNotes row={row} /></td>
                </tr>
              ))}
              {!strategies.length ? <tr><td colSpan={10} className="px-3 py-4 text-center text-terminal-muted">Waiting for completed cells.</td></tr> : null}
            </tbody>
          </table>
        </div>
        <p className="mt-1 text-[10px] text-terminal-muted" data-testid="research-snapshot-note">
          Research verdicts: static snapshot of research/SCORECARD.csv, with final outcomes from KILLED.md and DECISIONS.md,
          dated {SCORECARD_SNAPSHOT_DATE}, source {SCORECARD_SNAPSHOT_SOURCE}. It does not update by itself.
        </p>
        <div
          role="note"
          data-testid="coin-checker-banner"
          className="mt-3 rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1.5 text-[11px] font-semibold text-amber-200"
        >
          One 3-week window; coin results here are mostly the coin&apos;s own price move, not evidence. Do not select coins from this table.
        </div>
        <div className="mt-2 max-h-72 overflow-auto rounded border border-terminal-border/60">
          <table className="w-full min-w-[860px] border-collapse text-left text-[11px]">
            <thead className={STICKY_HEAD} data-testid="coin-checker-head">
              <tr>
                <th className={STICKY_TH}>Check</th>
                <th className={STICKY_TH}>Coin</th>
                <th className={STICKY_TH}>Cap</th>
                <th className={STICKY_TH}>1h net</th>
                <th className={STICKY_TH}>4h net</th>
                <th className={STICKY_TH}>Window net</th>
                <th className={STICKY_TH}>Trades</th>
                <th className={STICKY_TH}>Cells {PASS_LABEL}</th>
                <th className={PIN_RIGHT_TH} data-testid="pinned-best-cell-head">Best cell</th>
              </tr>
            </thead>
            <tbody data-testid="coin-checker-rows">
              <CoinGroup title={`Coins ${PASS_LABEL}`} rows={passedCoins} />
              <CoinGroup title="Loss coins" rows={lossCoins} />
              <CoinGroup title="Failed coins" rows={failedCoins} />
            </tbody>
          </table>
        </div>
      </div>

      <button
        type="button"
        className="mt-4 rounded border border-terminal-border px-2 py-1 text-[11px] text-terminal-muted"
        onClick={() => setShowCells((open) => !open)}
      >
        {showCells ? "Hide cell table" : `Show cell table (${filteredRows.length})`}
      </button>
      {showCells ? (
      <>
      <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
        <Filter label="Strategy" value={strategyFilter} onChange={setStrategyFilter} options={["all", ...strategiesInBook]} />
        <Filter label="Shard" value={shardFilter} onChange={setShardFilter} options={["all", "a", "b", "c", "d"]} />
        <Filter label="Coin" value={coinFilter} onChange={setCoinFilter} options={["all", ...coins]} />
        <Filter label="Timeframe" value={timeframeFilter} onChange={setTimeframeFilter} options={["all", "1h", "4h"]} />
        <Filter label="Side" value={sideFilter} onChange={setSideFilter} options={["all", "LONG", "SHORT"]} />
        <label className="flex items-center gap-1.5 text-terminal-muted">
          <input type="checkbox" checked={passedOnly} onChange={(event) => setPassedOnly(event.target.checked)} />
          Passed cells only
        </label>
      </div>

      <div className="mt-2 max-h-[520px] overflow-auto rounded border border-terminal-border/60">
        <table className="w-full min-w-[1050px] border-collapse text-left text-[11px]">
          <thead className={STICKY_HEAD}>
            <tr>
              <th className="px-2 py-2">Shard</th><th className="px-2 py-2">Strategy</th>
              <th className="px-2 py-2">Coin</th><th className="px-2 py-2">TF</th>
              <th className="px-2 py-2">Side</th><th className="px-2 py-2">Cap</th>
              <th className="px-2 py-2">Risk</th><th className="px-2 py-2">Trades</th>
              <th className="px-2 py-2">Avg R</th><th className="px-2 py-2">Avg R net</th>
              <th className="px-2 py-2">Net $</th><th className="px-2 py-2">Max DD R</th>
              <th className="px-2 py-2">Cell</th>
            </tr>
          </thead>
          <tbody data-testid="cap-book-rows">
            {visibleCells.map((row, index) => {
              const highlighted = Number(row.pnl_usd_net) > 0 && Number(row.sample_size) >= 5;
              return (
                <tr key={`${row.shard}-${index}-${row.combination_id}-${row.symbol}-${row.timeframe}-${row.direction}`} className={`border-t border-terminal-border/40 ${highlighted ? "bg-emerald-500/10 text-emerald-100" : "hover:bg-white/[0.03]"}`}>
                  <td className="px-2 py-1.5 font-mono">{row.shard.toUpperCase()}</td>
                  <td className="px-2 py-1.5 font-mono">{row.combination_id}</td>
                  <td className="px-2 py-1.5 font-mono">{compactSymbol(row.symbol)}</td>
                  <td className="px-2 py-1.5 font-mono">{row.timeframe}</td>
                  <td className="px-2 py-1.5 font-mono">{row.direction}</td>
                  <td className="px-2 py-1.5">{row.cap_group}</td>
                  <td className="px-2 py-1.5 font-mono">${Number(row.risk_usd).toFixed(2)}</td>
                  <td className="px-2 py-1.5 font-mono">{row.sample_size}</td>
                  <td className="px-2 py-1.5 font-mono">{row.average_R == null ? "—" : row.average_R.toFixed(3)}</td>
                  <td className="px-2 py-1.5 font-mono">{row.average_R_net == null ? "—" : row.average_R_net.toFixed(3)}</td>
                  <td className={`px-2 py-1.5 font-mono ${moneyTone(row.pnl_usd_net)}`}>{netMoney(row.pnl_usd_net)}</td>
                  <td className="px-2 py-1.5 font-mono">{row.max_drawdown_R == null ? "—" : row.max_drawdown_R.toFixed(2)}</td>
                  <td className="px-2 py-1.5 font-mono">{seconds(row.cell_seconds)}</td>
                </tr>
              );
            })}
            {!filteredRows.length ? <tr><td colSpan={13} className="px-3 py-6 text-center text-terminal-muted">No completed cells match these filters.</td></tr> : null}
            {filteredRows.length > visibleCells.length ? <tr><td colSpan={13} className="px-3 py-3 text-center text-terminal-muted">Showing {visibleCells.length} of {filteredRows.length}. Narrow the filters to see the rest.</td></tr> : null}
          </tbody>
        </table>
      </div>
      </>
      ) : null}
    </section>
  );
}

function CoinGroup({ title, rows }: { title: string; rows: CoinCheck[] }) {
  return (
    <>
      <tr>
        <td colSpan={9} className="bg-black/60 px-2 py-2 text-[10px] font-semibold uppercase tracking-wide text-terminal-text">
          {title} · {rows.length}
        </td>
      </tr>
      {rows.map((row) => (
        <tr key={row.symbol} className="border-t border-terminal-border/40">
          <td className="px-2 py-1.5"><Verdict verdict={row.verdict} /></td>
          <td className="px-2 py-1.5 font-mono">{row.symbol}</td>
          <td className="px-2 py-1.5">{row.cap}</td>
          <td className={`px-2 py-1.5 font-mono ${moneyTone(row.net1h)}`} data-testid="coin-net-1h">{netMoney(row.net1h)}</td>
          <td className={`px-2 py-1.5 font-mono ${moneyTone(row.net4h)}`} data-testid="coin-net-4h">{netMoney(row.net4h)}</td>
          <td className={`px-2 py-1.5 font-mono ${moneyTone(row.net)}`} data-testid="coin-net-window">{netMoney(row.net)}</td>
          <td className="px-2 py-1.5 font-mono">{row.trades}</td>
          <td className="px-2 py-1.5 font-mono">{row.passCells}</td>
          <td className={`${PIN_RIGHT_TD} px-2 py-1.5 font-mono`} data-testid="best-cell">{row.best}</td>
        </tr>
      ))}
      {!rows.length ? (
        <tr>
          <td colSpan={9} className="px-3 py-3 text-terminal-muted">None</td>
        </tr>
      ) : null}
    </>
  );
}

function Verdict({ verdict }: { verdict: "PASS" | "FAIL" | CoinVerdict }) {
  const tone = verdict === "PASS"
    ? "border-emerald-500/40 text-emerald-300"
    : verdict === "LOSS"
      ? "border-red-500/40 text-red-300"
      : verdict === "FAILED"
        ? "border-amber-500/40 text-amber-200"
        : "border-red-500/40 text-red-300";
  return (
    <span className={`whitespace-nowrap rounded border px-1.5 py-0.5 font-mono text-[10px] ${tone}`}>
      {verdict === "PASS" ? PASS_LABEL : verdict}
    </span>
  );
}

function ResearchVerdictCell({ combinationId, direction }: { combinationId: string; direction: string }) {
  const lookup = researchVerdictFor(combinationId, direction);
  if (!lookup) return <span className="text-terminal-muted">not in research</span>;
  const { entry, key, note } = lookup;
  const tone = entry.verdict === "KILLED"
    ? "border-red-500/40 text-red-300"
    : "border-amber-500/40 text-amber-200";
  return (
    <div className="max-w-[340px]">
      <span className={`whitespace-nowrap rounded border px-1.5 py-0.5 font-mono text-[10px] ${tone}`}>{entry.verdict}</span>
      <span className="ml-1 text-[10px] text-terminal-muted">{entry.stage}</span>
      <div className="mt-0.5 text-[10px] text-terminal-muted" title={`${key} · ${entry.source}`}>{entry.reason}</div>
      {note ? <div className="mt-0.5 text-[10px] text-amber-200/80">{note}</div> : null}
    </div>
  );
}

function StrategyNotes({ row }: { row: StrategyCheck }) {
  const lookahead = hasBacktestLookaheadD3(row.combinationId);
  if (!lookahead && !row.duplicateOf) return <span className="text-terminal-muted">—</span>;
  return (
    <div className="flex flex-col gap-0.5 text-[10px]">
      {lookahead ? <span className="text-red-300">lookahead in backtest (D3)</span> : null}
      {row.duplicateOf ? <span className="text-amber-200">duplicate of {row.duplicateOf}</span> : null}
    </div>
  );
}

function Filter({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[] }) {
  return (
    <label className="flex items-center gap-1.5 text-terminal-muted">
      {label}
      <select aria-label={`Filter by ${label.toLowerCase()}`} value={value} onChange={(event) => onChange(event.target.value)} className="rounded border border-terminal-border bg-black/40 px-2 py-1 font-mono text-terminal-text">
        {options.map((option) => <option key={option} value={option}>{option === "all" ? "All" : option.toUpperCase()}</option>)}
      </select>
    </label>
  );
}
