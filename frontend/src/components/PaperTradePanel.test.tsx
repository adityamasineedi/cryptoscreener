/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  disablePaperTrade,
  fetchDashboardReview,
  fetchPaperLedgerView,
  fetchPaperOpportunities,
  fetchPaperPositions,
  resetPaperTrade,
  type DashboardReview,
  type PaperLedgerRow,
  type PaperLedgerView,
  type PaperPosition,
  type PaperStatus,
} from "../api/client";
import { PaperTradePanel } from "./PaperTradePanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchPaperPositions: vi.fn(),
    fetchPaperOpportunities: vi.fn(),
    fetchDashboardReview: vi.fn(async () => REVIEW),
    fetchPaperLedgerView: vi.fn(async () => EMPTY_LEDGER),
    resetPaperTrade: vi.fn(),
    disablePaperTrade: vi.fn(),
    // Mounted PassedRegimePanel: never resolves, so it stays in its loading state.
    fetchPassedRegime: vi.fn(() => new Promise(() => {})),
    // Mounted Book16PaperPanel: same, never resolves.
    fetchBook16Paper: vi.fn(() => new Promise(() => {})),
  };
});

const REVIEW: DashboardReview = {
  read_only: true,
  as_of: "2026-10-21T06:00:00+00:00",
  armed_books: ["V1", "PASSED"],
  watchers: [
    {
      id: "V1",
      can_open: true,
      coins: [{ symbol: "BTCUSDT", risk_percent: 0.02, timeframe: "1h" }],
      risk: "2% of equity on each 1h book",
    },
    {
      id: "PASSED",
      can_open: true,
      coins: [{ symbol: "ATOMUSDT", group: "mid", risk_percent: 0.01 }],
      risk: "2% large-cap list, 1% mid-cap list",
    },
  ],
  telegram_text: "Telegram sends PAPER_ENTRY / PAPER_EXIT for COMBO_02 v1 (1 1h books).",
  forward_tests: [{ id: "V1", running: true, why: "PAPER_V1_WATCHER_ENABLED" }],
  reset_blocked: true,
  stops: {
    counter_start: { first_run_utc: "2026-10-20T17:57:21+00:00", ruling: "owner D27/D34" },
    limits: { consecutive_loss_halt: 3, peak_drawdown_halt_pct: 0.1, strategy_drawdown_halt_r: 6 },
    books: {},
    journal_tail: [],
    engine_loaded: false,
  },
  missed: {
    counter: 160,
    unevaluated_bars: 34,
    retained_rows: 2,
    unretained: 159,
    by_reason: { sleep: 0, crash: 0, "late bar": 1, ohlcv_catchup: 1, other: 0 },
    by_book: { PASSED_BOOK: 2 },
    by_day: { "2026-10-19": 1 },
    after_restart_utc: "2026-10-21T03:48:00+00:00",
    after_restart: [],
    after_restart_unknown: [],
    note: "missed_signals counts late_tip only.",
  },
  runners: [
    {
      runner: "passed book",
      last_heartbeat: "2026-10-21T06:01:24+00:00",
      age_seconds: 30,
      status: "FRESH",
      detail: "watermark",
    },
  ],
  candle12_v2: { present: true, equity: 1000, realized_pnl: 0, net_r: 0, trades: 0, open_risk: 0 },
};

const LEDGER_BOOKS: PaperLedgerView["books"] = [
  {
    id: "PASSED",
    label: "PARITY TEST",
    forward_start: "2026-10-21T00:00:00+00:00",
    forward_start_cite: "owner D187",
    equity: 1020.7,
    trades: 1,
    pnl_usd: 20.7,
    r_actual: 2.26,
    r_nominal: 2.26,
    r_ledger: 2.26,
    capped: 0,
    nominal_fallback: 0,
  },
  {
    id: "V1",
    label: "PAPER",
    forward_start: "2026-10-21T00:00:00+00:00",
    forward_start_cite: "owner D187",
    equity: 964.73,
    trades: 2,
    pnl_usd: -35.27,
    r_actual: -2.23,
    r_nominal: -1.93,
    r_ledger: -2.23,
    capped: 1,
    nominal_fallback: 0,
  },
  {
    id: "LEGACY",
    label: "LEGACY (disabled)",
    forward_start: null,
    forward_start_cite: "disabled stream",
    equity: 1000,
    trades: 0,
    pnl_usd: 0,
    r_actual: 0,
    r_nominal: 0,
    r_ledger: 0,
    capped: 0,
    nominal_fallback: 0,
  },
];

