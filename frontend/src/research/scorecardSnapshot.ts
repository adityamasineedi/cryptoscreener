/**
 * Static research verdict snapshot for the Profit checker (owner D201, owner's "D182").
 *
 * Snapshot date: 2026-10-22.
 * Source: research-audit worktree, branch research/audit-2026-10-20, commit 7e92b67
 *   (research/SCORECARD.csv last changed in 6f3caad, research/KILLED.md in e5fadfc).
 * Generated once, by hand-run script, from:
 *   1. research/SCORECARD.csv (strategy_side -> DISCOVERY verdict);
 *   2. research/KILLED.md, which overrides 1 with each side's final kill, including
 *      sides the SCORECARD still lists as NOT_RUN and kills made later at VALIDATION;
 *   3. research/DECISIONS.md for outcomes recorded only there: the COMBO_02_V2 family
 *      NOT RUN (D127, owner D110), MTF_4H_16 INCONCLUSIVE (D122, owner D96), and
 *      EMA200 / SMA50 falling with their family's VALIDATION kill (D122).
 * SCORECARD rows without a strategy|side key (R-REG-3..7, PATH_B_EXPERIMENTAL) are left out.
 *
 * Extended 2026-10-22 for the Strategies page (owner D191, DECISIONS D218), from the same
 * three files, unchanged at research-audit 019c0bb:
 *   4. research/KILLED.md for N1, HR-1 and HD-1 (killed at DISCOVERY; VALIDATION not reached);
 *   5. research/FINAL_VERDICT.md for N2 (INCONCLUSIVE at VALIDATION, D172) and HD-2
 *      (INCONCLUSIVE at DISCOVERY, D168);
 *   6. research/SCORECARD.csv UNTESTABLE rows for INTRADAY_BOS_SD_V1 and SMALL_CAP_VOLUME_BOS,
 *      shown as NOT RUN with the UNTESTABLE reason.
 * `family` names the later study an entry belongs to (FINAL_VERDICT / KILLED.md names).
 *
 * The live app cannot read the research worktree at runtime. This file does not refresh
 * itself: regenerate it when the research verdicts change. Display only; nothing else
 * may read it.
 */

export const SCORECARD_SNAPSHOT_DATE = "2026-10-22";
export const SCORECARD_SNAPSHOT_SOURCE = "research-audit 7e92b67";
export const RESEARCH_RESULTS_PAGE_PATH = "research/out/report/index.html";

export type ResearchVerdict = {
  verdict: string;
  stage: string;
  reason: string;
  source: string;
  /** Later study name (N1, N2, HD-2, HR-1, HD-1, H-COMBO, H-F1X, XS-MOM); unset for tournament rows. */
  family?: string;
};

