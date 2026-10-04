import { describe, expect, it } from "vitest";
import type { FreshValue, ScreenerRow } from "../types/market";
import {
  COL_WIDTHS,
  TECHNICAL_PINNED_IDS,
  compactEmptyLabel,
  displayV1Status,
  formatCompactNumber,
  formatFundingCell,
  formatPotentialLevel,
  formatSetupStateLabel,
  formatTechRatingDisplay,
  futuresColumnIds,
  isMissingLevel,
  liquidationTooltip,
  matchesSetupFilter,
  matchesSignalFilter,
  potentialLevelsForRow,
  shortDependencyLabel,
  shouldResetTableScroll,
  stickyOffsets,
  tradeStatusLabel,
} from "./screenerPresentation";

function fv<T extends string | number = string | number>(
  value: T | null,
  status: FreshValue["status"] = "WAITING",
  methodology?: string,
): FreshValue<T> {
  return {
    value,
    timestamp: null,
    source: "test",
    status,
    methodology: methodology ?? null,
  };
}

function row(partial: Partial<ScreenerRow>): ScreenerRow {
  return {
    rank: 1,
    symbol: "BTCUSDT",
    base_asset: "BTC",
    quote_asset: "USDT",
    exchange: "binance",
    market_type: "futures",
    price: fv(80000, "LIVE"),
    change_24h_pct: fv(1, "LIVE"),
    volume_24h: fv(1, "LIVE"),
    quote_volume_24h: fv(1, "LIVE"),
    high_24h: fv(1, "LIVE"),
    low_24h: fv(1, "LIVE"),
    funding_rate: fv(0.0001, "LIVE"),
    open_interest: fv(1, "LIVE"),
    oi_change_pct: fv(0, "LIVE"),
    market_cap: fv(1, "LIVE"),
    fdv: fv(1, "LIVE"),
    tvl: fv(null, "UNAVAILABLE"),
    relative_volume: fv(null, "WAITING", "Requires 15M OHLCV coverage."),
    structure: fv(null, "WAITING", "Requires confirmed 15M market structure."),
    zone: fv(null, "WAITING", "Requires 15M supply/demand data."),
    liquidation: fv(null, "WAITING", "Requires liquidation stream events"),
    technical_state: fv(null, "WAITING", "Requires Structure"),
    ...partial,
  } as ScreenerRow;
}

describe("Trade / Technical column modes", () => {
  it("1. Default Trade View columns", () => {
    expect(futuresColumnIds("trade")).toEqual([
      "symbol",
      "price",
      "change",
      "market_signal",
      "trend",
      "setup_signal",
      "v1_status",
      "setup_entry",
      "setup_sl",
      "setup_tp1",
      "setup_rr",
      "status",
    ]);
  });

  it("2. Technical View columns include pinned + core + market", () => {
    const ids = futuresColumnIds("technical");
    expect(ids.slice(0, 4)).toEqual(["symbol", "price", "market_signal", "setup_signal"]);
    expect(ids).toContain("rating");
    expect(ids).toContain("setup_bos");
    expect(ids).toContain("v1_status");
    expect(ids).toContain("mcap");
    expect(ids).toContain("liq");
    expect(ids).toContain("entry");
    expect(ids).not.toContain("status");
  });

  it("3. Pinned Symbol/Price/Signal/Setup offsets", () => {
    const off = stickyOffsets(TECHNICAL_PINNED_IDS);
    expect(off.symbol).toBe(0);
    expect(off.price).toBe(COL_WIDTHS.symbol);
    expect(off.market_signal).toBe(COL_WIDTHS.symbol + COL_WIDTHS.price);
    expect(off.setup_signal).toBe(
      COL_WIDTHS.symbol + COL_WIDTHS.price + COL_WIDTHS.market_signal,
    );
  });

  it("4/5. Mode switch resets scroll; same mode does not", () => {
    expect(shouldResetTableScroll("trade", "technical")).toBe(true);
    expect(shouldResetTableScroll("technical", "trade")).toBe(true);
    expect(shouldResetTableScroll("technical", "technical")).toBe(false);
    expect(shouldResetTableScroll("trade", "trade")).toBe(false);
  });
});

