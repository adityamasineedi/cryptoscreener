/** Sides that lost money on the 10 Oct large/mid book with at least 5 trades. */

const BLACKLISTED_SIDES = [
  "COMBO_01|1h|LONG",
  "COMBO_01|4h|LONG",
  "COMBO_01|4h|SHORT",
  "COMBO_02_LOCAL|1h|LONG",
  "COMBO_02_LOCAL|4h|LONG",
  "COMBO_02_LOCAL|4h|SHORT",
  "COMBO_02|1h|LONG",
  "COMBO_02|4h|LONG",
  "COMBO_02|4h|SHORT",
  "COMBO_02_CLOSED_HTF|1h|LONG",
  "COMBO_02_CLOSED_HTF|4h|LONG",
  "COMBO_02_CLOSED_HTF|4h|SHORT",
  "COMBO_02_V2|1h|LONG",
  "COMBO_02_V2|4h|LONG",
  "COMBO_02_V2|4h|SHORT",
  "COMBO_02_V2_1B|1h|LONG",
  "COMBO_02_V2_1B|4h|LONG",
  "COMBO_02_V2_1B|4h|SHORT",
  "COMBO_02_V2_1_A|1h|LONG",
  "COMBO_02_V2_1_A|4h|LONG",
  "COMBO_02_V2_1_A|4h|SHORT",
  "COMBO_03_TRANSITION|1h|LONG",
  "COMBO_03_TRANSITION_B|4h|LONG",
] as const;

const BLACKLIST = new Set<string>(BLACKLISTED_SIDES);

export function isBlacklistedStrategySide(
  combinationId: string,
  timeframe: string,
  direction: string,
): boolean {
  return BLACKLIST.has(`${combinationId}|${timeframe}|${direction}`);
}
