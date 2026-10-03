import { describe, expect, it } from "vitest";
import { dependencyLabel, emptyFreshLabel } from "./FreshCell";
import type { FreshValue } from "../types/market";

describe("dependencyLabel / emptyFreshLabel", () => {
  it("surfaces asset mapping reason instead of Waiting for live data", () => {
    const m =
      "transaction_count: no asset/chain/contract mapping in config/assets.yaml — do not assume chain from futures symbol";
    expect(dependencyLabel(m)).toMatch(/no asset\/chain\/contract mapping/i);
    const fv: FreshValue = {
      value: null,
      timestamp: null,
      source: "onchain",
      status: "WAITING",
      methodology: m,
    };
    expect(emptyFreshLabel(fv)).toMatch(/no asset\/chain\/contract mapping/i);
    expect(emptyFreshLabel(fv)).not.toMatch(/Waiting for live data/i);
  });

  it("surfaces sentiment provider configuration reason", () => {
    const m =
      "engagement requires configured sentiment provider (Santiment/LunarCrush/etc via providers.yaml) — never fabricated";
    expect(dependencyLabel(m)).toMatch(/sentiment provider|providers\.yaml|never fabricated/i);
    const fv: FreshValue = {
      value: null,
      timestamp: null,
      source: "sentiment",
      status: "WAITING",
      methodology: m,
    };
    expect(emptyFreshLabel(fv)).not.toMatch(/Waiting for live data/i);
  });

  it("LunarCrush WAITING / STALE / UNAVAILABLE stay honest (no fake zeros)", () => {
    const waiting: FreshValue = {
      value: null,
      timestamp: null,
      source: "lunarcrush",
      status: "WAITING",
      methodology: "sentiment: waiting for LunarCrush bulk response — never fabricated",
    };
    const stale: FreshValue = {
      value: 67,
      timestamp: new Date(Date.now() - 2_000_000).toISOString(),
      source: "lunarcrush",
      status: "STALE",
      methodology: "sentiment <- lunarcrush.sentiment",
    };
    const unavailable: FreshValue = {
      value: null,
      timestamp: null,
      source: "lunarcrush",
      status: "UNAVAILABLE",
      methodology: "sentiment: Asset not covered by LunarCrush",
    };
    expect(emptyFreshLabel(waiting)).toMatch(/waiting|LunarCrush|never fabricated/i);
    expect(emptyFreshLabel(waiting)).not.toBe("0");
    expect(emptyFreshLabel(unavailable)).toMatch(/not covered|unavailable|LunarCrush/i);
    expect(emptyFreshLabel(unavailable)).not.toBe("0");
    // STALE with a real value should not use empty label path as fabricated zero
    expect(stale.value).toBe(67);
    expect(stale.status).toBe("STALE");
  });

  it("keeps Requires OHLCV wording for non-compact / tooltip paths", () => {
    expect(dependencyLabel("Requires 15M OHLCV — RVOL not computed")).toBe("Requires 15M OHLCV");
  });

  it("compact empty cells use short WAIT labels", () => {
    const fv: FreshValue = {
      value: null,
      timestamp: null,
      source: "volume_engine",
      status: "WAITING",
      methodology: "Requires 15M OHLCV — RVOL not computed",
    };
    expect(emptyFreshLabel(fv, true)).toBe("WAIT — 15M OHLCV");
    expect(emptyFreshLabel(fv, true)).not.toMatch(/^Requires/i);
  });
});
