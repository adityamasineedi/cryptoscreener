import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchAlerts, wsUrl, type LiveAlert } from "../api/client";
import { useMarketStore } from "../store/marketStore";

const TYPE_FILTERS = [
  "ALL",
  "BOS",
  "SETUP_STATUS",
  "MARKET_SIGNAL",
  "PAPER_ENTRY",
  "PAPER_EXIT",
  "LIQ_SPIKE",
] as const;

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function mergeAlerts(prev: LiveAlert[], incoming: LiveAlert[]): LiveAlert[] {
  const byId = new Map<string, LiveAlert>();
  for (const a of [...incoming, ...prev]) {
    if (!a?.id) continue;
    if (!byId.has(a.id)) byId.set(a.id, a);
  }
  return Array.from(byId.values())
    .sort((a, b) => (b.seq || 0) - (a.seq || 0))
    .slice(0, 300);
}

export function AlertsPanel() {
  const [rows, setRows] = useState<LiveAlert[]>([]);
  const [typeFilter, setTypeFilter] = useState<string>("ALL");
  const [symbolQuery, setSymbolQuery] = useState("");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [latestSeq, setLatestSeq] = useState(0);
  const navigate = useNavigate();
  const setSelected = useMarketStore((s) => s.setSelected);
  const latestSeqRef = useRef(0);

  const applySnapshot = useCallback((list: LiveAlert[], seq?: number) => {
    setRows((prev) => mergeAlerts(prev, list));
    if (seq != null) {
      latestSeqRef.current = Math.max(latestSeqRef.current, seq);
      setLatestSeq(latestSeqRef.current);
    } else if (list.length) {
      const maxSeq = Math.max(...list.map((a) => a.seq || 0));
      latestSeqRef.current = Math.max(latestSeqRef.current, maxSeq);
      setLatestSeq(latestSeqRef.current);
    }
  }, []);

  const pollOnce = useCallback(async () => {
    try {
      const data = await fetchAlerts({
        limit: 120,
        since_seq: latestSeqRef.current > 0 ? latestSeqRef.current : undefined,
      });
      applySnapshot(data.rows || [], data.latest_seq);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load alerts");
    }
  }, [applySnapshot]);

  // REST load + incremental poll (backup even when WS is up)
  useEffect(() => {
    let alive = true;
    async function tick() {
      if (!alive) return;
      await pollOnce();
    }
    tick();
    const id = window.setInterval(tick, 4000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [pollOnce]);

  // Live WebSocket push
  useEffect(() => {
    let stopped = false;
    let retry = 0;
    let timer: number | undefined;
    let ws: WebSocket | null = null;

    function connect() {
      if (stopped) return;
      ws = new WebSocket(wsUrl("/ws/alerts"));
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
        setError(null);
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string) as {
            type?: string;
            rows?: LiveAlert[];
            alert?: LiveAlert;
            latest_seq?: number;
          };
          if (msg.type === "alerts_snapshot") {
            applySnapshot(msg.rows || [], msg.latest_seq);
          } else if (msg.type === "alert" && msg.alert) {
            applySnapshot([msg.alert], msg.alert.seq);
          } else if (msg.type === "alerts_heartbeat" && msg.latest_seq != null) {
            setLatestSeq(msg.latest_seq);
          }
        } catch {
          /* ignore bad frames */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (stopped) return;
        const delay = Math.min(15000, 800 * 2 ** retry);
        retry += 1;
        timer = window.setTimeout(connect, delay);
      };
      ws.onerror = () => {
        setConnected(false);
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
      };
    }

    connect();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
  }, [applySnapshot]);

  const filtered = useMemo(() => {
    const q = symbolQuery.trim().toUpperCase();
    return rows.filter((r) => {
      if (typeFilter !== "ALL" && r.type !== typeFilter) return false;
      if (q && !r.symbol.includes(q)) return false;
      return true;
    });
  }, [rows, typeFilter, symbolQuery]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: rows.length };
    for (const r of rows) {
      c[r.type] = (c[r.type] || 0) + 1;
    }
    return c;
  }, [rows]);

  function openSymbol(symbol: string) {
    setSelected(symbol);
    navigate("/");
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-4 text-terminal-text">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-terminal-border pb-3">
        <div>
          <h1 className="font-display text-lg tracking-tight">Alerts</h1>
          <p className="mt-1 max-w-xl text-[11px] text-terminal-muted">
            Live feed of BOS confirmations, setup status changes, market-signal flips, paper
            entries/exits, and liquidation spikes. Transition-only — no mock events.
          </p>
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          <span
            className={`rounded border px-2 py-1 uppercase tracking-wide ${
              connected
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-terminal-border text-terminal-muted"
            }`}
          >
            {connected ? "Live WS" : "Polling"}
          </span>
          <span className="font-mono text-terminal-muted">seq {latestSeq || "—"}</span>
        </div>
      </div>

      {error ? (
        <div className="rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-[12px] text-rose-200">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        <Stat label="Buffered" value={String(rows.length)} />
        <Stat label="BOS" value={String(counts.BOS || 0)} tone={(counts.BOS || 0) > 0 ? "up" : undefined} />
        <Stat label="Setup" value={String(counts.SETUP_STATUS || 0)} />
        <Stat label="Market" value={String(counts.MARKET_SIGNAL || 0)} />
        <Stat label="Paper in" value={String(counts.PAPER_ENTRY || 0)} tone={(counts.PAPER_ENTRY || 0) > 0 ? "up" : undefined} />
        <Stat label="Paper out" value={String(counts.PAPER_EXIT || 0)} />
        <Stat label="Liq spike" value={String(counts.LIQ_SPIKE || 0)} tone={(counts.LIQ_SPIKE || 0) > 0 ? "down" : undefined} />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1">
          {TYPE_FILTERS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTypeFilter(t)}
              className={`rounded px-2 py-0.5 text-[10px] uppercase tracking-wide ${
                typeFilter === t
                  ? "bg-white/10 text-terminal-text"
                  : "text-terminal-muted hover:bg-white/5"
              }`}
            >
              {t}
              {t !== "ALL" && counts[t] ? ` ${counts[t]}` : ""}
            </button>
          ))}
        </div>
        <input
          value={symbolQuery}
          onChange={(e) => setSymbolQuery(e.target.value)}
          placeholder="Filter symbol"
          className="w-40 rounded border border-terminal-border bg-black/20 px-2 py-1 font-mono text-[11px] text-terminal-text outline-none focus:border-white/20"
        />
      </div>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Feed — {filtered.length} shown
        </h2>
        {!filtered.length ? (
          <div className="rounded border border-dashed border-terminal-border px-3 py-8 text-center text-[12px] text-terminal-muted">
            No alerts yet. Keep Screener / Paper running — new BOS, entry candidates, paper fills,
            and liq spikes appear here live.
          </div>
        ) : (
          <div className="overflow-x-auto rounded border border-terminal-border">
            <table className="min-w-full text-left font-mono text-[11px]">
              <thead className="bg-black/20 text-[10px] uppercase tracking-wide text-terminal-muted">
                <tr>
                  <th className="px-2 py-1.5">Time</th>
                  <th className="px-2 py-1.5">Sev</th>
                  <th className="px-2 py-1.5">Type</th>
                  <th className="px-2 py-1.5">Symbol</th>
                  <th className="px-2 py-1.5">TF</th>
                  <th className="px-2 py-1.5">Title</th>
                  <th className="px-2 py-1.5">Detail</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((a) => (
                  <tr
                    key={a.id}
                    className="cursor-pointer border-t border-terminal-border/70 hover:bg-white/[0.03]"
                    onClick={() => openSymbol(a.symbol)}
                  >
                    <td className="whitespace-nowrap px-2 py-1.5 text-terminal-muted">
                      {fmtTime(a.time)}
                    </td>
                    <td className={`px-2 py-1.5 ${sevClass(a.severity)}`}>{a.severity}</td>
                    <td className="px-2 py-1.5">{a.type}</td>
                    <td className="px-2 py-1.5 text-terminal-text">{a.symbol}</td>
                    <td className="px-2 py-1.5">{a.timeframe || "—"}</td>
                    <td className="px-2 py-1.5 font-semibold">{a.title}</td>
                    <td
                      className="max-w-[18rem] truncate px-2 py-1.5 text-terminal-muted"
                      title={a.detail}
                    >
                      {a.detail || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  const color =
    tone === "up" ? "text-emerald-300" : tone === "down" ? "text-rose-300" : "text-terminal-text";
  return (
    <div className="rounded border border-terminal-border bg-terminal-panel/60 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-terminal-muted">{label}</div>
      <div className={`mt-1 font-mono text-sm ${color}`}>{value}</div>
    </div>
  );
}

function sevClass(sev: string): string {
  if (sev === "action") return "text-emerald-300";
  if (sev === "watch") return "text-amber-300";
  return "text-terminal-muted";
}
