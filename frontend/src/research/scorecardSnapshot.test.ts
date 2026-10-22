import { describe, expect, it } from "vitest";
import {
  SCORECARD_SNAPSHOT,
  SCORECARD_SNAPSHOT_DATE,
  SCORECARD_SNAPSHOT_SOURCE,
  LATER_STUDY_KEYS,
  PASSED_BOOK_VERDICT_TEXT,
  hasBacktestLookaheadD3,
  paperCardVerdict,
  verdictText,
  researchVerdictFor,
} from "./scorecardSnapshot";

describe("scorecardSnapshot", () => {
  it("carries the snapshot date and source commit", () => {
    expect(SCORECARD_SNAPSHOT_DATE).toBe("2026-10-22");
    expect(SCORECARD_SNAPSHOT_SOURCE).toContain("7e92b67");
  });

  it("uses the final outcome where the SCORECARD only had the DISCOVERY row", () => {
    // SCORECARD lists these as NOT_RUN; KILLED.md / DECISIONS hold the final outcome.
    expect(SCORECARD_SNAPSHOT["COMBO_03_TRANSITION|SHORT"].verdict).toBe("KILLED");
    expect(SCORECARD_SNAPSHOT["DAILY_TREND_14_ROC90|BOTH"].stage).toBe("VALIDATION");
    expect(SCORECARD_SNAPSHOT["DAILY_TREND_03_SMA50|BOTH"]).toMatchObject({ verdict: "KILLED", stage: "VALIDATION" });
    expect(SCORECARD_SNAPSHOT["MTF_4H_16_BREAKOUT252|LONG"].verdict).toBe("INCONCLUSIVE");
    expect(SCORECARD_SNAPSHOT["COMBO_02_V2|SHORT"].verdict).toBe("NOT RUN");
  });

  it("maps panel ids to research keys", () => {
    expect(researchVerdictFor("COMBO_01", "LONG")?.key).toBe("COMBO_01|LONG");
    expect(researchVerdictFor("COMBO_02_V2_1A", "SHORT")?.key).toBe("COMBO_02_V2_1_A|SHORT");
    expect(researchVerdictFor("COMBO_05", "SHORT")?.key).toBe("COMBO_05+PBFIX|SHORT");
    expect(researchVerdictFor("COMBO_05", "SHORT")?.note).toContain("pullback");
    expect(researchVerdictFor("COMBO_04_HTF", "SHORT")?.key).toBe("COMBO_04_HTF+PBFIX|SHORT");
    expect(researchVerdictFor("COMBO_04_HTF", "LONG")).toBeNull();
    expect(researchVerdictFor("UNKNOWN_BOOK", "LONG")).toBeNull();
  });

  it("carries the later families from KILLED.md and FINAL_VERDICT (D218)", () => {
    expect(LATER_STUDY_KEYS).toHaveLength(8);
    for (const key of LATER_STUDY_KEYS) expect(SCORECARD_SNAPSHOT[key]?.family).toBeTruthy();
    expect(SCORECARD_SNAPSHOT["N2_BTC_FUNDING_CARRY|NON_DIRECTIONAL"]).toMatchObject({
      verdict: "INCONCLUSIVE",
      stage: "VALIDATION",
      source: "FINAL_VERDICT.md",
    });
    expect(SCORECARD_SNAPSHOT["HD2_FUNDING_EXTREMES|LONG_SHORT"]).toMatchObject({ verdict: "INCONCLUSIVE", stage: "DISCOVERY" });
    expect(SCORECARD_SNAPSHOT["N1_BTC_RANGE_GRID|NON_DIRECTIONAL"].verdict).toBe("KILLED");
    expect(SCORECARD_SNAPSHOT["HR1_SWEEP_CHOCH_30M|BOTH"].verdict).toBe("KILLED");
    expect(SCORECARD_SNAPSHOT["HD1_TAKER_FLOW|LONG_SHORT"].verdict).toBe("KILLED");
    const verdicts = new Set(Object.values(SCORECARD_SNAPSHOT).map((e) => e.verdict));
    expect([...verdicts].sort()).toEqual(["INCONCLUSIVE", "KILLED", "NOT RUN"]);
  });

  it("maps a variant id to its book and the UNTESTABLE books to NOT RUN", () => {
    const hit = researchVerdictFor("INTRADAY_BOS_SD_V1.C", "SHORT");
    expect(hit?.key).toBe("INTRADAY_BOS_SD_V1|SHORT");
    expect(hit?.entry.verdict).toBe("NOT RUN");
    expect(hit?.note).toContain("all variants");
    expect(researchVerdictFor("SMALL_CAP_VOLUME_BOS", "LONG")?.entry.reason).toContain("UNTESTABLE");
    expect(researchVerdictFor("GTB-ARI-4", "LONG")).toBeNull();
  });

  it("carries the owner D200 wording for V1 and CANDLE12_V2", () => {
    expect(verdictText(SCORECARD_SNAPSHOT["COMBO_02_V1|LONG"])).toBe(
      "KILLED (OOS_FAIL archived; COMBO_02 LONG killed in DISCOVERY)"
    );
    expect(SCORECARD_SNAPSHOT["CANDLE12_V2|LONG"]).toMatchObject({ verdict: "KILLED", stage: "DISCOVERY" });
    expect(verdictText(SCORECARD_SNAPSHOT["CANDLE12_V2|LONG"])).toBe("KILLED (Gate 2: below random p90)");
    expect(SCORECARD_SNAPSHOT["CANDLE12_V2|LONG"].source).toContain("KILLED.md");
    expect(verdictText(SCORECARD_SNAPSHOT["COMBO_01|LONG"])).toBe("KILLED");
  });

  it("gives the dashboard cards their research verdicts", () => {
    expect(paperCardVerdict("V1")?.text).toBe("KILLED (OOS_FAIL archived; COMBO_02 LONG killed in DISCOVERY)");
    expect(paperCardVerdict("COMBO_02 v1")?.text).toBe(paperCardVerdict("V1")?.text);
    expect(paperCardVerdict("CANDLE12_V2")?.text).toBe("KILLED (Gate 2: below random p90)");
    expect(paperCardVerdict("PASSED")).toMatchObject({ verdict: "KILLED", text: PASSED_BOOK_VERDICT_TEXT });
    expect(PASSED_BOOK_VERDICT_TEXT).toBe("KILLED strategies (research) · parity test");
    expect(paperCardVerdict("FWD-1")).toMatchObject({ verdict: "INCONCLUSIVE", text: "INCONCLUSIVE" });
    expect(paperCardVerdict("FWD-1 book 16")?.text).toBe("INCONCLUSIVE");
    expect(paperCardVerdict("LEGACY")).toBeNull();
    expect(paperCardVerdict("FWD-3")).toBeNull();
  });

  it("flags only the two V2 ids named in D3", () => {
    expect(hasBacktestLookaheadD3("COMBO_02_V2_1_A")).toBe(true);
    expect(hasBacktestLookaheadD3("COMBO_02_V2_1A")).toBe(true);
    expect(hasBacktestLookaheadD3("COMBO_02_V2_1B")).toBe(true);
    expect(hasBacktestLookaheadD3("COMBO_02_V2")).toBe(false);
  });
});