export const SCORECARD_SNAPSHOT: Readonly<Record<string, ResearchVerdict>> = {
  "CANDLE12_V2|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 56 trades; +0.297 R/trade", source: "KILLED.md" },
  "CANDLE12_V3_EXEC_FIX|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 48 trades; +0.291 R/trade", source: "KILLED.md" },
  "COMBO_01|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 2,989 trades; −0.131 R/trade", source: "KILLED.md" },
  "COMBO_01|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 2,483 trades; −0.139 R/trade", source: "KILLED.md" },
  "COMBO_02_CLOSED_HTF|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 940 trades; −0.124 R/trade", source: "KILLED.md" },
  "COMBO_02_CLOSED_HTF|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 856 trades; −0.105 R/trade", source: "KILLED.md" },
  "COMBO_02_LOCAL|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 2,954 trades; −0.127 R/trade", source: "KILLED.md" },
  "COMBO_02_LOCAL|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 2,439 trades; −0.145 R/trade", source: "KILLED.md" },
  "COMBO_02_V2_1B|LONG": { verdict: "NOT RUN", stage: "DISCOVERY", reason: "stopped by the owner (D110): BOS has no edge (D86) and every COMBO_02 side was KILLED at Gate 2", source: "DECISIONS D127 (owner D110)" },
  "COMBO_02_V2_1B|SHORT": { verdict: "NOT RUN", stage: "DISCOVERY", reason: "stopped by the owner (D110): BOS has no edge (D86) and every COMBO_02 side was KILLED at Gate 2", source: "DECISIONS D127 (owner D110)" },
  "COMBO_02_V2_1_A|LONG": { verdict: "NOT RUN", stage: "DISCOVERY", reason: "stopped by the owner (D110): BOS has no edge (D86) and every COMBO_02 side was KILLED at Gate 2", source: "DECISIONS D127 (owner D110)" },
  "COMBO_02_V2_1_A|SHORT": { verdict: "NOT RUN", stage: "DISCOVERY", reason: "stopped by the owner (D110): BOS has no edge (D86) and every COMBO_02 side was KILLED at Gate 2", source: "DECISIONS D127 (owner D110)" },
  "COMBO_02_V2|LONG": { verdict: "NOT RUN", stage: "DISCOVERY", reason: "stopped by the owner (D110): BOS has no edge (D86) and every COMBO_02 side was KILLED at Gate 2; LONG floor-killed twice before the stop", source: "DECISIONS D127 (owner D110)" },
  "COMBO_02_V2|SHORT": { verdict: "NOT RUN", stage: "DISCOVERY", reason: "stopped by the owner (D110): BOS has no edge (D86) and every COMBO_02 side was KILLED at Gate 2", source: "DECISIONS D127 (owner D110)" },
  "COMBO_02_VP_LOC|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 815 trades; −0.119 R/trade", source: "KILLED.md" },
  "COMBO_02_VP_TGT|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 826 trades; −0.153 R/trade", source: "KILLED.md" },
  "COMBO_02|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 940 trades; −0.134 R/trade", source: "KILLED.md" },
  "COMBO_02|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 856 trades; −0.105 R/trade", source: "KILLED.md" },
  "COMBO_03+PBFIX|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 998 trades; −0.099 R/trade", source: "KILLED.md" },
  "COMBO_03+PBFIX|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 723 trades; −0.044 R/trade", source: "KILLED.md" },
  "COMBO_03_TRANSITION_B|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 170 trades; −0.182 R/trade", source: "KILLED.md" },
  "COMBO_03_TRANSITION_B|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 557 trades; −0.054 R/trade", source: "KILLED.md" },
  "COMBO_03_TRANSITION_C|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 6 trades; +0.041 R/trade", source: "KILLED.md" },
  "COMBO_03_TRANSITION_C|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 4 trades; −0.396 R/trade", source: "KILLED.md" },
  "COMBO_03_TRANSITION|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 427 trades; −0.218 R/trade", source: "KILLED.md" },
  "COMBO_03_TRANSITION|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 334 trades; −0.018 R/trade", source: "KILLED.md" },
  "COMBO_04+PBFIX|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 181 trades; +0.046 R/trade", source: "KILLED.md" },
  "COMBO_04+PBFIX|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 82 trades; −0.083 R/trade", source: "KILLED.md" },
  "COMBO_04_HTF+PBFIX|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 31 trades; −0.079 R/trade", source: "KILLED.md" },
  "COMBO_05+PBFIX|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 181 trades; +0.046 R/trade", source: "KILLED.md" },
  "COMBO_05+PBFIX|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 82 trades; −0.083 R/trade", source: "KILLED.md" },
  "COMBO_06+PBFIX|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 743 trades; −0.088 R/trade", source: "KILLED.md" },
  "COMBO_06+PBFIX|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 523 trades; +0.018 R/trade", source: "KILLED.md" },
  "COMBO_07+PBFIX|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 161 trades; +0.027 R/trade", source: "KILLED.md" },
  "COMBO_07+PBFIX|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 70 trades; −0.060 R/trade", source: "KILLED.md" },
  "COMBO_08+PBFIX|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 161 trades; +0.027 R/trade", source: "KILLED.md" },
  "COMBO_08+PBFIX|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 70 trades; −0.060 R/trade", source: "KILLED.md" },
  "COMBO_SD_A|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 37 trades; −0.181 R/trade", source: "KILLED.md" },
  "COMBO_SD_A|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 1 trade; +0.423 R/trade", source: "KILLED.md" },
  "COMBO_SD_B|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 6 trades; −0.483 R/trade", source: "KILLED.md" },
  "COMBO_SD_B|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 0 trades", source: "KILLED.md" },
  "COMBO_SD_C12|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 5 trades; −0.363 R/trade", source: "KILLED.md" },
  "COMBO_SD_C12|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 0 trades", source: "KILLED.md" },
  "COMBO_SD_C20|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 2 trades; −1.033 R/trade", source: "KILLED.md" },
  "COMBO_SD_C20|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 0 trades", source: "KILLED.md" },
  "COMBO_SD_C_FRESH|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 0 trades", source: "KILLED.md" },
  "COMBO_SD_C_FRESH|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 0 trades", source: "KILLED.md" },
  "COMBO_SD_C|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 5 trades; −0.363 R/trade", source: "KILLED.md" },
  "COMBO_SD_C|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 0 trades", source: "KILLED.md" },
  "COMBO_SD_D|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 4 trades; +0.455 R/trade", source: "KILLED.md" },
  "COMBO_SD_D|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 0 trades", source: "KILLED.md" },
  "COMBO_SD_E|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 10 trades; −0.036 R/trade", source: "KILLED.md" },
  "COMBO_SD_E|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 1 trade; +0.423 R/trade", source: "KILLED.md" },
  "COMBO_SD_F|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 44 trades; +0.011 R/trade", source: "KILLED.md" },
  "COMBO_SD_F|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 2 trades; +0.492 R/trade", source: "KILLED.md" },
  "COMBO_SD_LIQ|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 6 trades; −0.483 R/trade", source: "KILLED.md" },
  "COMBO_SD_LIQ|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 0 trades", source: "KILLED.md" },
  "DAILY_TREND_01_SMA200|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 51 trades; +1.005 R/trade", source: "KILLED.md" },
  "DAILY_TREND_02_EMA200|BOTH": { verdict: "KILLED", stage: "VALIDATION", reason: "family F2 representative ROC90 KILLED at VALIDATION Gate 3; EMA200 falls with it (E21, D122)", source: "DECISIONS D122" },
  "DAILY_TREND_03_SMA50|BOTH": { verdict: "KILLED", stage: "VALIDATION", reason: "family F3 representative ICHIMOKU KILLED at VALIDATION Gate 3; SMA50 falls with it (E21, D122)", source: "DECISIONS D122" },
  "DAILY_TREND_04_MA_SLOPE|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 37 trades; +1.469 R/trade", source: "KILLED.md" },
  "DAILY_TREND_05_GOLDEN_CROSS|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 14 trades; +1.740 R/trade", source: "KILLED.md" },
  "DAILY_TREND_06_WEINSTEIN|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 40 trades; +0.851 R/trade", source: "KILLED.md" },
  "DAILY_TREND_09_DONCHIAN20_WEINSTEIN|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 20 trades; +1.272 R/trade", source: "KILLED.md" },
  "DAILY_TREND_11_ADX_DMI|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 83 trades; +0.268 R/trade", source: "KILLED.md" },
  "DAILY_TREND_12_ICHIMOKU|BOTH": { verdict: "KILLED", stage: "VALIDATION", reason: "Gate 2, E9; 75 trades; +0.127 R/trade", source: "KILLED.md" },
  "DAILY_TREND_14_ROC90|BOTH": { verdict: "KILLED", stage: "VALIDATION", reason: "Gate 2, E9; 40 trades; +0.046 R/trade", source: "KILLED.md" },
  "DAILY_TREND_15_ROC30|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 153 trades; +0.326 R/trade", source: "KILLED.md" },
  "DAILY_TREND_19_COMBINED|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 1: under 30 trades, E11; 19 trades; +1.222 R/trade", source: "KILLED.md" },
  "DAILY_TREND_ROTATION|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 110 trades; −0.008 R/trade", source: "KILLED.md" },
  "DAILY_TREND_STATIC12|BOTH": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 327 trades; −0.006 R/trade", source: "KILLED.md" },
  "HCOMBO_T4_F1F3|SHORT": { verdict: "KILLED", stage: "VALIDATION", reason: "Gate 3: PF 0.86, own-twin pct 2.0 < p90, 27% coins positive, 2x slip -0.123, ex-2-best -115.1 R; D188; 127 other combos were not candidates at DISCOVERY; no re-search on this data (E31); 1,117 trades; −0.090 R/trade", source: "KILLED.md", family: "H-COMBO" },
  "LARGE_CAP_SWEEP_CHOCH|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 996 trades; +0.052 R/trade", source: "KILLED.md" },
  "MID_CAP_FVG_DISCOUNT|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 2,243 trades; −0.081 R/trade", source: "KILLED.md" },
  "MTF_4H_16_BREAKOUT252_F1X|LONG": { verdict: "KILLED", stage: "VALIDATION", reason: "Gate 3, E22/E23: net R/trade < 0, PF 0.75, baseline pct 0.325, 24% coins positive, 2x slip < 0, ex-2-best < 0; no variant on the same data (D105d); 169 trades; −0.206 R/trade", source: "KILLED.md", family: "H-F1X" },
  "MTF_4H_16_BREAKOUT252|LONG": { verdict: "INCONCLUSIVE", stage: "VALIDATION", reason: "owner D96: 13 VALIDATION trades, +0.977 R/trade, under the 30-trade line; no holdout; its F1X follow-up was KILLED at VALIDATION Gate 3 (D126); FWD-1 continues", source: "DECISIONS D122, D124 (owner D96)" },
  "OSE_A_corrected_baseline|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 5,845 trades; −0.038 R/trade", source: "KILLED.md" },
  "OSE_A_corrected_baseline|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 5,770 trades; −0.021 R/trade", source: "KILLED.md" },
  "OSE_B_acceptance|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 4,636 trades; −0.002 R/trade", source: "KILLED.md" },
  "OSE_B_acceptance|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 4,624 trades; −0.024 R/trade", source: "KILLED.md" },
  "OSE_C_retest|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 2,808 trades; −0.020 R/trade", source: "KILLED.md" },
  "OSE_C_retest|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 2,691 trades; −0.051 R/trade", source: "KILLED.md" },
  "OSE_D_faster_confirmation|LONG": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 6,779 trades; −0.043 R/trade", source: "KILLED.md" },
  "OSE_D_faster_confirmation|SHORT": { verdict: "KILLED", stage: "DISCOVERY", reason: "Gate 2, E9; 6,646 trades; −0.024 R/trade", source: "KILLED.md" },
  "XS_MOM|LONG_SHORT": { verdict: "KILLED", stage: "VALIDATION-not-reached", reason: "DISCOVERY: fails 2x costs -2.91%, max DD 30.5% > 25%, ex-2-best -0.19; no variant on this data (E24); 100 active weeks; +10.95% net", source: "KILLED.md", family: "XS-MOM" },
  "N1_BTC_RANGE_GRID|NON_DIRECTIONAL": { verdict: "KILLED", stage: "VALIDATION-not-reached", reason: "DISCOVERY: net < 0, max DD 16.1% > 15%, fees doubled -21.7%; D159; no variant on this data; -15.9% net", source: "KILLED.md", family: "N1" },
  "N2_BTC_FUNDING_CARRY|NON_DIRECTIONAL": { verdict: "INCONCLUSIVE", stage: "VALIDATION", reason: "E25 sample rule: 0 completed carry holds, 3 required (BTC funding never averaged above the 0.01% entry level); DISCOVERY passed (D167); no holdout run; D172", source: "FINAL_VERDICT.md", family: "N2" },
  "HD2_FUNDING_EXTREMES|LONG_SHORT": { verdict: "INCONCLUSIVE", stage: "DISCOVERY", reason: "E24 sample rule: 22 active weeks, 26 required; every other DISCOVERY rule passed; no VALIDATION look (E24/E30); D168", source: "FINAL_VERDICT.md", family: "HD-2" },
  "HR1_SWEEP_CHOCH_30M|BOTH": { verdict: "KILLED", stage: "VALIDATION-not-reached", reason: "DISCOVERY Gate 2: PF 0.91, random pct 71.5 < p90, 42% coins positive, 2x slip < 0, ex-2-best < 0; D181; no variant on this data; 1,417 trades; −0.045 R/trade", source: "KILLED.md", family: "HR-1" },
  "HD1_TAKER_FLOW|LONG_SHORT": { verdict: "KILLED", stage: "VALIDATION-not-reached", reason: "DISCOVERY: random pct 71 < p90, 2x costs -2.7%, max DD 30.0% > 25%, ex-2-best -25.9%; D182; no variant on this data; 97 active weeks; +5.2% net", source: "KILLED.md", family: "HD-1" },
  "INTRADAY_BOS_SD_V1|LONG": { verdict: "NOT RUN", stage: "DISCOVERY", reason: "UNTESTABLE: no 1m data before 2026-07-15 (D7)", source: "SCORECARD.csv (DESIGN_FLAWS.md)" },
  "INTRADAY_BOS_SD_V1|SHORT": { verdict: "NOT RUN", stage: "DISCOVERY", reason: "UNTESTABLE: no 1m data before 2026-07-15 (D7)", source: "SCORECARD.csv (DESIGN_FLAWS.md)" },
  "SMALL_CAP_VOLUME_BOS|LONG": { verdict: "NOT RUN", stage: "DISCOVERY", reason: "UNTESTABLE: no small-cap coin in the 27", source: "SCORECARD.csv (DESIGN_FLAWS.md)" },
};

