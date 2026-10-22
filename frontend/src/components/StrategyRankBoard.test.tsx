/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { StrategyRankResponse, StrategyRankRow } from "../api/client";
import { RESEARCH_RESULTS_PAGE_PATH } from "../research/scorecardSnapshot";
import { NO_PASS_BANNER } from "./ResearchScorecard";
import { OLD_RANKING_TITLE, StrategyRankBoard, StrategyRankDetail, withoutSmallRun } from "./StrategyRankBoard";

function row(over: Partial<StrategyRankRow>): StrategyRankRow {
  return {
    rank: 1,
    key: "day|X|LONG|1h",
    style: "day",
    family: "combo",
    strategy_id: "X",
    direction: "LONG",
    timeframe: "1h",
    in_catalog: false,
    catalog_id: null,
    name: "X",
    description_source: "rank_file",
    summary: "Not in the strategy catalog. Family: combo.",
    gates: [],
    variant: null,
    coins: 10,
    coins_passing: 0,
    coins_net_positive: 0,
    passing_symbols: [],
    trades: 10,
    net_pnl_usd: 5,
    fees_usd: -1,
    worst_cell_net_usd: -1,
    best_cell_net_usd: 2,
    execution_models: ["LEGACY_SIGNAL_CLOSE"],
    fill_label: "Signal close, no slippage",
    risk_usd: [20],
    leverage: [2],
    sizing_note: null,
    usd_comparable: true,
    aliases: [],
    alias_of: null,
    do_not_use: false,
    do_not_use_reason: null,
    cells: [],
    ...over,
  };
}

const CANDLE_SUMMARY =
  "Research only, frozen BTC 1h book (not production, not paper): small run, BTC 1h, DB-tail " +
  "1,200 bars (12d preset, about 50 days to 2026-10-16): 3 trades, net −$2.50.";

const RANK: StrategyRankResponse = {
  status: "OK",
  source: { path: "backend/scripts/strategy_rank/ranked.json" },
  window: {
    start: "2026-02-14",
    end: "2026-10-12",
    note: "1d candles end 2026-10-08",
    sample_note: "In-sample, one window, no out-of-sample test.",
    coins: ["BTC", "ETH"],
    market: "USDT perps",
    account: { principal_usd: 1000, risk_usd: 20, leverage: 2, exceptions: "Grid is 1x." },
    fees: { taker_pct: 0.04, maker_pct: 0.02 },
  },
  caveats: ["Fees in every cell: 0.04% taker, 0.02% maker."],
  groups: [
    {
      id: "day",
      label: "Day trading",
      timeframes: "1h",
      cells: 406,
      cells_net_positive: 88,
      cells_passing: 37,
      caveats: [],
      rows: [
        row({
          key: "day|CANDLE12_V2|LONG|1h",
          strategy_id: "CANDLE12_V2",
          in_catalog: true,
          catalog_id: "CANDLE12_V2",
          description_source: "catalog",
          summary: CANDLE_SUMMARY,
          net_pnl_usd: 525.68,
          trades: 194,
        }),
        row({ rank: 2, key: "day|COMBO_05|SHORT|1h", strategy_id: "COMBO_05", direction: "SHORT" }),
        row({ rank: 3, key: "day|CANDLE12_V2|SHORT|1h", strategy_id: "CANDLE12_V2", direction: "SHORT" }),
      ],
    },
    {
      id: "scalp",
      label: "Scalp",
      timeframes: "5m · 15m",
      cells: 340,
      cells_net_positive: 35,
      cells_passing: 3,
      caveats: [],
      rows: [row({ key: "scalp|INTRADAY_BOS_SD_V1.C|LONG|5m", strategy_id: "INTRADAY_BOS_SD_V1.C", variant: "C" })],
    },
  ],
};

function renderBoard(rank: StrategyRankResponse | null = RANK) {
  return render(
    <StrategyRankBoard rank={rank} loading={false} error={null} selectedKey={null} onSelect={() => {}} />
  );
}

afterEach(cleanup);