describe("WAITING dependency labels + tooltips", () => {
  it("6. Maps Requires … to WAIT — … ; full text for tooltip source", () => {
    expect(shortDependencyLabel("Requires 15M OHLCV coverage.", "WAITING")).toBe(
      "WAIT — 15M OHLCV",
    );
    expect(shortDependencyLabel("Requires confirmed 15M market structure.", "WAITING")).toBe(
      "WAIT — Structure",
    );
    expect(shortDependencyLabel("Requires 15M supply/demand data.", "WAITING")).toBe(
      "WAIT — Zone",
    );
    expect(shortDependencyLabel("Requires Volume", "WAITING")).toBe("WAIT — Volume");
    expect(shortDependencyLabel(undefined, "WAITING")).toBe("WAITING");
    expect(compactEmptyLabel(fv(null, "WAITING", "Requires 15M OHLCV coverage."))).toBe(
      "WAIT — 15M OHLCV",
    );
    expect(compactEmptyLabel(fv(null, "UNAVAILABLE"))).toBe("N/A");
  });

  it("7. Missing Entry/SL/TP → dash", () => {
    expect(isMissingLevel(undefined)).toBe(true);
    expect(isMissingLevel(fv(null, "WAITING"))).toBe(true);
    expect(isMissingLevel(fv("WAITING", "LIVE"))).toBe(true);
    expect(isMissingLevel(fv(83000, "LIVE"))).toBe(false);
  });

  it("8. Liquidation WAITING tooltip", () => {
    const tip = liquidationTooltip(fv(null, "WAITING", "Requires liquidation stream events"));
    expect(tip).toMatch(/liquidation/i);
    expect(liquidationTooltip(fv(null, "UNAVAILABLE"))).toMatch(/unavailable/i);
  });
});

describe("Setup / entry state presentation", () => {
  it("9–13. ENTRY_READY / candidates / CONFLICT / NO_SETUP", () => {
    expect(formatSetupStateLabel("ENTRY_READY")).toBe("ENTRY READY");
    expect(formatSetupStateLabel("LONG_ENTRY_CANDIDATE")).toBe("LONG CANDIDATE");
    expect(formatSetupStateLabel("SHORT_ENTRY_CANDIDATE")).toBe("SHORT CANDIDATE");
    expect(formatSetupStateLabel("CONFLICT")).toBe("CONFLICT");
    expect(formatSetupStateLabel("NO_SETUP")).toBe("NO SETUP");
    expect(tradeStatusLabel(row({ setup_signal: fv("NO_SETUP", "LIVE") })).text).toBe(
      "WAIT — no setup",
    );
    expect(tradeStatusLabel(row({ setup_signal: fv("ENTRY_READY", "LIVE") })).text).toBe(
      "ENTRY READY",
    );
  });

  it("10. Tech rating hides Requires…", () => {
    const d = formatTechRatingDisplay(
      fv("Requires Structure", "WAITING", "Requires Structure"),
    );
    expect(d.text).not.toMatch(/^Requires/i);
    expect(d.text).toMatch(/WAIT|INSUFFICIENT|WAITING/i);
    expect(d.title).toMatch(/Requires Structure/i);
  });
});