const EMPTY_LEDGER: PaperLedgerView = {
  read_only: true,
  available: true,
  as_of: "2026-10-22T06:00:00+00:00",
  books: LEDGER_BOOKS,
  rows: [],
  total_rows: 0,
  default_rows: 0,
};

function ledgerRow(
  id: string,
  symbol: string,
  tag: PaperLedgerRow["evidence_tag"],
  extra: Partial<PaperLedgerRow> = {},
): PaperLedgerRow {
  return {
    id,
    symbol,
    side: "SHORT",
    status: "CLOSED",
    entry_price: 1,
    stop_price: 1.02,
    tp1_price: 0.96,
    quantity: 500,
    risk_usd: 10,
    opened_at: "2026-10-21T19:00:47+00:00",
    closed_at: "2026-10-22T03:00:00+00:00",
    exit_price: 0.98,
    exit_reason: "TP1",
    pnl_usd: 20.7,
    r_multiple: 2.07,
    mark_price: null,
    unrealized_pnl_usd: null,
    unrealized_r: null,
    source_candle_ts: "2026-10-21T18:00:00+00:00",
    timeframe: "1h",
    signal_snippet: { source: "PASSED_CAP_BOOK", stream: "PASSED|COMBO_02_V2|SHORT" },
    book: "PASSED",
    evidence_tag: tag,
    evidence_reason: tag === "VALID" ? "" : `rule ${tag}`,
    evidence_matches: tag && tag !== "VALID" ? [tag] : [],
    in_default_view: tag === "VALID",
    capped_by_leverage: false,
    equity_at_open: 1000,
    nominal_risk_usd: 10,
    r_nominal: 2.07,
    r_actual: 2.07,
    ...extra,
  };
}

const STATUS: PaperStatus = {
  enabled: true,
  paper_only: true,
  disclaimer: "Paper only — not real orders",
  starting_equity: 1000,
  equity: 1000,
  realized_pnl_usd: 0,
  open_count: 5,
  closed_count: 0,
  open_risk_usd: 80,
  risk_percent: 0.02,
  timestamp: "2026-10-12T10:05:00+00:00",
};

function position(
  id: string,
  symbol: string,
  side: string,
  snippet: Record<string, unknown>,
  extra: Partial<PaperPosition> = {},
): PaperPosition {
  return {
    id,
    symbol,
    side,
    status: "OPEN",
    entry_price: 100,
    stop_price: side === "SHORT" ? 102 : 98,
    tp1_price: side === "SHORT" ? 96 : 104,
    quantity: 5,
    risk_usd: 10,
    opened_at: "2026-10-12T10:00:05+00:00",
    closed_at: null,
    exit_price: null,
    exit_reason: null,
    pnl_usd: null,
    r_multiple: null,
    mark_price: null,
    unrealized_pnl_usd: null,
    unrealized_r: null,
    source_candle_ts: "2026-10-12T10:00:00+00:00",
    timeframe: "1h",
    signal_snippet: snippet,
    ...extra,
  };
}

const OPEN_ROWS: PaperPosition[] = [
  position(
    "v1-long",
    "BTCUSDT",
    "LONG",
    {
      source: "V1_PAPER_WATCHER",
      strategy_id: "COMBO_02_V1",
      path: "A",
      combo_version: "v1-combo02-long-htf",
      price_source: "mark_price",
    },
    { mark_price: 101.5, unrealized_pnl_usd: 7.5, unrealized_r: 0.75 },
  ),
  position("cap-short", "NEARUSDT", "SHORT", {
    source: "PASSED_CAP_BOOK",
    stream: "PASSED|COMBO_02_V2|SHORT",
    strategy_id: "COMBO_02_V2",
    price_source: "plan",
  }),
  position("cap-long", "ETHUSDT", "LONG", {
    source: "PASSED_CAP_BOOK",
    stream: "PASSED|COMBO_03_TRANSITION_B|LONG",
    strategy_id: "COMBO_03_TRANSITION_B",
    price_source: "plan",
  }),
  position("cap-no-stream", "SOLUSDT", "SHORT", {
    source: "PASSED_CAP_BOOK",
    strategy_id: "COMBO_01",
    price_source: "plan",
  }),
  position("old-row", "ADAUSDT", "LONG", {
    source: "LEGACY_SETUP_SIGNAL",
    strategy_id: "RESEARCH_15M",
    path: "PATH_A",
  }),
];

