import { useEffect, useRef } from "react";
import { fetchFuturesScreener, wsUrl } from "../api/client";
import { useMarketStore } from "../store/marketStore";
import type { ScreenerRow } from "../types/market";

/** True when REST owns membership (search / cap preset / screen filter). */
function filtersActive(): boolean {
  const st = useMarketStore.getState();
  const filt = (st.screenFilter || "ALL_ELIGIBLE").toUpperCase();
  return Boolean(st.search?.trim()) || Boolean(st.preset) || filt !== "ALL_ELIGIBLE";
}

export function useMarketStream() {
  const setSnapshot = useMarketStore((s) => s.setSnapshot);
  const applyBatch = useMarketStore((s) => s.applyBatch);
  const applyRowPatch = useMarketStore((s) => s.applyRowPatch);
  const setConnected = useMarketStore((s) => s.setConnected);
  const setDomainLoading = useMarketStore((s) => s.setDomainLoading);
  const search = useMarketStore((s) => s.search);
  const preset = useMarketStore((s) => s.preset);
  const domain = useMarketStore((s) => s.domain);
  const screenSize = useMarketStore((s) => s.screenSize);
  const screenFilter = useMarketStore((s) => s.screenFilter);
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
          limit: screenSize,
          screen_filter: screenFilter,
        });
        if (!cancelled) {
          setSnapshot(data.rows, data.total, data.ingestion, {
            total_universe: data.total_universe,
            discovered_universe: data.discovered_universe,
            active_universe: data.active_universe,
            active_universe_cap: data.active_universe_cap,
            eligible_count: data.eligible_count,
            returned_count: data.returned_count,
            limit: data.limit,
            selection_updated_at: data.selection_updated_at,
            excluded: data.excluded,
            search_mode: data.search_mode,
            screen_filter: data.screen_filter,
            screen_timeframe: data.screen_timeframe,
            screener_identity: data.screener_identity,
            v1_watcher_view: data.v1_watcher_view,
          });
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
  }, [search, preset, domain, screenSize, screenFilter, setSnapshot, setDomainLoading]);

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

  // Screener WS: unfiltered snapshots must not wipe search/preset REST membership.
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
            if (filtersActive()) {
              // Keep REST-filtered row list; refresh fields for visible symbols only.
              for (const row of msg.rows as ScreenerRow[]) {
                if (!row?.symbol) continue;
                if (!useMarketStore.getState().rows[row.symbol]) continue;
                applyRowPatch(row.symbol, row);
              }
              return;
            }
            setSnapshot(
              msg.rows as ScreenerRow[],
              msg.total ?? msg.rows.length,
              msg.ingestion ?? "",
              {
                total_universe: msg.total_universe,
                discovered_universe: msg.discovered_universe,
                active_universe: msg.active_universe,
                active_universe_cap: msg.active_universe_cap,
                eligible_count: msg.eligible_count,
                returned_count: msg.returned_count ?? msg.rows.length,
                limit: msg.limit,
                selection_updated_at: msg.selection_updated_at,
                excluded: msg.excluded,
                search_mode: msg.search_mode,
                screen_filter: msg.screen_filter,
                screen_timeframe: msg.screen_timeframe,
                screener_identity: msg.screener_identity,
                v1_watcher_view: msg.v1_watcher_view,
              }
            );
          } else if (msg.type === "row_patch" && msg.symbol && msg.changes) {
            applyRowPatch(msg.symbol as string, msg.changes as Partial<ScreenerRow>);
          } else if (msg.type === "screener_heartbeat") {
            if (filtersActive()) return;
            const cur = useMarketStore.getState().screenMeta;
            if (cur && (msg.total_universe != null || msg.eligible_count != null)) {
              useMarketStore.setState({
                screenMeta: {
                  ...cur,
                  total_universe: Number(msg.total_universe ?? cur.total_universe),
                  discovered_universe: Number(
                    msg.discovered_universe ?? cur.discovered_universe ?? 0
                  ),
                  active_universe: Number(
                    msg.active_universe ?? msg.total_universe ?? cur.active_universe
                  ),
                  eligible_count: Number(msg.eligible_count ?? cur.eligible_count),
                  returned_count: Number(msg.returned_count ?? cur.returned_count),
                },
              });
            }
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
