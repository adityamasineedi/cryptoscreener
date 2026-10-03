import { useEffect, useState } from "react";
import {
  createDiagnosticsBackup,
  createDiagnosticsSnapshot,
  exportDiagnosticsSnapshot,
  fetchDiagnosticsBackups,
  fetchDiagnosticsDataHealth,
  fetchDiagnosticsDataHealthDrilldown,
  fetchDiagnosticsDatabase,
  fetchDiagnosticsDatabaseDetail,
  fetchDiagnosticsJobs,
  fetchDiagnosticsLogs,
  fetchDiagnosticsRest,
  fetchDiagnosticsSnapshotAiPackage,
  fetchDiagnosticsSnapshots,
  fetchDiagnosticsWebsocket,
  restoreTestDiagnosticsBackup,
  verifyDiagnosticsBackup,
} from "../../api/client";
import { DiagStatusBadge } from "./DiagStatusBadge";

function fmt(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  const s = String(v);
  return s || "—";
}

function fmtBytes(n: unknown): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function PanelHeader({
  title,
  status,
  reason,
  latency,
}: {
  title: string;
  status?: string;
  reason?: string;
  latency?: unknown;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <div className="font-display text-sm font-semibold">{title}</div>
      {status ? <DiagStatusBadge status={status} /> : null}
      {reason ? <span className="text-[11px] text-terminal-muted">{reason}</span> : null}
      {latency != null ? (
        <span className="font-mono text-[10px] text-terminal-muted">
          endpoint {fmt(typeof latency === "object" && latency && "last_ms" in latency ? (latency as { last_ms: number }).last_ms : latency)}ms
        </span>
      ) : null}
    </div>
  );
}

function DenseTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: Array<Array<unknown>>;
}) {
  return (
    <div className="overflow-auto rounded border border-terminal-border/80">
      <table className="w-full min-w-[640px] border-collapse text-left text-[11px]">
        <thead className="bg-terminal-panel/80 text-[10px] uppercase tracking-wide text-terminal-muted">
          <tr>
            {columns.map((c) => (
              <th key={c} className="border-b border-terminal-border px-2 py-2 font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-terminal-border/40 hover:bg-white/[0.03]">
              {row.map((cell, j) => (
                <td key={j} className="px-2 py-1.5 font-mono text-[10px] text-terminal-text/90">
                  {typeof cell === "string" &&
                  ["HEALTHY", "ERROR", "WARNING", "STALE", "LIVE", "WAITING", "UNKNOWN", "UNAVAILABLE", "DEGRADED", "MISSING", "DISABLED", "CRITICAL", "FAILED", "RUNNING", "COMPLETED", "QUEUED", "BLOCKED"].includes(
                    cell.toUpperCase()
                  ) ? (
                    <DiagStatusBadge status={cell} />
                  ) : (
                    fmt(cell)
                  )}
                </td>
              ))}
            </tr>
          ))}
          {!rows.length ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-4 text-center text-terminal-muted">
                No measured rows
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

export function DatabaseTab() {
  const [fast, setFast] = useState<Record<string, unknown> | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function loadFast() {
      try {
        const d = await fetchDiagnosticsDatabase();
        if (alive) setFast(d);
      } catch {
        /* keep */
      }
    }
    void loadFast();
    // Fast health only — never auto-poll expensive detail
    const id = window.setInterval(() => void loadFast(), 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    async function loadDetailCached() {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const d = await fetchDiagnosticsDatabaseDetail();
        if (alive) setDetail(d);
      } catch (e) {
        if (alive) setDetailError(e instanceof Error ? e.message : "detail_failed");
      } finally {
        if (alive) setDetailLoading(false);
      }
    }
    void loadDetailCached();
    return () => {
      alive = false;
    };
  }, []);

  async function refreshDetail() {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const d = await fetchDiagnosticsDatabaseDetail({ refresh: true });
      setDetail(d);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : "detail_failed");
    } finally {
      setDetailLoading(false);
    }
  }

  if (!fast) return <p className="text-xs text-terminal-muted">Loading database health…</p>;

  const pool = (fast.pool || {}) as Record<string, unknown>;
  const conn = (fast.connections || {}) as Record<string, unknown>;
  const fastTimings = (fast.timings || {}) as Record<string, unknown>;
  const queries = (detail?.queries || {}) as Record<string, unknown>;
  const tables = (detail?.tables || []) as Array<Record<string, unknown>>;
  const longRunning = (queries.long_running || []) as Array<Record<string, unknown>>;
  const blocked = (queries.blocked || []) as Array<Record<string, unknown>>;
  const slowest = (detail?.slowest_queries || []) as Array<Record<string, unknown>>;
  const timeline = (detail?.timeline || {}) as Record<string, unknown>;
  const detailTimings = (detail?.timings || {}) as Record<string, unknown>;

  return (
    <div className="space-y-5">
      <section className="space-y-3">
        <PanelHeader
          title="Fast Health"
          status={String(fast.status || "UNKNOWN")}
          reason={String(fast.reason || "")}
          latency={fast.endpoint_latency}
        />
        <div className="grid gap-3 md:grid-cols-3 font-mono text-[11px]">
          <div className="rounded border border-terminal-border/80 p-3 space-y-1">
            <div className="text-[10px] uppercase text-terminal-muted">Connection</div>
            <div>db: {fmt(fast.database_name)}</div>
            <div>latency: {fmt(fast.latency_ms)}ms</div>
            <div>size: {fmtBytes(fast.database_size_bytes)}</div>
            <div>active: {fmt(conn.active)} idle: {fmt(conn.idle)}</div>
            <div>waiting: {fmt(conn.waiting)} max: {fmt(conn.max_connections)}</div>
            <div>ungranted_locks: {fmt(fast.lock_count)}</div>
          </div>
          <div className="rounded border border-terminal-border/80 p-3 space-y-1">
            <div className="text-[10px] uppercase text-terminal-muted">Pool</div>
            <DiagStatusBadge status={String(pool.status || "UNKNOWN")} />
            <div>size: {fmt(pool.pool_size)}</div>
            <div>checked_out: {fmt(pool.checked_out)}</div>
            <div>utilization: {fmt(pool.utilization_pct)}%</div>
            <div className="text-terminal-muted">{fmt(pool.reason)}</div>
          </div>
          <div className="rounded border border-terminal-border/80 p-3 space-y-1">
            <div className="text-[10px] uppercase text-terminal-muted">Fast timings</div>
            <div>total: {fmt(fast.collection_duration_ms)}ms</div>
            <div>ping: {fmt(fastTimings.connection_check_ms)}ms</div>
            <div>size: {fmt(fastTimings.database_size_ms)}ms</div>
            <div>connections: {fmt(fastTimings.connections_ms)}ms</div>
            <div>locks: {fmt(fastTimings.locks_ms)}ms</div>
            <div>pool: {fmt(fastTimings.pool_ms)}ms</div>
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <PanelHeader
            title="Detailed Forensics"
            status={detail ? String(detail.status || "UNKNOWN") : "WAITING"}
            reason={
              detail
                ? `${detail.cached === true ? "cached" : "fresh"} · ${fmt(detail.reason)}`
                : detailLoading
                  ? "collecting…"
                  : "not loaded"
            }
            latency={detail?.endpoint_latency}
          />
          <button
            type="button"
            className="rounded border border-terminal-border px-2 py-1 font-mono text-[10px] text-terminal-text hover:bg-white/[0.04] disabled:opacity-50"
            onClick={() => void refreshDetail()}
            disabled={detailLoading}
          >
            {detailLoading ? "Refreshing…" : "Refresh Detailed Diagnostics"}
          </button>
        </div>
        {detailError ? (
          <p className="font-mono text-[11px] text-terminal-danger">{detailError}</p>
        ) : null}
        {detail ? (
          <>
            <div className="grid gap-3 md:grid-cols-3 font-mono text-[11px]">
              <div className="rounded border border-terminal-border/80 p-3 space-y-1">
                <div className="text-[10px] uppercase text-terminal-muted">Cache</div>
                <div>cached: {fmt(detail.cached)}</div>
                <div>collected_at: {fmt(detail.collected_at)}</div>
                <div>cache_age: {fmt(detail.cache_age_seconds)}s</div>
                <div>ttl: {fmt(detail.cache_ttl_seconds)}s</div>
                <div>collection: {fmt(detail.collection_duration_ms)}ms</div>
              </div>
              <div className="rounded border border-terminal-border/80 p-3 space-y-1">
                <div className="text-[10px] uppercase text-terminal-muted">Timeline</div>
                <div>started: {fmt(timeline.collection_started)}</div>
                <div>completed: {fmt(timeline.collection_completed)}</div>
                <div>duration: {fmt(timeline.duration_ms)}ms</div>
                <DiagStatusBadge status={String(queries.status || "UNKNOWN")} />
                <div>long_running: {longRunning.length}</div>
                <div>blocked: {blocked.length}</div>
              </div>
              <div className="rounded border border-terminal-border/80 p-3 space-y-1">
                <div className="text-[10px] uppercase text-terminal-muted">Slowest queries</div>
                {slowest.length ? (
                  slowest.slice(0, 5).map((s) => (
                    <div key={String(s.name)}>
                      {fmt(s.name)}: {fmt(s.duration_ms)}ms
                    </div>
                  ))
                ) : (
                  <div className="text-terminal-muted">—</div>
                )}
                <div className="pt-1 text-terminal-muted">
                  table_sizes: {fmt(detailTimings.table_sizes_ms)}ms
                </div>
              </div>
            </div>
            {longRunning.length ? (
              <>
                <div className="text-[10px] uppercase text-terminal-muted">Long-running queries</div>
                <DenseTable
                  columns={["PID", "Age s", "State", "Wait", "Query"]}
                  rows={longRunning.map((q) => [
                    q.pid,
                    q.age_seconds,
                    q.state,
                    q.wait_event,
                    q.query,
                  ])}
                />
              </>
            ) : null}
            {blocked.length ? (
              <>
                <div className="text-[10px] uppercase text-terminal-muted">Blocked queries</div>
                <DenseTable
                  columns={["Blocked PID", "Blocking PID", "Blocked query"]}
                  rows={blocked.map((q) => [q.blocked_pid, q.blocking_pid, q.blocked_query])}
                />
              </>
            ) : null}
            <div className="text-[10px] uppercase text-terminal-muted">Largest / watched tables</div>
            <DenseTable
              columns={["Table", "Total", "Data", "Indexes", "Live rows", "Dead", "Last vacuum"]}
              rows={tables.map((t) => [
                t.table,
                fmtBytes(t.total_bytes),
                fmtBytes(t.table_bytes),
                fmtBytes(t.index_bytes),
                t.live_rows_est,
                t.dead_tuples,
                t.last_autovacuum || t.last_vacuum,
              ])}
            />
          </>
        ) : (
          <p className="text-xs text-terminal-muted">
            {detailLoading ? "Collecting detailed forensics (may take several seconds)…" : "No detail yet"}
          </p>
        )}
      </section>
    </div>
  );
}

