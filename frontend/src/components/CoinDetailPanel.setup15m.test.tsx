/** @vitest-environment jsdom */
// Owner D201 / D234: the Setup tab shows the 15m research-only note for a 15m setup, and only then.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ScreenerRow } from "../types/market";
import { SETUP_15M_NOTE, SetupTab } from "./CoinDetailPanel";

const NOTE = "15m: research-only; tight stops make costs a large share of R; no tested edge.";

function tab(timeframe: string) {
  return {
    analysis: { timeframe, status: "WAITING" },
    trend: {},
    structure: {},
    setup_state: { status: "WAITING" },
    trade_plan: {},
  } as Record<string, unknown>;
}

afterEach(cleanup);

describe("Setup tab 15m note (D234)", () => {
  it("is the exact owner text", () => {
    expect(SETUP_15M_NOTE).toBe(NOTE);
  });

  it("shows on a 15m setup", () => {
    render(<SetupTab payload={tab("15m")} row={{} as ScreenerRow} />);
    expect(screen.getByTestId("setup-15m-note").textContent).toBe(NOTE);
  });

  it("is hidden on a 1h setup", () => {
    render(<SetupTab payload={tab("1h")} row={{} as ScreenerRow} />);
    expect(screen.queryByTestId("setup-15m-note")).toBeNull();
    expect(screen.queryByText(NOTE)).toBeNull();
  });

  it("shows the invalid-plan line instead of a wrong-side stop", () => {
    const payload = tab("15m");
    payload.plan_notice = "invalid plan: stop on wrong side";
    payload.trade_plan = {
      entry: { entry_price: 1216.25, direction: "SHORT" },
      stop: { final_stop: 1209.684 },
      targets: [{ name: "TP1", target_price: 1198.29 }],
    };
    render(<SetupTab payload={payload} row={{} as ScreenerRow} />);
    expect(screen.getByTestId("invalid-plan-notice").textContent).toBe(
      "invalid plan: stop on wrong side",
    );
    expect(screen.queryByText(/1209\.684/)).toBeNull();
    expect(screen.queryByText(/1216\.25/)).toBeNull();
  });
});
