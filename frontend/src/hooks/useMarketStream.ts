import { useEffect, useRef } from "react";
import { fetchFuturesScreener, wsUrl } from "../api/client";
import { useMarketStore } from "../store/marketStore";
import type { ScreenerRow } from "../types/market";

export function useMarketStream() {
  const setSnapshot = useMarketStore((s) => s.setSnapshot);
  const applyBatch = useMarketStore((s) => s.applyBatch);
  const applyRowPatch = useMarketStore((s) => s.applyRowPatch);
  const setConnected = useMarketStore((s) => s.setConnected);
  const setDomainLoading = useMarketStore((s) => s.setDomainLoading);
  const search = useMarketStore((s) => s.search);
  const preset = useMarketStore((s) => s.preset);
  const domain = useMarketStore((s) => s.domain);
  const marketWsRef = useRef<WebSocket | null>(null);
  const screenerWsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;

    const sortBy =
      domain === "Fundamentals"
        ? "market_cap"
        : domain === "Open Interest"
          ? "open_interest"
          : domain === "Volume Analysis"
            ? "relative_volume"
            : domain === "Market Structure" || domain === "Futures"
              ? "buy_opportunity"
              : "quote_volume_24h";

    async function load() {
      setDomainLoading(true);
      try {
        const data = await fetchFuturesScreener({
          search: search || undefined,
          preset: preset || undefined,
          sort_by: sortBy,
          limit: 300,
        });
        if (!cancelled) {
          setSnapshot(data.rows, data.total, data.ingestion);
        }
      } catch {
        if (!cancelled) {
          setSnapshot([], 0, "api_unavailable");
        }
      } finally {
        if (!cancelled) setDomainLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [search, preset, domain, setSnapshot, setDomainLoading]);

  // Market batch stream (price/funding ticks)
  useEffect(() => {
    let stopped = false;
    let retry = 0;
    let timer: number | undefined;

    function connect() {
      if (stopped) return;
      const ws = new WebSocket(wsUrl("/ws/market"));
      marketWsRef.current = ws;

      ws.onopen = () => {
        retry = 0;
        setConnected(true);
        const ping = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 15000);
        ws.addEventListener("close", () => window.clearInterval(ping));
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === "market_batch" && Array.isArray(msg.updates)) {
            applyBatch(msg.updates);
          }
        } catch {
          /* ignore */
        }
      };

      ws.onclose = () => {
        setConnected(false);
        const delay = Math.min(30000, 1000 * 2 ** retry + Math.random() * 400);
        retry += 1;
        timer = window.setTimeout(connect, delay);
      };

      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      marketWsRef.current?.close();
    };
  }, [applyBatch, setConnected]);

  // Screener incremental patches
  useEffect(() => {
    let stopped = false;
    let retry = 0;
    let timer: number | undefined;

    function connect() {
      if (stopped) return;
      const ws = new WebSocket(wsUrl("/ws/screener"));
      screenerWsRef.current = ws;

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === "screener_snapshot" && Array.isArray(msg.rows)) {
            setSnapshot(msg.rows as ScreenerRow[], msg.total ?? msg.rows.length, msg.ingestion ?? "");
          } else if (msg.type === "row_patch" && msg.symbol && msg.changes) {
            applyRowPatch(msg.symbol as string, msg.changes as Partial<ScreenerRow>);
          }
        } catch {
          /* ignore */
        }
      };

      ws.onclose = () => {
        const delay = Math.min(30000, 1000 * 2 ** retry + Math.random() * 400);
        retry += 1;
        timer = window.setTimeout(connect, delay);
      };

      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      screenerWsRef.current?.close();
    };
  }, [applyRowPatch, setSnapshot]);
}
