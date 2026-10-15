/**
 * @vitest-environment jsdom
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMarketStore } from "../store/marketStore";

vi.mock("../api/client", () => ({
  fetchFuturesScreener: vi.fn(async () => ({
    rows: [],
    total: 0,
    ingestion: "ok",
  })),
  wsUrl: (path: string) => `ws://test${path}`,
}));

import { fetchFuturesScreener } from "../api/client";
import { useMarketStream } from "./useMarketStream";

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  url: string;
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  private closeListeners: Array<() => void> = [];

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(_data: string) {}

  addEventListener(type: string, fn: () => void) {
    if (type === "close") this.closeListeners.push(fn);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  close() {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
    for (const fn of this.closeListeners) fn();
  }
}

function sockets(path: string) {
  return FakeWebSocket.instances.filter((ws) => ws.url.endsWith(path));
}

function row(symbol: string) {
  return {
    rows: [{ symbol } as never],
    total: 1,
    ingestion: "ok",
  };
}

describe("useMarketStream sockets and screener REST", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    useMarketStore.setState({
      rows: {},
      orderedSymbols: [],
      total: 0,
      ingestion: "starting",
      connected: false,
      search: "",
      preset: null,
      domain: "Futures",
      domainLoading: false,
      screenSize: 100,
      screenFilter: "ALL_ELIGIBLE",
      screenMeta: null,
    });
    vi.mocked(fetchFuturesScreener).mockReset();
    vi.mocked(fetchFuturesScreener).mockResolvedValue({
      rows: [],
      total: 0,
      ingestion: "ok",
    } as never);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("does not reconnect market or screener sockets after unmount", async () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useMarketStream());
    expect(sockets("/ws/market")).toHaveLength(1);
    expect(sockets("/ws/screener")).toHaveLength(1);

    unmount();
    expect(sockets("/ws/market")[0].readyState).toBe(FakeWebSocket.CLOSED);
    expect(sockets("/ws/screener")[0].readyState).toBe(FakeWebSocket.CLOSED);

    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(sockets("/ws/market")).toHaveLength(1);
    expect(sockets("/ws/screener")).toHaveLength(1);
    expect(useMarketStore.getState().connected).toBe(false);
  });

  it("keeps the nav connected flag on the market socket and ignores a stale close", async () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useMarketStream());
    const market = sockets("/ws/market")[0];
    const screener = sockets("/ws/screener")[0];

    act(() => {
      market.open();
      screener.open();
    });
    expect(useMarketStore.getState().connected).toBe(true);

    act(() => {
      screener.close();
    });
    expect(useMarketStore.getState().connected).toBe(true);
    expect(market.readyState).toBe(FakeWebSocket.OPEN);

    await act(async () => {
      vi.advanceTimersByTime(1_500);
    });
    expect(sockets("/ws/screener")).toHaveLength(2);
    expect(sockets("/ws/market")).toHaveLength(1);
    expect(useMarketStore.getState().connected).toBe(true);

    act(() => {
      market.close();
    });
    expect(useMarketStore.getState().connected).toBe(false);

    await act(async () => {
      vi.advanceTimersByTime(1_500);
    });
    const replacement = sockets("/ws/market")[1];
    expect(replacement).toBeTruthy();
    act(() => {
      replacement.open();
    });
    expect(useMarketStore.getState().connected).toBe(true);

    const staleClose = market.onclose;
    act(() => {
      staleClose?.();
    });
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(sockets("/ws/market")).toHaveLength(2);
    expect(useMarketStore.getState().connected).toBe(true);
    expect(replacement.readyState).toBe(FakeWebSocket.OPEN);
    unmount();
    expect(useMarketStore.getState().connected).toBe(false);
  });

  it("does not let a stale screener response overwrite a newer one", async () => {
    let resolveFirst: (value: unknown) => void = () => {};
    let resolveSecond: (value: unknown) => void = () => {};
    vi.mocked(fetchFuturesScreener)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }) as ReturnType<typeof fetchFuturesScreener>,
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecond = resolve;
          }) as ReturnType<typeof fetchFuturesScreener>,
      );

    const { unmount } = renderHook(() => useMarketStream());
    await act(async () => {
      useMarketStore.getState().setSearch("second");
    });

    await act(async () => {
      resolveSecond(row("NEWUSDT"));
    });
    expect(useMarketStore.getState().orderedSymbols).toEqual(["NEWUSDT"]);

    await act(async () => {
      resolveFirst(row("OLDUSDT"));
    });
    expect(useMarketStore.getState().orderedSymbols).toEqual(["NEWUSDT"]);
    expect(useMarketStore.getState().rows.OLDUSDT).toBeUndefined();
    unmount();
  });
});