/**
 * The later studies in FINAL_VERDICT / KILLED.md order (owner D191: N1/N2/HD-2/HR-1/HD-1/H-COMBO,
 * H-F1X, XS-MOM). The Strategies page lists these first.
 */
export const LATER_STUDY_KEYS: readonly string[] = [
  "N1_BTC_RANGE_GRID|NON_DIRECTIONAL",
  "N2_BTC_FUNDING_CARRY|NON_DIRECTIONAL",
  "HD2_FUNDING_EXTREMES|LONG_SHORT",
  "HR1_SWEEP_CHOCH_30M|BOTH",
  "HD1_TAKER_FLOW|LONG_SHORT",
  "HCOMBO_T4_F1F3|SHORT",
  "MTF_4H_16_BREAKOUT252_F1X|LONG",
  "XS_MOM|LONG_SHORT",
];

/** Panel ids that research knows under another spelling. */
const RESEARCH_ALIASES: Readonly<Record<string, string>> = {
  // DECISIONS D26: COMBO_02_V2_1A is the duplicate code id of COMBO_02_V2_1_A.
  COMBO_02_V2_1A: "COMBO_02_V2_1_A",
};

/**
 * The app's COMBO_03..08 and COMBO_04_HTF still carry the pullback bug (0 trades,
 * BACKTEST_UI_GAPS.md family B). Research only tested the pullback-fixed copies.
 */
