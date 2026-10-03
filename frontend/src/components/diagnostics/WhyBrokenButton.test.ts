import { describe, expect, it } from "vitest";

/** Pure helpers mirrored from diagnostics status coloring expectations. */
function isFailureStatus(status: string): boolean {
  return ["ERROR", "UNAVAILABLE", "STALE", "WARNING", "DEGRADED", "MISSING", "CRITICAL"].includes(
    status.toUpperCase()
  );
}

describe("WhyBrokenButton status rules", () => {
  it("does not treat WAITING as failure", () => {
    expect(isFailureStatus("WAITING")).toBe(false);
    expect(isFailureStatus("HEALTHY")).toBe(false);
    expect(isFailureStatus("DISABLED")).toBe(false);
  });

  it("treats STALE connected-without-data as failure", () => {
    expect(isFailureStatus("STALE")).toBe(true);
    expect(isFailureStatus("ERROR")).toBe(true);
  });
});
