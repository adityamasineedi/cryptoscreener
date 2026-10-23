/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BANNER } from "./contextChecklist";
import { ContextChecklist } from "./ContextChecklist";

const MISSING = {
  banner: BANNER,
  symbol: "SYNUSDT",
  timeframe: "1h",
  counts: { "aligned with a long": 0, "aligned with a short": 0, "mixed/neutral": 7 },
  rows: [
    { category: "Structure", state: "no structure", alignment: "mixed/neutral" },
    { category: "Supply/demand", state: "no zone", alignment: "mixed/neutral" },
    { category: "Volume", state: "no volume average", alignment: "mixed/neutral" },
    { category: "Futures", state: "funding: no data", alignment: "mixed/neutral" },
    { category: "Open interest", state: "no OI history", alignment: "mixed/neutral" },
    { category: "Liquidations", state: "no 30-day liquidation history yet", alignment: "mixed/neutral" },
    { category: "Higher timeframe", state: "4h no closed-bar EMA; 1d no closed-bar EMA", alignment: "mixed/neutral" },
  ],
};

afterEach(cleanup);

describe("context checklist", () => {
  it("shows the banner, the counts, and the missing-data texts", () => {
    render(<ContextChecklist data={MISSING} />);
    expect(screen.getByTestId("context-banner").textContent).toBe(BANNER);
    expect(screen.getByText(/aligned with a long 0/)).toBeTruthy();
    expect(screen.getByText(/aligned with a short 0/)).toBeTruthy();
    expect(screen.getByText(/mixed\/neutral 7/)).toBeTruthy();
    expect(screen.getByText("no OI history")).toBeTruthy();
    expect(screen.getByText("no 30-day liquidation history yet")).toBeTruthy();
    const blob = document.body.textContent || "";
    expect(blob.toLowerCase().includes("buy")).toBe(false);
    expect(blob.toLowerCase().includes("sell")).toBe(false);
  });
});