describe("Strategies page: research scorecard on top", () => {
  it("shows the banner, the results-page path and the scorecard before the old ranking", () => {
    renderBoard();
    const scorecard = screen.getByTestId("research-scorecard");
    const old = screen.getByTestId("old-ranking");
    expect(scorecard.compareDocumentPosition(old) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("no-pass-banner").textContent).toBe(
      "No strategy passed validation (FINAL_VERDICT)."
    );
    expect(NO_PASS_BANNER).toBe("No strategy passed validation (FINAL_VERDICT).");
    expect(screen.getByTestId("scorecard-pointer").textContent).toContain(RESEARCH_RESULTS_PAGE_PATH);
    expect(RESEARCH_RESULTS_PAGE_PATH).toBe("research/out/report/index.html");
    expect(screen.getByTestId("scorecard-snapshot").textContent).toContain("7e92b67");
  });

  it("lists the later families with their final verdicts", () => {
    renderBoard();
    const later = screen.getByTestId("scorecard-later");
    const expectations: Array<[string, string, string]> = [
      ["N1_BTC_RANGE_GRID|NON_DIRECTIONAL", "N1", "KILLED"],
      ["N2_BTC_FUNDING_CARRY|NON_DIRECTIONAL", "N2", "INCONCLUSIVE"],
      ["HD2_FUNDING_EXTREMES|LONG_SHORT", "HD-2", "INCONCLUSIVE"],
      ["HR1_SWEEP_CHOCH_30M|BOTH", "HR-1", "KILLED"],
      ["HD1_TAKER_FLOW|LONG_SHORT", "HD-1", "KILLED"],
      ["HCOMBO_T4_F1F3|SHORT", "H-COMBO", "KILLED"],
      ["MTF_4H_16_BREAKOUT252_F1X|LONG", "H-F1X", "KILLED"],
      ["XS_MOM|LONG_SHORT", "XS-MOM", "KILLED"],
    ];
    const rows = within(later).getAllByTestId(/^scorecard-row-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual(
      expectations.map(([k]) => `scorecard-row-${k}`)
    );
    for (const [key, family, verdict] of expectations) {
      const tr = within(later).getByTestId(`scorecard-row-${key}`);
      expect(tr.textContent).toContain(family);
      expect(tr.textContent).toContain(verdict);
    }
    // The tournament table carries the gate each side failed.
    const earlier = screen.getByTestId("scorecard-earlier");
    expect(within(earlier).getByTestId("scorecard-row-COMBO_01|LONG").textContent).toContain("Gate 2, E9");
    expect(within(earlier).getByTestId("scorecard-row-COMBO_02_V2|LONG").textContent).toContain("NOT RUN");
    expect(within(earlier).queryByTestId("scorecard-row-XS_MOM|LONG_SHORT")).toBeNull();
  });

  it("filters by verdict", () => {
    renderBoard();
    fireEvent.click(screen.getByTestId("scorecard-filter-INCONCLUSIVE"));
    const later = within(screen.getByTestId("scorecard-later")).getAllByTestId(/^scorecard-row-/);
    expect(later.map((r) => r.getAttribute("data-testid"))).toEqual([
      "scorecard-row-N2_BTC_FUNDING_CARRY|NON_DIRECTIONAL",
      "scorecard-row-HD2_FUNDING_EXTREMES|LONG_SHORT",
    ]);
    const earlier = within(screen.getByTestId("scorecard-earlier")).getAllByTestId(/^scorecard-row-/);
    expect(earlier.map((r) => r.getAttribute("data-testid"))).toEqual(["scorecard-row-MTF_4H_16_BREAKOUT252|LONG"]);
  });

  it("still shows the scorecard when the ranking file is missing", () => {
    renderBoard({ status: "MISSING", error: "ranked.json not found.", groups: [] });
    expect(screen.getByTestId("no-pass-banner")).toBeTruthy();
    expect(screen.getByTestId("strategy-rank-error").textContent).toContain("ranked.json not found");
  });
});

describe("Strategies page: old in-sample ranking", () => {
  it("is collapsed under the exact title", () => {
    renderBoard();
    const old = screen.getByTestId("old-ranking") as HTMLDetailsElement;
    expect(old.tagName).toBe("DETAILS");
    expect(old.open).toBe(false);
    expect(screen.getByTestId("old-ranking-summary").textContent).toBe(
      "Old in-sample ranking (window 2026-02-14 to 2026-10-12 overlaps the sealed holdout; not evidence)"
    );
    expect(OLD_RANKING_TITLE).toBe(screen.getByTestId("old-ranking-summary").textContent);
    expect(screen.queryByText(/Ranked by backtest/)).toBeNull();
  });

  it("shows a research verdict badge on every card", () => {
    renderBoard();
    const badgeOf = (key: string) =>
      within(screen.getByTestId(`rank-row-${key}`)).getByTestId("research-verdict-badge");
    expect(badgeOf("day|CANDLE12_V2|LONG|1h").textContent).toBe("Research: KILLED · DISCOVERY");
    expect(badgeOf("day|COMBO_05|SHORT|1h").textContent).toBe("Research: KILLED · DISCOVERY");
    expect(badgeOf("day|COMBO_05|SHORT|1h").getAttribute("title")).toContain("COMBO_05+PBFIX|SHORT");
    expect(badgeOf("day|CANDLE12_V2|SHORT|1h").textContent).toBe("Research: no verdict (not in scorecard)");
    fireEvent.click(screen.getByTestId("rank-tab-scalp"));
    expect(badgeOf("scalp|INTRADAY_BOS_SD_V1.C|LONG|5m").textContent).toBe("Research: NOT RUN · DISCOVERY");
    const cards = screen.getAllByTestId(/^rank-row-/);
    for (const card of cards) expect(within(card).getAllByTestId("research-verdict-badge")).toHaveLength(1);
  });

  it("states the fees the ranking used and the current default", () => {
    renderBoard();
    expect(screen.getByTestId("strategy-rank-fees").textContent).toBe(
      "this ranking used 0.04% taker; current default 0.05% (D144)"
    );
  });

  it("labels the tab count as cells net > 0 under the old rule", () => {
    renderBoard();
    expect(screen.getByTestId("rank-tab-day").textContent).toContain("88 of 406 cells net > 0 (old rule)");
    expect(screen.getByTestId("rank-tab-scalp").textContent).toContain("35 of 340 cells net > 0 (old rule)");
    expect(screen.getByTestId("strategy-rank-board").textContent).not.toMatch(/cells pass\b/);
  });

  it("shows only ranking numbers on a card and leaves the small run off", () => {
    renderBoard();
    const card = screen.getByTestId("rank-row-day|CANDLE12_V2|LONG|1h");
    expect(card.textContent).toContain("+525.68");
    expect(card.textContent).toContain("Research only, frozen BTC 1h book (not production, not paper).");
    expect(card.textContent).not.toContain("−$2.50");
    expect(card.textContent).not.toContain("small run,");
    expect(within(card).getByTestId("small-run-dropped").textContent).toContain("not part of");
    expect(within(screen.getByTestId("rank-row-day|COMBO_05|SHORT|1h")).queryByTestId("small-run-dropped")).toBeNull();
  });

  it("drops only the small-run tail of a summary", () => {
    expect(withoutSmallRun("Research only: small run, BTC 1h: LONG 13 trades, net +$1.09.")).toEqual({
      text: "Research only.",
      dropped: true,
    });
    expect(withoutSmallRun("Primary working long playbook.")).toEqual({
      text: "Primary working long playbook.",
      dropped: false,
    });
  });

  it("gives the detail pane the same badge", () => {
    render(<StrategyRankDetail row={RANK.groups[0].rows[0]} window={RANK.window} />);
    const detail = screen.getByTestId("strategy-rank-detail");
    expect(detail.textContent).toContain("Old in-sample rank 1");
    expect(within(detail).getByTestId("research-verdict-badge").textContent).toBe("Research: KILLED · DISCOVERY");
  });
});
