import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchAlerts, wsUrl, type LiveAlert } from "../api/client";
import { useMarketStore } from "../store/marketStore";

const QUICK_FILTERS = [
  "All",
  "V1 Trades",
  "Research Paper",
  "Structure",
  "Liquidations",
  "Experimental",
] as const;

type QuickFilter = (typeof QUICK_FILTERS)[number];

type BadgeKind =
  | "V1_VERIFIED"
  | "RESEARCH_15M"
  | "STRUCTURE"
  | "LIQUIDATIONS"
  | "EXPERIMENTAL";

function snipOf(a: LiveAlert): Record<string, unknown> {
  const payload = (a.payload || {}) as Record<string, unknown>;
  const snip = (payload.signal_snippet || {}) as Record<string, unknown>;
  return snip && typeof snip === "object" ? snip : {};
}

function pickField(a: LiveAlert, key: string): unknown {
  const snip = snipOf(a);
  const payload = (a.payload || {}) as Record<string, unknown>;
  return (a as Record<string, unknown>)[key] ?? payload[key] ?? snip[key];
}

function classifyBadge(a: LiveAlert): BadgeKind {
  if (a.badge === "V1_VERIFIED" || a.badge === "RESEARCH_15M" || a.badge === "STRUCTURE" || a.badge === "LIQUIDATIONS" || a.badge === "EXPERIMENTAL") {
    return a.badge;
  }
  const t = String(a.type || "").toUpperCase();
  if (t === "LIQ_SPIKE") return "LIQUIDATIONS";
  if (t === "BOS" || t === "CHOCH" || t === "SETUP_STATUS" || t === "MARKET_SIGNAL") {
    return "STRUCTURE";
  }
  if (t === "PAPER_ENTRY" || t === "PAPER_EXIT") {
    const strategyId = String(pickField(a, "strategy_id") || "");
    const source = String(pickField(a, "source") || "");
    const path = String(pickField(a, "path") || "").toUpperCase().replace("PATH_", "");
    if (strategyId === "COMBO_02_V1" && source === "V1_PAPER_WATCHER") {
      return "V1_VERIFIED";
    }
    if (strategyId === "EXPERIMENTAL_PATH_B" || path === "B") {
      return "EXPERIMENTAL";
    }
    return "RESEARCH_15M";
  }
  return "STRUCTURE";
}

function badgeLabel(kind: BadgeKind): string {
  switch (kind) {
    case "V1_VERIFIED":
      return "V1 VERIFIED";
    case "RESEARCH_15M":
      return "RESEARCH 15M";
    case "STRUCTURE":
      return "STRUCTURE";
    case "LIQUIDATIONS":
      return "LIQUIDATIONS";
    case "EXPERIMENTAL":
      return "EXPERIMENTAL";
  }
}

function badgeClass(kind: BadgeKind): string {
  switch (kind) {
    case "V1_VERIFIED":
      return "border-emerald-500/50 bg-emerald-500/15 text-emerald-300";
    case "RESEARCH_15M":
      return "border-sky-500/40 bg-sky-500/10 text-sky-200";
    case "STRUCTURE":
      return "border-terminal-border bg-white/5 text-terminal-muted";
    case "LIQUIDATIONS":
      return "border-rose-500/40 bg-rose-500/10 text-rose-200";
    case "EXPERIMENTAL":
      return "border-amber-500/40 bg-amber-500/10 text-amber-200";
  }
}

function alertSubtitle(a: LiveAlert, kind: BadgeKind): string {
  const snip = snipOf(a);
  if (kind === "V1_VERIFIED") {
    const tier = String(pickField(a, "v1_tier") || snip.v1_tier || "—").toUpperCase();
    const htf = String(pickField(a, "htf_alignment") || snip.htf_alignment || "HTF_ALIGNED").replace(
      /_/g,
      " ",
    );
    return `COMBO_02 v1 • 1h • ${htf} • ${tier}`;
  }
  if (kind === "RESEARCH_15M") {
    const tf = a.timeframe || String(pickField(a, "timeframe") || "15m");
    return `RESEARCH ONLY • ${tf} • Not Telegram eligible`;
  }
  if (kind === "EXPERIMENTAL") {
    return "EXPERIMENTAL • Path B • Not Telegram eligible";
  }
  if (kind === "LIQUIDATIONS") {
    return "Liquidation spike";
  }
  return "Structure / setup";
}

