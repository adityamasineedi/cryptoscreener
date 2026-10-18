/** @vitest-environment jsdom */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  disablePaperTrade,
  fetchPaperOpportunities,
  fetchPaperPositions,
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
    disablePaperTrade: vi.fn(),
    // Mounted PassedRegimePanel: never resolves, so it stays in its loading state.
    fetchPassedRegime: vi.fn(() => new Promise(() => {})),
    // Mounted Book16PaperPanel: same, never resolves.
    fetchBook16Paper: vi.fn(() => new Promise(() => {})),
  };
});

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
    vi.mocked(fetchPaperPositions).mockResolvedValue({
      open: [],
      closed: [
        position(
          "closed-bnb",
          "BNBUSDT",
          "SHORT",
          { source: "PASSED_CAP_BOOK", stream: "PASSED|COMBO_02_V2|SHORT" },
          {
            status: "CLOSED",
            closed_at: "2026-10-12T17:28:46+00:00",
            exit_price: 748.2,
            exit_reason: "TP1",
            pnl_usd: 17.9,
            r_multiple: 2.05,
          },
        ),
      ],
      status: STATUS,
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
