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