function matchesQuickFilter(a: LiveAlert, filter: QuickFilter): boolean {
  if (filter === "All") return true;
  const kind = classifyBadge(a);
  if (filter === "V1 Trades") return kind === "V1_VERIFIED";
  if (filter === "Research Paper") return kind === "RESEARCH_15M";
  if (filter === "Structure") return kind === "STRUCTURE";
  if (filter === "Liquidations") return kind === "LIQUIDATIONS";
  if (filter === "Experimental") return kind === "EXPERIMENTAL";
  return true;
}

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
  const [quickFilter, setQuickFilter] = useState<QuickFilter>("All");
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
      if (!matchesQuickFilter(r, quickFilter)) return false;
      if (q && !r.symbol.includes(q)) return false;
      return true;
    });
  }, [rows, quickFilter, symbolQuery]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { All: rows.length };
    for (const r of rows) {
      const kind = classifyBadge(r);
      if (kind === "V1_VERIFIED") c["V1 Trades"] = (c["V1 Trades"] || 0) + 1;
      else if (kind === "RESEARCH_15M") c["Research Paper"] = (c["Research Paper"] || 0) + 1;
      else if (kind === "STRUCTURE") c.Structure = (c.Structure || 0) + 1;
      else if (kind === "LIQUIDATIONS") c.Liquidations = (c.Liquidations || 0) + 1;
      else if (kind === "EXPERIMENTAL") c.Experimental = (c.Experimental || 0) + 1;
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
            Live feed with explicit source badges. V1 VERIFIED = COMBO_02 watcher (1h). RESEARCH 15M =
            legacy paper only — not Telegram eligible. Structure / liquidations continue independently.
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

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Buffered" value={String(rows.length)} />
        <Stat label="V1 trades" value={String(counts["V1 Trades"] || 0)} tone={(counts["V1 Trades"] || 0) > 0 ? "up" : undefined} />
        <Stat label="Research paper" value={String(counts["Research Paper"] || 0)} />
        <Stat label="Structure" value={String(counts.Structure || 0)} />
        <Stat label="Liquidations" value={String(counts.Liquidations || 0)} tone={(counts.Liquidations || 0) > 0 ? "down" : undefined} />
        <Stat label="Experimental" value={String(counts.Experimental || 0)} />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1">
          {QUICK_FILTERS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setQuickFilter(t)}
              className={`rounded px-2 py-0.5 text-[10px] uppercase tracking-wide ${
                quickFilter === t
                  ? "bg-white/10 text-terminal-text"
                  : "text-terminal-muted hover:bg-white/5"
              }`}
            >
              {t}
              {t !== "All" && counts[t] ? ` ${counts[t]}` : ""}
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
                  <th className="px-2 py-1.5">Badge</th>
                  <th className="px-2 py-1.5">Sev</th>
                  <th className="px-2 py-1.5">Type</th>
                  <th className="px-2 py-1.5">Symbol</th>
                  <th className="px-2 py-1.5">TF</th>
                  <th className="px-2 py-1.5">Title</th>
                  <th className="px-2 py-1.5">Detail</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((a) => {
                  const kind = classifyBadge(a);
                  const subtitle = alertSubtitle(a, kind);
                  return (
                    <tr
                      key={a.id}
                      className="cursor-pointer border-t border-terminal-border/70 hover:bg-white/[0.03]"
                      onClick={() => openSymbol(a.symbol)}
                    >
                      <td className="whitespace-nowrap px-2 py-1.5 text-terminal-muted">
                        {fmtTime(a.time)}
                      </td>
                      <td className="px-2 py-1.5">
                        <div className="flex flex-col gap-0.5">
                          <span
                            className={`inline-flex w-fit rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wide ${badgeClass(kind)}`}
                          >
                            {badgeLabel(kind)}
                          </span>
                          <span className="max-w-[14rem] truncate text-[9px] text-terminal-muted" title={subtitle}>
                            {subtitle}
                          </span>
                        </div>
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
                  );
                })}
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