export function WebsocketTab() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const d = await fetchDiagnosticsWebsocket();
        if (alive) setData(d);
      } catch {
        /* keep */
      }
    }
    void load();
    const id = window.setInterval(() => void load(), 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);
  if (!data) return <p className="text-xs text-terminal-muted">Loading WebSocket forensics…</p>;
  const conns = (data.connections || []) as Array<Record<string, unknown>>;
  const byType = (data.by_stream_type || {}) as Record<string, Record<string, unknown>>;

  return (
    <div className="space-y-4">
      <PanelHeader
        title="WebSocket"
        status={String(data.status || "UNKNOWN")}
        reason={String(data.reason || "")}
        latency={data.endpoint_latency}
      />
      <p className="text-[11px] text-terminal-muted">
        Connected does not mean healthy. Stale frames → STALE.
      </p>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {Object.entries(byType).map(([k, v]) => (
          <div key={k} className="rounded border border-terminal-border/80 p-3 font-mono text-[10px]">
            <div className="mb-1 flex items-center justify-between">
              <span className="uppercase text-terminal-muted">{k}</span>
              <DiagStatusBadge status={String(v.status || "UNKNOWN")} />
            </div>
            <div>connections: {fmt(v.connections ?? v.connected_count)}</div>
            <div>expected streams: {fmt(v.expected_streams)}</div>
            <div>active subs: {fmt(v.active_subscriptions)}</div>
            <div>last frame: {fmt(v.last_frame_at || v.last_event_at)}</div>
            <div>parse errors: {fmt(v.parse_errors)}</div>
          </div>
        ))}
      </div>
      <DenseTable
        columns={[
          "ID",
          "Type",
          "Status",
          "Connected",
          "Frames",
          "Subs",
          "Expected",
          "Stale s",
          "Last frame",
          "Error",
        ]}
        rows={conns.map((c) => [
          c.connection_id,
          c.stream_type,
          c.status,
          c.connected,
          c.frames_total ?? c.messages_total,
          c.subscription_count,
          c.expected_subscription_count,
          c.stale_seconds,
          c.last_frame_at,
          c.last_error || c.reason,
        ])}
      />
    </div>
  );
}

