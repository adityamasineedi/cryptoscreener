import { describe, expect, it } from "vitest";
import { closedTruncationNote, opportunityAction, paperRiskLabel } from "./paperLabels";

const flags = { autoOn: true, watcherOwns: false, legacyAutoOn: false };

describe("opportunityAction (P1, P6 already_open)", () => {
  it("does not say Auto open while legacy auto-entry is off", () => {
    expect(opportunityAction({ tier: "READY", already_open: false }, flags)).toBe(
      "Research only (legacy auto-entry off)",
    );
  });

  it("says Open for a symbol open in any stream", () => {
    // The route sets already_open from every stream (V1, passed book, legacy).
    expect(opportunityAction({ tier: "READY", already_open: true }, flags)).toBe("Open");
  });

  it("says Auto open only when legacy auto-entry and paper auto are both on", () => {
    expect(
      opportunityAction({ tier: "READY", already_open: false }, { ...flags, legacyAutoOn: true }),
    ).toBe("Auto open (15m research)");
    expect(
      opportunityAction(
        { tier: "READY", already_open: false },
        { ...flags, legacyAutoOn: true, autoOn: false },
      ),
    ).toBe("Ready (Auto OFF)");
    expect(
      opportunityAction({ tier: "READY", already_open: false }, { ...flags, watcherOwns: true }),
    ).toBe("Research only");
    expect(opportunityAction({ tier: "NEAR", already_open: false }, flags)).toBe("Watch");
  });
});

describe("paperRiskLabel (P3)", () => {
  it("shows capped dollars and a cap marker after the leverage cap", () => {
    const label = paperRiskLabel(0.02, 9.9, { capped_by_leverage: true, leverage: 2 });
    expect(label).toBe("$9.90 (capped 2×; planned 2%)");
    expect(label).not.toMatch(/^2% \//);
  });

  it("keeps the percent / dollars label when not capped", () => {
    expect(paperRiskLabel(0.02, 20, { capped_by_leverage: false })).toBe("2% / $20.00");
    expect(paperRiskLabel(null, 20, undefined)).toBe("$20.00");
  });
});

describe("closedTruncationNote (P6)", () => {
  it("says the table is truncated when it is full", () => {
    expect(closedTruncationNote(100, 100)).toMatch(/most recent closed trades.*full ledger/);
    expect(closedTruncationNote(12, 100)).toBeNull();
  });
});