const PULLBACK_FIXED_IN_RESEARCH = new Set([
  "COMBO_03",
  "COMBO_04",
  "COMBO_05",
  "COMBO_06",
  "COMBO_07",
  "COMBO_08",
  "COMBO_04_HTF",
]);

/** Ids flagged by the owner's ruling D3 (DECISIONS D26): the backtest reads the forming 4h bar. */
const LOOKAHEAD_D3_IDS = new Set(["COMBO_02_V2_1_A", "COMBO_02_V2_1A", "COMBO_02_V2_1B"]);

export type ResearchLookup = {
  key: string;
  entry: ResearchVerdict;
  note: string | null;
};

export function researchVerdictFor(combinationId: string, direction: string): ResearchLookup | null {
  const id = RESEARCH_ALIASES[combinationId] ?? combinationId;
  const side = direction.toUpperCase();
  if (PULLBACK_FIXED_IN_RESEARCH.has(id)) {
    const key = `${id}+PBFIX|${side}`;
    const entry = SCORECARD_SNAPSHOT[key];
    return entry ? { key, entry, note: `research tested ${id}+PBFIX (pullback fix); this copy has the pullback bug` } : null;
  }
  const key = `${id}|${side}`;
  const entry = SCORECARD_SNAPSHOT[key];
  if (entry) return { key, entry, note: null };
  // "INTRADAY_BOS_SD_V1.C" is variant C of INTRADAY_BOS_SD_V1; research has one row for the book.
  const base = id.split(".")[0];
  if (base !== id) {
    const baseKey = `${base}|${side}`;
    const baseEntry = SCORECARD_SNAPSHOT[baseKey];
    if (baseEntry) return { key: baseKey, entry: baseEntry, note: `research verdict for ${base} (all variants)` };
  }
  return null;
}

export function hasBacktestLookaheadD3(combinationId: string): boolean {
  return LOOKAHEAD_D3_IDS.has(combinationId);
}