export function RestTab() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const d = await fetchDiagnosticsRest();
        if (alive) setData(d);
      } catch {
        /* keep */
      }
    }
    void load();
    const id = window.setInterval(() => void load(), 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);
  if (!data) return <p className="text-xs text-terminal-muted">Loading REST forensics…</p>;
  const providers = (data.providers || []) as Array<Record<string, unknown>>;
  const endpoints = (data.endpoints || []) as Array<Record<string, unknown>>;

  return (
    <div className="space-y-4">
      <PanelHeader
        title="REST / Providers"
        status={String(data.status || "UNKNOWN")}
        reason={String(data.reason || "")}
        latency={data.endpoint_latency}
      />
      <p className="text-[11px] text-terminal-muted">
        Window: {fmt(data.window_seconds)}s · null metrics are uninstrumented (not zero)
      </p>
      <DenseTable
        columns={[
          "Provider",
          "Status",
          "Requests",
          "Success",
          "Fail",
          "429",
          "5xx",
          "Retries",
          "Avg ms",
          "P95 ms",
          "Last success",
        ]}
        rows={providers.map((p) => [
          p.provider,
          p.status,
          p.request_count,
          p.success_count,
          p.failure_count,
          p.count_429,
          p.count_5xx,
          p.retry_count,
          p.avg_latency_ms,
          p.p95_latency_ms,
          p.last_success,
        ])}
      />
      <div className="text-[10px] uppercase text-terminal-muted">Endpoints (audit window)</div>
      <DenseTable
        columns={["Provider", "Endpoint", "Req", "429", "5xx", "Avg ms", "P95"]}
        rows={endpoints.slice(0, 40).map((e) => [
          e.provider,
          e.endpoint,
          e.request_count,
          e.count_429,
          e.count_5xx,
          e.avg_latency_ms,
          e.p95_latency_ms,
        ])}
      />
    </div>
  );
}

