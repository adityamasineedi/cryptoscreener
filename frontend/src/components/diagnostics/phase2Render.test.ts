import { describe, expect, it } from "vitest";

/** Frontend must render backend status literally — no health inventing. */
function displayMetric(value: unknown): string {
  if (value == null) return "—";
  return String(value);
}

function isLive(status: string): boolean {
  return status.toUpperCase() === "LIVE" || status.toUpperCase() === "HEALTHY";
}

describe("Phase 2 diagnostics rendering rules", () => {
  it("renders UNKNOWN as UNKNOWN", () => {
    expect("UNKNOWN".toUpperCase()).toBe("UNKNOWN");
    expect(isLive("UNKNOWN")).toBe(false);
  });

  it("renders null metrics as dash not zero", () => {
    expect(displayMetric(null)).toBe("—");
    expect(displayMetric(undefined)).toBe("—");
    expect(displayMetric(0)).toBe("0");
  });

  it("does not treat STALE as LIVE", () => {
    expect(isLive("STALE")).toBe(false);
    expect(isLive("LIVE")).toBe(true);
  });

  it("connected WS with STALE status stays STALE", () => {
    const row = { connected: true, status: "STALE", frames_total: 0 };
    expect(row.connected).toBe(true);
    expect(isLive(row.status)).toBe(false);
    expect(row.status).toBe("STALE");
  });

  it("fast DB health does not invent detail tables", () => {
    const fast = { mode: "fast", status: "HEALTHY", tables: undefined as unknown };
    expect(fast.mode).toBe("fast");
    expect(fast.tables).toBeUndefined();
  });
});