function cellText(row: HTMLElement, testId: string): string {
  return within(row).getByTestId(testId).textContent ?? "";
}

async function renderOpenTable() {
  render(<PaperTradePanel />);
  const firstStreamCell = (await screen.findAllByTestId("paper-stream"))[0];
  const table = firstStreamCell.closest("table") as HTMLTableElement;
  const headers = Array.from(table.querySelectorAll("thead th")).map(
    (th) => th.textContent ?? "",
  );
  const rows = Array.from(table.querySelectorAll("tbody tr")) as HTMLElement[];
  const bySymbol = (symbol: string): HTMLElement => {
    const row = rows.find((tr) => tr.querySelector("td")?.textContent === symbol);
    if (!row) throw new Error(`no row for ${symbol}`);
    return row;
  };
  return { headers, rows, bySymbol };
}

describe("PaperTradePanel positions table", () => {
  beforeEach(() => {
    vi.mocked(fetchPaperLedgerView).mockResolvedValue(EMPTY_LEDGER);
    vi.mocked(fetchPaperPositions).mockResolvedValue({
      open: OPEN_ROWS,
      closed: [],
      status: STATUS,
    });
    vi.mocked(fetchPaperOpportunities).mockResolvedValue({
      rows: [],
      count: 0,
      ready: 0,
      near: 0,
      forming: 0,
      auto_enabled: true,
      note: "",
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the side of each position", async () => {
    const { bySymbol } = await renderOpenTable();

    expect(cellText(bySymbol("BTCUSDT"), "paper-side")).toBe("LONG");
    expect(cellText(bySymbol("NEARUSDT"), "paper-side")).toBe("SHORT");
    expect(cellText(bySymbol("ETHUSDT"), "paper-side")).toBe("LONG");
    expect(cellText(bySymbol("SOLUSDT"), "paper-side")).toBe("SHORT");
    expect(cellText(bySymbol("ADAUSDT"), "paper-side")).toBe("LONG");
  });

  it("shows the cap-book stream id instead of LEGACY", async () => {
    const { bySymbol } = await renderOpenTable();

    expect(cellText(bySymbol("BTCUSDT"), "paper-stream")).toBe("V1");
    expect(cellText(bySymbol("NEARUSDT"), "paper-stream")).toBe("PASSED|COMBO_02_V2|SHORT");
    expect(cellText(bySymbol("ETHUSDT"), "paper-stream")).toBe(
      "PASSED|COMBO_03_TRANSITION_B|LONG",
    );
    expect(cellText(bySymbol("SOLUSDT"), "paper-stream")).toBe("LEGACY");
    expect(cellText(bySymbol("ADAUSDT"), "paper-stream")).toBe("LEGACY");
  });

  it("shows the price source, and a dash when the row has none", async () => {
    const { bySymbol } = await renderOpenTable();

    expect(cellText(bySymbol("BTCUSDT"), "paper-price-source")).toBe("mark_price");
    expect(cellText(bySymbol("NEARUSDT"), "paper-price-source")).toBe("plan");
    expect(cellText(bySymbol("ETHUSDT"), "paper-price-source")).toBe("plan");

    const absent = cellText(bySymbol("ADAUSDT"), "paper-price-source");
    expect(absent).toBe("—");
    expect(absent).not.toBe("0");
    expect(absent).not.toBe("");
  });

  it("keeps the new columns aligned with the header", async () => {
    const { headers, rows } = await renderOpenTable();

    expect(headers).toContain("Side");
    expect(headers).toContain("Price src");
    expect(rows).toHaveLength(OPEN_ROWS.length);
    for (const row of rows) {
      const cells = Array.from(row.querySelectorAll("td"));
      expect(cells).toHaveLength(headers.length);
      expect(cells[headers.indexOf("Stream")].getAttribute("data-testid")).toBe("paper-stream");
      expect(cells[headers.indexOf("Side")].getAttribute("data-testid")).toBe("paper-side");
      expect(cells[headers.indexOf("Price src")].getAttribute("data-testid")).toBe(
        "paper-price-source",
      );
    }
  });

  it("shows closed time and realized profit on closed rows", async () => {
    vi.mocked(fetchPaperLedgerView).mockResolvedValue({
      ...EMPTY_LEDGER,
      rows: [
        ledgerRow("closed-bnb", "BNBUSDT", "VALID", {
          closed_at: "2026-10-22T17:28:46+00:00",
          exit_price: 748.2,
          pnl_usd: 17.9,
          r_multiple: 2.05,
        }),
      ],
    });
    render(<PaperTradePanel />);
    const closedAt = await screen.findByTestId("paper-closed-at");
    expect(closedAt.textContent).not.toBe("—");
    expect(closedAt.textContent).toContain("2026");
    const pnl = screen.getByTestId("paper-closed-pnl");
    expect(pnl.textContent).not.toBe("—");
    expect(pnl.textContent).toMatch(/17[.,]90/);
  });

  it("renders a dash, not 0, when the mark price is null", async () => {
    const { headers, bySymbol } = await renderOpenTable();
    const markIndex = headers.indexOf("Mark");
    expect(markIndex).toBeGreaterThan(-1);
    const markText = (symbol: string) =>
      bySymbol(symbol).querySelectorAll("td")[markIndex].textContent ?? "";

    expect(OPEN_ROWS[1].mark_price).toBeNull();
    expect(markText("NEARUSDT")).toBe("—");
    expect(markText("NEARUSDT")).not.toContain("0");
    expect(markText("BTCUSDT")).toBe((101.5).toLocaleString(undefined, {
      minimumFractionDigits: 4,
      maximumFractionDigits: 4,
    }));
  });
});

describe("PaperTradePanel watcher last-skip chip", () => {
  const ETH_SKIP = "ETHUSDT:ohlcv_catchup";
  const BTC_SKIP = "BTCUSDT:paper_open_skipped";
  const ETH_BAR = "2026-10-12T09:00:00+00:00";
  const BTC_BAR = "2026-10-12T10:00:00+00:00";

  function statusWith(btcRowSkip: string | null): PaperStatus {
    return {
      ...STATUS,
      v1_watcher: {
        enabled: true,
        timeframe: "1h",
        // The watcher-wide slot keeps the last reason noted, whatever the rows say.
        last_skip: BTC_SKIP,
        live: [
          {
            symbol: "BTCUSDT",
            timeframe: "1h",
            tier: "core",
            risk_percent: 0.02,
            tip_status: "WAITING_NEXT_BAR",
            tip_reason: "Current 1h tip already watermarked",
            last_skip: btcRowSkip,
            last_skip_bar: btcRowSkip ? BTC_BAR : null,
          },
          {
            symbol: "ETHUSDT",
            timeframe: "1h",
            tier: "secondary",
            risk_percent: 0.02,
            tip_status: "SYNCING",
            tip_reason: "Waiting for 1h and 4h history",
            last_skip: ETH_SKIP,
            last_skip_bar: ETH_BAR,
          },
        ],
      },
    };
  }

  function mockStatus(status: PaperStatus) {
    vi.mocked(fetchPaperPositions).mockResolvedValue({ open: [], closed: [], status });
    vi.mocked(fetchPaperOpportunities).mockResolvedValue({
      rows: [],
      count: 0,
      ready: 0,
      near: 0,
      forming: 0,
      auto_enabled: true,
      note: "",
    });
  }

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("hides the header chip when no book row still carries watcher.last_skip", async () => {
    mockStatus(statusWith(null));
    render(<PaperTradePanel />);

    // ETH's own row detail is unchanged and still shows its skip and bar.
    const ethDetail = await screen.findByText(`last skip: ${ETH_SKIP} @ ${ETH_BAR}`);
    expect(ethDetail).toBeTruthy();
    // BTC opened: its row has no skip line, and the stale header chip is gone.
    expect(screen.queryByText(new RegExp(BTC_SKIP))).toBeNull();
    expect(screen.queryByTestId("watcher-last-skip-chip")).toBeNull();
  });

  it("keeps the header chip while a book row still has that same reason", async () => {
    mockStatus(statusWith(BTC_SKIP));
    render(<PaperTradePanel />);

    const chip = await screen.findByTestId("watcher-last-skip-chip");
    expect(chip.textContent).toBe(`last skip: ${BTC_SKIP}`);
    expect(chip.getAttribute("title")).toBe(BTC_SKIP);
    // Row detail lines for both books are still rendered as before.
    expect(screen.getByText(`last skip: ${BTC_SKIP} @ ${BTC_BAR}`)).toBeTruthy();
    expect(screen.getByText(`last skip: ${ETH_SKIP} @ ${ETH_BAR}`)).toBeTruthy();
  });
});

describe("PaperTradePanel open risk stat", () => {
  beforeEach(() => {
    vi.mocked(fetchPaperLedgerView).mockResolvedValue(EMPTY_LEDGER);
    vi.mocked(fetchPaperPositions).mockResolvedValue({
      open: OPEN_ROWS,
      closed: [],
      status: STATUS,
    });
    vi.mocked(fetchPaperOpportunities).mockResolvedValue({
      rows: [],
      count: 0,
      ready: 0,
      near: 0,
      forming: 0,
      auto_enabled: true,
      note: "",
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("describes the stat as the sum of open risk, with no symbols and no percent", async () => {
    await renderOpenTable();

    const label = screen.getByText("Open risk $");
    const stat = label.parentElement as HTMLElement;
    const title = stat.getAttribute("title") ?? "";

    expect(title).toBe("Sum of open position risk_usd");
    expect(title).not.toContain("BTC/ETH/SOL");
    expect(title).not.toMatch(/BTC|ETH|SOL/);
    expect(title).not.toContain("%");
    expect(title).not.toMatch(/percent/i);
    // Label and value are as before: STATUS.open_risk_usd is 80.
    expect(label.textContent).toBe("Open risk $");
    expect(within(stat).getByText("$80.00")).toBeTruthy();
  });
});

describe("PaperTradePanel Auto control before status loads (P6)", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("does not read ON and does not send disable before status loads", async () => {
    vi.mocked(fetchPaperPositions).mockReturnValue(new Promise(() => {}));
    vi.mocked(fetchPaperOpportunities).mockReturnValue(new Promise(() => {}));
    render(<PaperTradePanel />);
    const toggle = screen.getByTestId("paper-auto-toggle") as HTMLButtonElement;
    expect(toggle.textContent).toBe("Auto …");
    expect(toggle.disabled).toBe(true);
    toggle.click();
    expect(vi.mocked(disablePaperTrade)).not.toHaveBeenCalled();
  });
});

describe("Paper dashboard safety and copy", () => {
  beforeEach(() => {
    vi.mocked(fetchPaperLedgerView).mockResolvedValue(EMPTY_LEDGER);
    vi.mocked(fetchPaperPositions).mockResolvedValue({ open: [], closed: [], status: STATUS });
    vi.mocked(fetchPaperOpportunities).mockResolvedValue({
      rows: [],
      count: 0,
      ready: 0,
      near: 0,
      forming: 0,
      auto_enabled: true,
      note: "",
    });
    vi.mocked(fetchDashboardReview).mockResolvedValue(REVIEW);
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("builds the header from the review and drops the stale phrases", async () => {
    render(<PaperTradePanel />);
    const config = await screen.findByTestId("paper-book-config");
    expect(config).toHaveTextContent("V1");
    expect(config).toHaveTextContent("ATOMUSDT");
    expect(screen.getByTestId("paper-auto-line")).toHaveTextContent("Watchers that can open trades: V1, PASSED");
    expect(screen.getByText(/COMBO_02 v1 \(1 1h books\)/)).toBeTruthy();
    expect(screen.queryByText(/ETH\/SOL \+ liquid majors/)).toBeNull();
    expect(screen.queryByText(/V2\.1-A/)).toBeNull();
    expect(screen.queryByText(/auto opens only from the v1 watcher/i)).toBeNull();
    expect(screen.getByTestId("paper-missed-breakdown")).toHaveTextContent("160");
    expect(screen.getByTestId("paper-missed-breakdown")).toHaveTextContent("late bar 1");
    expect(screen.getByTestId("paper-combined-label")).toHaveTextContent("all streams combined");
  });

  it("keeps Reset off while a forward test is running", async () => {
    render(<PaperTradePanel />);
    const reset = (await screen.findByTestId("paper-reset")) as HTMLButtonElement;
    expect(reset.disabled).toBe(true);
    expect(screen.getByTestId("paper-reset-locked")).toHaveTextContent("V1");
    expect(screen.getByTestId("paper-reset-locked")).toHaveTextContent("PAPER_V1_WATCHER_ENABLED");
    fireEvent.click(reset);
    expect(screen.queryByTestId("paper-confirm")).toBeNull();
  });

  it("asks for the action name and logs a successful reset", async () => {
    vi.mocked(fetchDashboardReview).mockResolvedValue({
      ...REVIEW,
      reset_blocked: false,
      forward_tests: [{ id: "V1", running: false, why: "PAPER_V1_WATCHER_ENABLED" }],
    });
    vi.mocked(resetPaperTrade).mockResolvedValue(STATUS);
    render(<PaperTradePanel />);
    const reset = (await screen.findByTestId("paper-reset")) as HTMLButtonElement;
    expect(reset.disabled).toBe(false);
    fireEvent.click(reset);
    fireEvent.change(screen.getByTestId("paper-confirm-input"), { target: { value: "NO" } });
    expect((screen.getByTestId("paper-confirm-submit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId("paper-confirm-input"), { target: { value: "RESET" } });
    fireEvent.click(screen.getByTestId("paper-confirm-submit"));
    expect(await screen.findByTestId("paper-action-log")).toHaveTextContent("RESET");
    expect(screen.getByTestId("paper-action-log").textContent).toMatch(/IST/);
    expect(vi.mocked(resetPaperTrade)).toHaveBeenCalledTimes(1);
  });

  it("shows only VALID rows from the forward start by default", async () => {
    vi.mocked(fetchPaperLedgerView).mockResolvedValue({
      ...EMPTY_LEDGER,
      rows: [
        ledgerRow("valid", "SUIUSDT", "VALID"),
        ledgerRow("old", "ARBUSDT", "PRE_COUNTERS", { opened_at: "2026-10-20T18:05:31+00:00" }),
        ledgerRow("legacy", "NEARUSDT", "LEGACY", { opened_at: "2026-10-09T07:45:02+00:00" }),
        ledgerRow("dup", "TRXUSDT", "DUPLICATE_FILL", { status: "CANCELLED", exit_reason: "duplicate_fill" }),
      ],
      total_rows: 4,
      default_rows: 1,
    });
    render(<PaperTradePanel />);
    expect(await screen.findByText("SUIUSDT")).toBeTruthy();
    expect(screen.queryByText("ARBUSDT")).toBeNull();
    expect(screen.queryByText("NEARUSDT")).toBeNull();
    expect(screen.queryByText("TRXUSDT")).toBeNull();
    const scope = screen.getByTestId("paper-ledger-scope");
    expect(scope).toHaveTextContent("VALID trades from each book's forward start");
    expect(scope).toHaveTextContent("Hidden: 3 rows (LEGACY 1 · PRE_COUNTERS 1 · DUPLICATE_FILL 1)");
    const tags = screen.getAllByTestId("paper-evidence-tag").map((cell) => cell.textContent);
    expect(tags).toEqual(["VALID"]);
    expect(screen.getByTestId("paper-history-toggle")).toHaveAttribute("aria-pressed", "false");
  });

  it("shows every row with its tag when show all history is on, and back", async () => {
    vi.mocked(fetchPaperLedgerView).mockResolvedValue({
      ...EMPTY_LEDGER,
      rows: [
        ledgerRow("valid", "SUIUSDT", "VALID"),
        ledgerRow("old", "ARBUSDT", "PRE_COUNTERS", {
          opened_at: "2026-10-20T18:05:31+00:00",
          closed_at: "2026-10-20T18:05:31+00:00",
          evidence_matches: ["PRE_COUNTERS", "CATCHUP_CLOSE"],
        }),
        ledgerRow("dup", "TRXUSDT", "DUPLICATE_FILL", { status: "CANCELLED", exit_reason: "duplicate_fill" }),
      ],
      total_rows: 3,
      default_rows: 1,
    });
    render(<PaperTradePanel />);
    await screen.findByText("SUIUSDT");
    fireEvent.click(screen.getByTestId("paper-history-toggle"));
    expect(screen.getByTestId("paper-history-toggle")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("paper-ledger-scope")).toHaveTextContent("All ledger history");
    expect(screen.getByText("ARBUSDT")).toBeTruthy();
    expect(screen.getByText("TRXUSDT")).toBeTruthy();
    const tagOf = (symbol: string) =>
      within(screen.getByText(symbol).closest("tr") as HTMLElement).getByTestId("paper-evidence-tag");
    expect(tagOf("SUIUSDT")).toHaveTextContent("VALID");
    expect(tagOf("ARBUSDT")).toHaveTextContent("PRE_COUNTERS");
    expect(tagOf("ARBUSDT").getAttribute("title")).toContain("CATCHUP_CLOSE");
    expect(tagOf("TRXUSDT")).toHaveTextContent("DUPLICATE_FILL");
    fireEvent.click(screen.getByTestId("paper-history-toggle"));
    expect(screen.queryByText("ARBUSDT")).toBeNull();
    expect(screen.queryByText("TRXUSDT")).toBeNull();
  });

  it("shows nothing in the default view until the ledger view loads", async () => {
    vi.mocked(fetchPaperLedgerView).mockResolvedValue({
      read_only: true,
      available: false,
      error: "ledger read failed: RuntimeError: database not connected",
      as_of: "2026-10-22T06:00:00+00:00",
      books: [],
      rows: [],
    });
    vi.mocked(fetchPaperPositions).mockResolvedValue({
      open: [],
      closed: [position("engine-row", "LTCUSDT", "SHORT", { source: "PASSED_CAP_BOOK" }, { status: "CLOSED" })],
      status: STATUS,
    });
    render(<PaperTradePanel />);
    expect(await screen.findByTestId("paper-ledger-error")).toHaveTextContent("database not connected");
    expect(screen.queryByText("LTCUSDT")).toBeNull();
    fireEvent.click(screen.getByTestId("paper-history-toggle"));
    expect(screen.getByText("LTCUSDT")).toBeTruthy();
    const cell = within(screen.getByText("LTCUSDT").closest("tr") as HTMLElement).getByTestId("paper-evidence-tag");
    expect(cell).toHaveTextContent("—");
  });

  it("per-book cards read the ledger view: nominal R, R on actual risk and the cap count", async () => {
    vi.mocked(fetchPaperLedgerView).mockResolvedValue(EMPTY_LEDGER);
    render(<PaperTradePanel />);
    const v1 = await screen.findByTestId("paper-book-V1");
    expect(v1).toHaveTextContent("Trades 2");
    expect(v1).toHaveTextContent(/Equity \$964[.,]73/);
    expect(screen.getByTestId("paper-book-V1-r")).toHaveTextContent(/Nominal R -1[.,]93 · R on actual risk -2[.,]23/);
    expect(screen.getByTestId("paper-book-V1-capped")).toHaveTextContent("Shrunk by 2× leverage cap 1");
    expect(screen.getByTestId("paper-book-PASSED")).toHaveTextContent("Trades 1");
    expect(screen.getByTestId("paper-book-LEGACY")).toHaveTextContent("Disabled: history only");
  });
});