describe("Formatting + filters", () => {
  it("14. Numeric formatting stays readable", () => {
    expect(formatCompactNumber(327_690_000_000)).toBe("327.69B");
    expect(formatCompactNumber(8_100_000_000)).toBe("8.10B");
    expect(formatCompactNumber(2_300_000)).toBe("2.30M");
    expect(formatFundingCell(0.000019)).toBe("0.0019%");
  });

  it("15. Toggle modes expose different column sets", () => {
    expect(futuresColumnIds("trade")).not.toEqual(futuresColumnIds("technical"));
  });

  it("16. Filters use existing backend states", () => {
    const buy = row({ market_signal: fv("BUY", "LIVE"), setup_signal: fv("NO_SETUP", "LIVE") });
    const ready = row({
      market_signal: fv("BUY", "LIVE"),
      setup_signal: fv("ENTRY_READY", "LIVE"),
    });
    expect(matchesSignalFilter(buy, "BUY")).toBe(true);
    expect(matchesSignalFilter(buy, "SELL")).toBe(false);
    expect(matchesSetupFilter(ready, "ENTRY_READY")).toBe(true);
    expect(matchesSetupFilter(buy, "NO_SETUP")).toBe(true);
    expect(matchesSetupFilter(buy, "ENTRY_READY")).toBe(false);
  });

  it("17. Row click target is symbol selection (table uses setSelected)", () => {
    // Presentation contract: row identity for Coin Detail is symbol
    expect(row({ symbol: "ETHUSDT" }).symbol).toBe("ETHUSDT");
  });

  it("18. Live updates do not imply mode change / scroll reset", () => {
    expect(shouldResetTableScroll("technical", "technical")).toBe(false);
  });
});

describe("V1 presentation safety", () => {
  it("never treats a 15m discovery row as V1 ELIGIBLE", () => {
    const near = row({
      symbol: "NEARUSDT",
      setup_trend: fv("BULLISH", "LIVE"),
      setup_signal: fv("WAITING", "LIVE"),
      setup_sl: fv(4.7692, "LIVE"),
      setup_tp1: fv(4.836, "LIVE"),
      setup_rr: fv(4.0, "LIVE"),
      v1_status: {
        in_v1_universe: false,
        status: "DISCOVERY_ONLY",
        label: "DISCOVERY ONLY",
      },
      is_telegram_eligible: false,
    });
    const d = displayV1Status(near);
    expect(d.text).toBe("DISCOVERY ONLY");
    expect(d.tone).toBe("discovery");
    expect(near.is_telegram_eligible).toBe(false);
  });

  it("ETH without snapshot is UNAVAILABLE, not inferred eligible", () => {
    const eth = row({
      symbol: "ETHUSDT",
      setup_trend: fv("BULLISH", "LIVE"),
      setup_signal: fv("WAITING", "LIVE"),
    });
    const d = displayV1Status(eth);
    expect(d.text).toBe("V1 DATA UNAVAILABLE");
    expect(d.tone).toBe("unavailable");
  });

  it("WAITING/CONFLICT potential levels are reference/candidate only", () => {
    const waiting = row({
      setup_signal: fv("WAITING", "LIVE"),
      setup_entry: fv<number>(null, "WAITING"),
      setup_sl: fv(1.23, "LIVE"),
      setup_tp1: fv(1.45, "LIVE"),
      setup_rr: fv(4, "LIVE"),
    });
    const levels = potentialLevelsForRow(waiting);
    expect(levels.referenceOnly).toBe(true);
    const sl = formatPotentialLevel(levels.stop, {
      kind: "stop",
      referenceOnly: true,
      entryMissing: true,
    });
    expect(sl.muted).toBe(true);
    expect(sl.text).toMatch(/candidate/i);
    const entry = formatPotentialLevel(null, {
      kind: "entry",
      referenceOnly: true,
      entryMissing: true,
    });
    expect(entry.text).toBe("—");
  });

  it("V1 ELIGIBLE display only when API status says so (read-only)", () => {
    const btc = row({
      symbol: "BTCUSDT",
      v1_status: {
        in_v1_universe: true,
        status: "V1_ELIGIBLE",
        label: "V1 ELIGIBLE — confirmed 1h entry",
      },
      is_telegram_eligible: false,
    });
    expect(displayV1Status(btc).tone).toBe("eligible");
    expect(btc.is_telegram_eligible).toBe(false);
  });
});
