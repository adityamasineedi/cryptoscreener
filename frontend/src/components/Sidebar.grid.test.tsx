/**
 * @vitest-environment jsdom
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

vi.mock("../gridTrading/client", () => ({
  fetchGridStatus: vi.fn(),
}));

import { fetchGridStatus } from "../gridTrading/client";

const status = vi.mocked(fetchGridStatus);

function renderSidebar() {
  return render(
    <MemoryRouter>
      <Sidebar collapsed={false} onToggle={() => undefined} />
    </MemoryRouter>,
  );
}

describe("Sidebar grid tab", () => {
  beforeEach(() => {
    status.mockReset();
  });

  it("stays hidden when grid trading is off", async () => {
    status.mockResolvedValue(null);
    renderSidebar();
    expect(await screen.findByText("Screener")).toBeInTheDocument();
    await waitFor(() => expect(status).toHaveBeenCalled());
    expect(screen.queryByText("Grid Trading")).not.toBeInTheDocument();
  });

  it("shows Grid Trading only after the status flag is on", async () => {
    status.mockResolvedValue({
      enabled: true,
      market: "binance_usdt_m_futures",
      mode: "backtest",
      live: false,
      paper: false,
    });
    renderSidebar();
    expect(await screen.findByText("Grid Trading")).toBeInTheDocument();
  });
});
