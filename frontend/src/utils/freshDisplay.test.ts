import { describe, expect, it } from "vitest";
import type { FreshValue, ScreenerRow } from "../types/market";
import {
  formatRatio,
  mergeScreenerRowPreferLive,
  normalizeTvlFresh,
  preferFresherFresh,
  statusLaneLabel,
} from "./freshDisplay";

function fv(
  value: number | null,
  status: FreshValue["status"],
  timestamp: string,
): FreshValue {
  return { value, timestamp, source: "binance_ws", status };
}

describe("statusLaneLabel", () => {
  it("never hardcodes LIVE stream for unavailable price", () => {
    expect(statusLaneLabel("UNAVAILABLE", "price")).toMatch(/timed out|last known/i);
    expect(statusLaneLabel("UNAVAILABLE", "price")).not.toMatch(/LIVE stream/i);
    expect(statusLaneLabel("LIVE", "price")).toMatch(/ticker/i);
  });

  it("labels volume by status", () => {
    expect(statusLaneLabel("UNAVAILABLE", "volume")).toMatch(/last known/i);
    expect(statusLaneLabel("LIVE", "volume")).toMatch(/24h quote/i);
  });
});

describe("formatRatio", () => {
  it("formats vol/mcap as percent", () => {
    expect(formatRatio(0.006245415914997968)).toBe("0.62%");
  });
});

describe("normalizeTvlFresh", () => {
  it("maps TVL 0 to N/A unavailable", () => {
    const out = normalizeTvlFresh(fv(0, "LIVE", "2026-10-01T00:00:00Z"));
    expect(out?.value).toBeNull();
    expect(out?.status).toBe("UNAVAILABLE");
    expect(out?.methodology).toMatch(/N\/A/i);
  });
});

describe("preferFresherFresh / merge", () => {
  it("keeps LIVE WS tick over older UNAVAILABLE snapshot", () => {
    const live = fv(83844, "LIVE", "2026-10-01T15:30:00Z");
    const snap = fv(83742, "UNAVAILABLE", "2026-10-01T15:28:00Z");
    expect(preferFresherFresh(live, snap)?.status).toBe("LIVE");
    expect(preferFresherFresh(live, snap)?.value).toBe(83844);
  });

  it("merges screener row stream fields", () => {
    const prev = {
      symbol: "BTCUSDT",
      price: fv(83844, "LIVE", "2026-10-01T15:30:00Z"),
      quote_volume_24h: fv(1e10, "LIVE", "2026-10-01T15:30:00Z"),
    } as unknown as ScreenerRow;
    const next = {
      symbol: "BTCUSDT",
      price: fv(83700, "UNAVAILABLE", "2026-10-01T15:28:00Z"),
      quote_volume_24h: fv(1e10, "UNAVAILABLE", "2026-10-01T15:28:00Z"),
      market_cap: fv(1e12, "CACHED", "2026-10-01T15:00:00Z"),
    } as unknown as ScreenerRow;
    const merged = mergeScreenerRowPreferLive(prev, next);
    expect(merged.price.status).toBe("LIVE");
    expect(merged.price.value).toBe(83844);
    expect(merged.quote_volume_24h.status).toBe("LIVE");
    expect(merged.market_cap?.status).toBe("CACHED");
  });
});