export function DataHealthTab() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [tf, setTf] = useState("15m");
  const [drill, setDrill] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const d = await fetchDiagnosticsDataHealth();
        if (alive) setData(d);
      } catch {
        /* keep */
      }
    }
    void load();
    const id = window.setInterval(() => void load(), 10000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const datasets = (data?.datasets || []) as Array<Record<string, unknown>>;

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Data Health"
        status={String(data?.status || "UNKNOWN")}
        reason={String(data?.reason || "")}
        latency={data?.endpoint_latency}
      />
      <DenseTable
        columns={[
          "Dataset",
          "Status",
          "Provider",
          "Universe",
          "Available",
          "Fresh",
          "Stale",
          "Missing",
          "Unavailable",
          "Coverage %",
          "Note",
        ]}
        rows={datasets.map((d) => [
          d.dataset,
          d.status,
          d.provider,
          d.universe_size,
          d.available_count,
          d.fresh_count,
          d.stale_count,
          d.missing_count,
          d.unavailable_count,
          d.coverage_percent,
          d.note,
        ])}
      />
      <div className="rounded border border-terminal-border/80 p-3">
        <div className="mb-2 text-[10px] uppercase text-terminal-muted">
          Symbol / timeframe drilldown (explicit — may scan gaps)
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-[11px] text-terminal-muted">
            Symbol
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className="mt-1 block rounded border border-terminal-border bg-terminal-bg px-2 py-1 font-mono text-xs"
            />
          </label>
          <label className="text-[11px] text-terminal-muted">
            Timeframe
            <select
              value={tf}
              onChange={(e) => setTf(e.target.value)}
              className="mt-1 block rounded border border-terminal-border bg-terminal-bg px-2 py-1 font-mono text-xs"
            >
              {["1m", "5m", "15m", "1h", "4h", "1d"].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy}
            className="rounded border border-terminal-accent/40 bg-terminal-accent/10 px-3 py-1.5 text-[11px] text-terminal-accent"
            onClick={async () => {
              setBusy(true);
              try {
                const d = await fetchDiagnosticsDataHealthDrilldown({
                  dataset: "ohlcv",
                  symbol,
                  timeframe: tf,
                });
                setDrill(d);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Scanning…" : "Run drilldown"}
          </button>
        </div>
        {drill ? (
          <div className="mt-3 grid gap-1 font-mono text-[10px] sm:grid-cols-2">
            <div>status: <DiagStatusBadge status={String(drill.status || "UNKNOWN")} /></div>
            <div>reason: {fmt(drill.reason)}</div>
            <div>earliest: {fmt(drill.earliest_timestamp)}</div>
            <div>latest: {fmt(drill.latest_timestamp)}</div>
            <div>candles: {fmt(drill.candle_count)}</div>
            <div>gaps: {fmt(drill.gap_count)}</div>
            <div>duplicates: {fmt(drill.duplicate_count)}</div>
            <div>invalid: {fmt(drill.invalid_count)}</div>
            <div>freshness_s: {fmt(drill.freshness_seconds)}</div>
            <div>table: {fmt(drill.storage_table)}</div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function ResearchJobsTab() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const d = await fetchDiagnosticsJobs();
        if (alive) setData(d);
      } catch {
        /* keep */
      }
    }
    void load();
    const id = window.setInterval(() => void load(), 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);
  if (!data) return <p className="text-xs text-terminal-muted">Loading research jobs…</p>;
  const jobs = (data.jobs || []) as Array<Record<string, unknown>>;
  const locks = (data.locks || []) as Array<Record<string, unknown>>;

  return (
    <div className="space-y-4">
      <PanelHeader
        title="Research Jobs"
        status={String(data.status || "UNKNOWN")}
        reason={String(data.reason || "")}
        latency={data.endpoint_latency}
      />
      <DenseTable
        columns={[
          "Job ID",
          "Type",
          "Status",
          "Stage",
          "Symbol",
          "TF",
          "Started",
          "Updated",
          "Worker",
          "Errors",
          "Error",
        ]}
        rows={jobs.map((j) => [
          j.job_id || j.run_id,
          j.job_type,
          j.status,
          j.stage,
          j.symbol,
          j.timeframe,
          j.started_at,
          j.updated_at,
          j.worker,
          j.error_count,
          j.error,
        ])}
      />
      <div className="text-[10px] uppercase text-terminal-muted">
        Sync locks (diagnostics never deletes locks)
      </div>
      <DenseTable
        columns={[
          "Lock key",
          "Op",
          "Symbol",
          "TF",
          "Owner",
          "Run",
          "Status",
          "Heartbeat",
          "Age s",
          "Stale",
        ]}
        rows={locks.map((l) => [
          l.lock_key,
          l.operation,
          l.symbol,
          l.timeframe,
          l.owner,
          l.run_id,
          l.status,
          l.heartbeat,
          l.age_seconds,
          l.stale ? "STALE" : "OK",
        ])}
      />
    </div>
  );
}

export function LogsTab() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [filters, setFilters] = useState({
    severity: "",
    component: "",
    diagnostic_id: "",
    text: "",
  });

  async function load() {
    const d = await fetchDiagnosticsLogs({
      severity: filters.severity || undefined,
      component: filters.component || undefined,
      diagnostic_id: filters.diagnostic_id || undefined,
      text: filters.text || undefined,
      limit: 100,
    });
    setData(d);
  }

  useEffect(() => {
    void load().catch(() => undefined);
    // manual / modest refresh
    const id = window.setInterval(() => void load().catch(() => undefined), 15000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const entries = (data?.entries || []) as Array<Record<string, unknown>>;

  return (
    <div className="space-y-3">
      <PanelHeader
        title="Logs"
        status="HEALTHY"
        reason={String(data?.reason || "Bounded diagnostics log buffer")}
        latency={data?.endpoint_latency}
      />
      <div className="flex flex-wrap gap-2">
        {(
          [
            ["severity", "Severity"],
            ["component", "Component"],
            ["diagnostic_id", "Diagnostic ID"],
            ["text", "Text"],
          ] as const
        ).map(([k, label]) => (
          <label key={k} className="text-[11px] text-terminal-muted">
            {label}
            <input
              value={filters[k]}
              onChange={(e) => setFilters((s) => ({ ...s, [k]: e.target.value }))}
              className="mt-1 block rounded border border-terminal-border bg-terminal-bg px-2 py-1 font-mono text-xs"
            />
          </label>
        ))}
        <button
          type="button"
          className="self-end rounded border border-terminal-border px-3 py-1.5 text-[11px]"
          onClick={() => void load()}
        >
          Search
        </button>
      </div>
      <div className="text-[11px] text-terminal-muted">
        matched {fmt(data?.total_matched)} · buffer {fmt(data?.buffer_size)}/{fmt(data?.buffer_capacity)}
      </div>
      <DenseTable
        columns={["Time", "Sev", "Service", "Component", "Diag ID", "Symbol", "Message"]}
        rows={entries.map((e) => [
          e.timestamp,
          e.severity,
          e.service,
          e.component,
          e.diagnostic_id,
          e.symbol,
          e.message,
        ])}
      />
    </div>
  );
}

export function BackupsTab() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [snapshots, setSnapshots] = useState<Record<string, unknown> | null>(null);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function reload() {
    try {
      const [b, s] = await Promise.all([
        fetchDiagnosticsBackups(),
        fetchDiagnosticsSnapshots(),
      ]);
      setData(b);
      setSnapshots(s);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "load_failed");
    }
  }

  useEffect(() => {
    void reload();
    const id = window.setInterval(() => void reload(), 10000);
    return () => window.clearInterval(id);
  }, []);

  async function create(type: string) {
    setBusy(`create:${type}`);
    setMsg(null);
    try {
      const r = await createDiagnosticsBackup(type);
      setMsg(`Started ${type}: ${fmt(r.backup_id)} (${fmt(r.status)})`);
      await reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "create_failed");
    } finally {
      setBusy(null);
    }
  }

  async function verify(id: string) {
    setBusy(`verify:${id}`);
    try {
      const r = await verifyDiagnosticsBackup(id);
      setMsg(`Verify ${id}: ${fmt(r.status)}`);
      await reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "verify_failed");
    } finally {
      setBusy(null);
    }
  }

  async function restoreTest(id: string) {
    setBusy(`restore:${id}`);
    try {
      const r = await restoreTestDiagnosticsBackup(id);
      setMsg(`Restore test ${id}: ${fmt(r.status)} — ${fmt(r.reason)}`);
      await reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "restore_test_failed");
    } finally {
      setBusy(null);
    }
  }

  async function takeSnapshot() {
    setBusy("snapshot");
    try {
      const r = await createDiagnosticsSnapshot("manual");
      setMsg(`Snapshot ${fmt(r.snapshot_id)} status=${fmt(r.status)}`);
      await reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "snapshot_failed");
    } finally {
      setBusy(null);
    }
  }

  if (!data) return <p className="text-xs text-terminal-muted">Loading backups…</p>;

  const summary = (data.summary || {}) as Record<string, unknown>;
  const retention = (data.retention || {}) as Record<string, unknown>;
  const disk = (data.disk || {}) as Record<string, unknown>;
  const dest = (data.destination || {}) as Record<string, unknown>;
  const restoreCap = (data.restore_capability || {}) as Record<string, unknown>;
  const backups = (data.backups || []) as Array<Record<string, unknown>>;
  const snapRows = ((snapshots?.snapshots || []) as Array<Record<string, unknown>>);
  const lastOk = (summary.last_successful_backup || null) as Record<string, unknown> | null;
  const types = (data.types_available || {}) as Record<string, unknown>;

  return (
    <div className="space-y-5">
      <PanelHeader
        title="Backups"
        status={String(data.status || "UNKNOWN")}
        reason={String(data.reason || "")}
      />
      {msg ? <p className="font-mono text-[11px] text-terminal-muted">{msg}</p> : null}

      <div className="grid gap-3 md:grid-cols-3 font-mono text-[11px]">
        <div className="rounded border border-terminal-border/80 p-3 space-y-1">
          <div className="text-[10px] uppercase text-terminal-muted">Summary</div>
          <div>last success: {fmt(lastOk?.backup_id)}</div>
          <div>type: {fmt(lastOk?.backup_type)}</div>
          <div>checksum: {fmt(lastOk?.verification_status)}</div>
          <div>restore test: {fmt(lastOk?.restore_test_status)}</div>
          <div>destination: {fmt(dest.path)}</div>
          <div>free: {fmt(disk.available_gb)} GB (margin {fmt(disk.safety_margin_gb)})</div>
        </div>
        <div className="rounded border border-terminal-border/80 p-3 space-y-1">
          <div className="text-[10px] uppercase text-terminal-muted">Retention (visibility only)</div>
          <div>configured_days: {fmt(retention.configured_days)}</div>
          <div>auto_delete: {fmt(retention.auto_delete)} (Phase 3: never)</div>
          <div>count: {fmt(retention.backup_count)}</div>
          <div>oldest: {fmt(retention.oldest)}</div>
          <div>newest: {fmt(retention.newest)}</div>
          <div>total_size: {fmtBytes(retention.total_size_bytes)}</div>
        </div>
        <div className="rounded border border-terminal-border/80 p-3 space-y-1">
          <div className="text-[10px] uppercase text-terminal-muted">Restore readiness</div>
          <DiagStatusBadge status={String(restoreCap.status || "UNKNOWN")} />
          <div className="text-terminal-muted whitespace-pre-wrap">{fmt(restoreCap.reason)}</div>
          <div>pg_dump: {fmt(restoreCap.pg_dump)}</div>
          <div>pg_restore: {fmt(restoreCap.pg_restore)}</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {(["CONFIG", "DIAGNOSTICS", "RESEARCH", "DATABASE", "FULL"] as const).map((t) => (
          <button
            key={t}
            type="button"
            disabled={busy != null || types[t] === false}
            className="rounded border border-terminal-border px-2 py-1 font-mono text-[10px] disabled:opacity-40"
            onClick={() => void create(t)}
            title={types[t] === false ? "Unavailable in this environment" : `Create ${t} backup`}
          >
            {busy === `create:${t}` ? "…" : `Create ${t}`}
          </button>
        ))}
        <button
          type="button"
          disabled={busy != null}
          className="rounded border border-terminal-border px-2 py-1 font-mono text-[10px]"
          onClick={() => void takeSnapshot()}
        >
          {busy === "snapshot" ? "…" : "Create System Snapshot"}
        </button>
        <button
          type="button"
          className="rounded border border-terminal-border px-2 py-1 font-mono text-[10px]"
          onClick={() => void reload()}
        >
          Refresh
        </button>
      </div>

      <div className="text-[10px] uppercase text-terminal-muted">Backup runs</div>
      <DenseTable
        columns={[
          "Backup ID",
          "Type",
          "Created",
          "Size",
          "Checksum",
          "Verification",
          "Restore test",
          "Health",
          "Status",
          "Actions",
        ]}
        rows={backups.map((b) => [
          b.backup_id,
          b.backup_type,
          b.started_at,
          fmtBytes(b.size_bytes),
          typeof b.checksum === "string" ? `${b.checksum.slice(0, 12)}…` : "—",
          b.verification_status,
          b.restore_test_status,
          b.health_status || "UNKNOWN",
          b.status,
          " ",
        ])}
      />
      <div className="space-y-2">
        {backups.map((b) => (
          <div key={String(b.backup_id)} className="flex flex-wrap items-center gap-2 font-mono text-[10px]">
            <span className="text-terminal-muted">{fmt(b.backup_id)}</span>
            <button
              type="button"
              className="rounded border border-terminal-border px-2 py-0.5"
              onClick={() => setSelected(b)}
            >
              View Details
            </button>
            <button
              type="button"
              className="rounded border border-terminal-border px-2 py-0.5"
              disabled={busy != null}
              onClick={() => void verify(String(b.backup_id))}
            >
              Verify
            </button>
            <button
              type="button"
              className="rounded border border-terminal-border px-2 py-0.5"
              disabled={busy != null}
              onClick={() => void restoreTest(String(b.backup_id))}
            >
              Restore Test
            </button>
          </div>
        ))}
      </div>

      {selected ? (
        <div className="rounded border border-terminal-border/80 p-3 font-mono text-[11px] space-y-1">
          <div className="text-[10px] uppercase text-terminal-muted">Backup details</div>
          <div>id: {fmt(selected.backup_id)}</div>
          <div>type: {fmt(selected.backup_type)}</div>
          <div>status: {fmt(selected.status)} / health: {fmt(selected.health_status)}</div>
          <div>reason: {fmt(selected.health_reason)}</div>
          <div>created: {fmt(selected.started_at)}</div>
          <div>duration_ms: {fmt(selected.duration_ms)}</div>
          <div>size: {fmtBytes(selected.size_bytes)}</div>
          <div>destination: {fmt(selected.destination)}</div>
          <div>sha256: {fmt(selected.checksum)}</div>
          <div>verification: {fmt(selected.verification_status)}</div>
          <div>validation: {fmt(selected.validation_status)}</div>
          <div>restore_test: {fmt(selected.restore_test_status)}</div>
          <div>tool: {fmt(selected.tool)} {fmt(selected.tool_version)}</div>
          <div>git: {fmt(selected.git_commit)}</div>
          <div>run_id: {fmt(selected.run_id)}</div>
          <div>error: {fmt(selected.error)}</div>
          <button
            type="button"
            className="mt-2 rounded border border-terminal-border px-2 py-0.5"
            onClick={() => setSelected(null)}
          >
            Close
          </button>
        </div>
      ) : null}

      <div className="text-[10px] uppercase text-terminal-muted">System snapshots</div>
      <DenseTable
        columns={["Snapshot ID", "Created", "Status", "Size", "Checksum", "Git", "Actions"]}
        rows={snapRows.map((s) => [
          s.snapshot_id,
          s.created_at,
          s.status,
          fmtBytes(s.size_bytes),
          typeof s.checksum === "string" ? `${String(s.checksum).slice(0, 12)}…` : "—",
          s.git_commit,
          " ",
        ])}
      />
      <div className="space-y-1">
        {snapRows.map((s) => (
          <div key={String(s.snapshot_id)} className="flex gap-2 font-mono text-[10px]">
            <span className="text-terminal-muted">{fmt(s.snapshot_id)}</span>
            <button
              type="button"
              className="rounded border border-terminal-border px-2 py-0.5"
              onClick={() =>
                void fetchDiagnosticsSnapshotAiPackage(String(s.snapshot_id)).then((p) =>
                  setMsg(`AI package ready (${fmt((p.markdown as string | undefined)?.length)} chars)`)
                )
              }
            >
              AI Package
            </button>
            <button
              type="button"
              className="rounded border border-terminal-border px-2 py-0.5"
              onClick={() =>
                void exportDiagnosticsSnapshot(String(s.snapshot_id)).then((p) =>
                  setMsg(`Bundle: ${fmt(p.bundle_path)}`)
                )
              }
            >
              Export Bundle
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
