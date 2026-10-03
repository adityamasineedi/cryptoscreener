import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  acknowledgeDiagnosticsIssue,
  fetchDiagnosticsAiPackage,
  fetchDiagnosticsGit,
  fetchDiagnosticsIssue,
  fetchDiagnosticsIssues,
  fetchDiagnosticsOverview,
  fetchDiagnosticsResources,
  postDiagnosticsAiHandoff,
  resolveDiagnosticsIssue,
} from "../../api/client";
import { DiagStatusBadge } from "./DiagStatusBadge";
import {
  BackupsTab,
  DatabaseTab,
  DataHealthTab,
  LogsTab,
  ResearchJobsTab,
  RestTab,
  WebsocketTab,
} from "./Phase2Tabs";
import type { DiagnosticCard, DiagnosticIssue, DiagnosticsOverview } from "./types";

const TABS = [
  "Overview",
  "Issues",
  "Services",
  "Data Health",
  "Database",
  "WebSocket",
  "REST/API",
  "Research",
  "Resources",
  "Backups",
  "Logs",
  "AI Fix Package",
] as const;

type Tab = (typeof TABS)[number];

function tabFromPath(pathname: string, searchTab: string | null): Tab {
  if (pathname.includes("/diagnostics/issues")) return "Issues";
  if (searchTab) {
    const hit = TABS.find((t) => t.toLowerCase().replace(/[\s/]+/g, "-") === searchTab);
    if (hit) return hit;
  }
  return "Overview";
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

function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

async function copyText(text: string) {
  await navigator.clipboard.writeText(text);
}

function PhaseStub({ title, phase, reason }: { title: string; phase: number; reason: string }) {
  return (
    <div className="rounded border border-terminal-border/80 p-4">
      <div className="font-display text-sm font-semibold">{title}</div>
      <p className="mt-2 text-xs text-terminal-muted">
        Phase {phase} — {reason}
      </p>
      <p className="mt-1 text-[11px] text-terminal-muted">
        Status is UNKNOWN/UNAVAILABLE — never marked healthy without evidence.
      </p>
    </div>
  );
}

function OverviewTab({
  overview,
}: {
  overview: DiagnosticsOverview | null;
}) {
  if (!overview) {
    return <p className="text-xs text-terminal-muted">Loading overview…</p>;
  }
  const cards = overview.cards || [];
  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="font-display text-lg font-semibold tracking-tight">SYSTEM STATUS</div>
        <DiagStatusBadge status={String(overview.system_status)} />
        <span className="text-[11px] text-terminal-muted">{overview.reason}</span>
      </div>
      <div className="mt-1 text-[10px] text-terminal-muted">
        Last checked: {overview.last_checked || "—"} · UI poll{" "}
        {overview.polling?.ui_refresh_seconds ?? 5}s · expensive metrics{" "}
        {overview.polling?.expensive_metrics_seconds ?? 30}s
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {cards.map((c) => (
          <HealthCard key={c.key} card={c} />
        ))}
      </div>
    </div>
  );
}

function HealthCard({ card }: { card: DiagnosticCard }) {
  const m = card.metrics || {};
  return (
    <div className="rounded border border-terminal-border/80 bg-terminal-panel/40 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-terminal-muted">
          {card.label}
        </div>
        <DiagStatusBadge status={String(card.status)} />
      </div>
      {card.reason ? (
        <p className="mt-1.5 text-[11px] leading-snug text-terminal-text/90">{card.reason}</p>
      ) : null}
      <dl className="mt-2 space-y-0.5 font-mono text-[10px] text-terminal-muted">
        {card.last_checked ? (
          <div className="flex justify-between gap-2">
            <dt>Last checked</dt>
            <dd className="truncate text-terminal-text/80">{card.last_checked}</dd>
          </div>
        ) : null}
        {card.latency_ms != null ? (
          <div className="flex justify-between gap-2">
            <dt>Latency</dt>
            <dd>{Number(card.latency_ms).toFixed(0)}ms</dd>
          </div>
        ) : null}
        {card.last_success ? (
          <div className="flex justify-between gap-2">
            <dt>Last success</dt>
            <dd className="truncate">{card.last_success}</dd>
          </div>
        ) : null}
        {card.error_count != null ? (
          <div className="flex justify-between gap-2">
            <dt>Errors</dt>
            <dd>{card.error_count}</dd>
          </div>
        ) : null}
        {card.stale_seconds != null ? (
          <div className="flex justify-between gap-2">
            <dt>Stale</dt>
            <dd>{fmtAge(card.stale_seconds)}</dd>
          </div>
        ) : null}
        {Object.entries(m)
          .filter(([, v]) => v != null && typeof v !== "object")
          .slice(0, 6)
          .map(([k, v]) => (
            <div key={k} className="flex justify-between gap-2">
              <dt className="truncate">{k}</dt>
              <dd className="truncate text-terminal-text/80">{String(v)}</dd>
            </div>
          ))}
      </dl>
    </div>
  );
}

function IssuesTab({
  selectedId,
}: {
  selectedId?: string;
}) {
  const navigate = useNavigate();
  const [issues, setIssues] = useState<DiagnosticIssue[]>([]);
  const [statusFilter, setStatusFilter] = useState("OPEN");
  const [detail, setDetail] = useState<{
    issue: DiagnosticIssue;
    events: Array<Record<string, unknown>>;
    timeline: Array<Record<string, unknown>>;
  } | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [aiMd, setAiMd] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const res = await fetchDiagnosticsIssues({
      status: statusFilter || undefined,
      limit: 200,
    });
    setIssues((res.issues || []) as unknown as DiagnosticIssue[]);
  }, [statusFilter]);

  useEffect(() => {
    void reload().catch(() => undefined);
    const id = window.setInterval(() => void reload().catch(() => undefined), 5000);
    return () => window.clearInterval(id);
  }, [reload]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError(null);
      setAiMd(null);
      return;
    }
    let alive = true;
    setDetail(null);
    setDetailError(null);
    fetchDiagnosticsIssue(selectedId)
      .then((d) => {
        if (!alive) return;
        if (!d?.issue) {
          setDetailError("Issue payload missing");
          return;
        }
        setDetail({
          issue: d.issue as unknown as DiagnosticIssue,
          events: d.events || [],
          timeline: d.timeline || [],
        });
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setDetail(null);
        setDetailError(e instanceof Error ? e.message : "Failed to load issue");
      });
    return () => {
      alive = false;
    };
  }, [selectedId]);

  async function onCopy(label: string, text: string) {
    await copyText(text);
    setCopied(label);
    window.setTimeout(() => setCopied(null), 1500);
  }

  if (selectedId && !detail) {
    return (
      <div className="space-y-3">
        <button
          type="button"
          onClick={() => navigate("/diagnostics/issues")}
          className="text-[11px] text-terminal-accent underline"
        >
          ← Back to issues
        </button>
        {detailError ? (
          <p className="text-xs text-rose-300">{detailError}</p>
        ) : (
          <p className="text-xs text-terminal-muted">
            Loading issue {selectedId}…
          </p>
        )}
      </div>
    );
  }

  if (selectedId && detail) {
    const issue = detail.issue;
    return (
      <div className="space-y-4">
        <button
          type="button"
          onClick={() => navigate("/diagnostics/issues")}
          className="text-[11px] text-terminal-accent underline"
        >
          ← Back to issues
        </button>
        <div className="flex flex-wrap items-center gap-2">
          <DiagStatusBadge status={issue.severity} />
          <DiagStatusBadge status={issue.status} />
          <span className="font-mono text-xs text-terminal-text">{issue.diagnostic_id}</span>
        </div>
        <h3 className="font-display text-base font-semibold">{issue.message}</h3>

        <section className="grid gap-3 md:grid-cols-2">
          <InfoBlock title="SUMMARY">
            <Row k="Component" v={issue.component} />
            <Row k="Service" v={issue.service} />
            <Row k="Subsystem" v={issue.subsystem} />
            <Row k="Category" v={issue.category} />
            <Row k="Occurrences" v={String(issue.occurrence_count)} />
            <Row k="First seen" v={issue.first_seen} />
            <Row k="Last seen" v={issue.last_seen} />
            <Row k="Owner" v={issue.owner || "—"} />
          </InfoBlock>
          <InfoBlock title="EXACT LOCATION">
            <Row k="File" v={issue.file} />
            <Row k="Function" v={issue.function} />
            <Row k="Line" v={issue.line != null ? String(issue.line) : "—"} />
            <Row k="Location" v={issue.location} />
            <Row k="Exception" v={issue.exception_type} />
            <Row k="Error code" v={issue.error_code} />
            <Row k="Symbol" v={issue.symbol} />
            <Row k="Timeframe" v={issue.timeframe} />
            <Row k="Provider" v={issue.provider} />
            <Row k="Endpoint" v={issue.endpoint} />
          </InfoBlock>
          <InfoBlock title="EXPECTED / ACTUAL">
            <Row k="Expected" v={issue.expected || "—"} />
            <Row k="Actual" v={issue.actual || "—"} />
          </InfoBlock>
          <InfoBlock title="TIMELINE">
            {(detail.timeline || []).slice(-12).map((t) => (
              <div key={String(t.event_id)} className="font-mono text-[10px] text-terminal-muted">
                [{String(t.at)}] {String(t.severity)} — {String(t.message)}
              </div>
            ))}
            {!detail.timeline?.length ? (
              <div className="text-[11px] text-terminal-muted">No events</div>
            ) : null}
          </InfoBlock>
        </section>

        {issue.stack_trace ? (
          <InfoBlock title="STACK TRACE">
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-rose-200/90">
              {issue.stack_trace}
            </pre>
          </InfoBlock>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded border border-terminal-border px-2 py-1 text-[11px] hover:border-terminal-accent"
            onClick={() => void onCopy("issue", JSON.stringify(issue, null, 2))}
          >
            Copy Issue
          </button>
          <button
            type="button"
            className="rounded border border-terminal-accent/50 bg-terminal-accent/10 px-2 py-1 text-[11px] text-terminal-accent"
            onClick={async () => {
              const pkg = await fetchDiagnosticsAiPackage(issue.id);
              setAiMd(pkg.markdown);
              await onCopy("ai", pkg.markdown);
            }}
          >
            Copy AI Fix Package
          </button>
          {issue.stack_trace ? (
            <button
              type="button"
              className="rounded border border-terminal-border px-2 py-1 text-[11px]"
              onClick={() => void onCopy("stack", issue.stack_trace || "")}
            >
              Copy Stack Trace
            </button>
          ) : null}
          <button
            type="button"
            className="rounded border border-terminal-border px-2 py-1 text-[11px]"
            onClick={() =>
              void onCopy("json", JSON.stringify({ issue, events: detail.events }, null, 2))
            }
          >
            Copy JSON
          </button>
          {issue.status === "OPEN" ? (
            <button
              type="button"
              className="rounded border border-amber-500/40 px-2 py-1 text-[11px] text-amber-200"
              onClick={async () => {
                await acknowledgeDiagnosticsIssue(issue.id, "ui");
                navigate(`/diagnostics/issues/${issue.id}`);
                const d = await fetchDiagnosticsIssue(issue.id);
                setDetail({
                  issue: d.issue as unknown as DiagnosticIssue,
                  events: d.events || [],
                  timeline: d.timeline || [],
                });
              }}
            >
              Acknowledge
            </button>
          ) : null}
          {issue.status !== "RESOLVED" ? (
            <button
              type="button"
              className="rounded border border-emerald-500/40 px-2 py-1 text-[11px] text-emerald-200"
              onClick={async () => {
                await resolveDiagnosticsIssue(issue.id, "Resolved from UI");
                const d = await fetchDiagnosticsIssue(issue.id);
                setDetail({
                  issue: d.issue as unknown as DiagnosticIssue,
                  events: d.events || [],
                  timeline: d.timeline || [],
                });
                void reload();
              }}
            >
              Resolve
            </button>
          ) : null}
          {copied ? (
            <span className="self-center text-[10px] text-emerald-300">Copied {copied}</span>
          ) : null}
        </div>

        {aiMd ? (
          <InfoBlock title="AI FIX PACKAGE">
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-terminal-text/90">
              {aiMd}
            </pre>
          </InfoBlock>
        ) : null}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="text-[11px] text-terminal-muted">
          Status{" "}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="ml-1 rounded border border-terminal-border bg-terminal-bg px-2 py-1 text-[11px]"
          >
            <option value="">All</option>
            <option value="OPEN">OPEN</option>
            <option value="ACKNOWLEDGED">ACKNOWLEDGED</option>
            <option value="RESOLVED">RESOLVED</option>
            <option value="SUPPRESSED">SUPPRESSED</option>
          </select>
        </label>
        <span className="text-[11px] text-terminal-muted">{issues.length} issues</span>
      </div>
      <div className="overflow-auto rounded border border-terminal-border/80">
        <table className="w-full min-w-[900px] border-collapse text-left text-[11px]">
          <thead className="bg-terminal-panel/80 text-[10px] uppercase tracking-wide text-terminal-muted">
            <tr>
              {[
                "Severity",
                "Issue ID",
                "Component",
                "Subsystem",
                "Location",
                "First Seen",
                "Last Seen",
                "Occurrences",
                "Status",
                "Owner",
                "Actions",
              ].map((h) => (
                <th key={h} className="border-b border-terminal-border px-2 py-2 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {issues.map((iss) => (
              <tr
                key={iss.id}
                className="border-b border-terminal-border/50 hover:bg-white/[0.03]"
              >
                <td className="px-2 py-1.5">
                  <DiagStatusBadge status={iss.severity} />
                </td>
                <td className="px-2 py-1.5 font-mono text-[10px]">
                  <Link
                    to={`/diagnostics/issues/${iss.id}`}
                    className="text-terminal-accent underline"
                  >
                    {iss.diagnostic_id}
                  </Link>
                </td>
                <td className="px-2 py-1.5">{iss.component || "—"}</td>
                <td className="px-2 py-1.5">{iss.subsystem || "—"}</td>
                <td className="max-w-[220px] truncate px-2 py-1.5 font-mono text-[10px]" title={iss.location || ""}>
                  {iss.location || iss.file || "—"}
                </td>
                <td className="px-2 py-1.5 font-mono text-[10px]">{iss.first_seen || "—"}</td>
                <td className="px-2 py-1.5 font-mono text-[10px]">{iss.last_seen || "—"}</td>
                <td className="px-2 py-1.5 font-mono">{iss.occurrence_count}</td>
                <td className="px-2 py-1.5">
                  <DiagStatusBadge status={iss.status} />
                </td>
                <td className="px-2 py-1.5">{iss.owner || "—"}</td>
                <td className="px-2 py-1.5">
                  <Link
                    to={`/diagnostics/issues/${iss.id}`}
                    className="text-terminal-accent underline"
                  >
                    Open
                  </Link>
                </td>
              </tr>
            ))}
            {!issues.length ? (
              <tr>
                <td colSpan={11} className="px-3 py-6 text-center text-terminal-muted">
                  No issues matched — diagnostics never invent errors.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v?: string | null }) {
  return (
    <div className="flex justify-between gap-3 font-mono text-[10px]">
      <span className="text-terminal-muted">{k}</span>
      <span className="min-w-0 truncate text-right text-terminal-text/90">{v || "—"}</span>
    </div>
  );
}

function InfoBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded border border-terminal-border/80 p-3">
      <div className="mb-2 text-[10px] uppercase tracking-wide text-terminal-muted">{title}</div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function ResourcesTab() {
  const [res, setRes] = useState<Record<string, unknown> | null>(null);
  const [git, setGit] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const [r, g] = await Promise.all([
          fetchDiagnosticsResources(),
          fetchDiagnosticsGit(),
        ]);
        if (!alive) return;
        setRes(r);
        setGit(g);
      } catch {
        /* keep */
      }
    }
    void load();
    const id = window.setInterval(() => void load(), 30000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const disk = (res?.disk || {}) as {
    drives?: Array<Record<string, unknown>>;
    status?: string;
  };
  const mem = (res?.memory || {}) as Record<string, unknown>;
  const cpu = (res?.cpu || {}) as Record<string, unknown>;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        <InfoBlock title="DISK">
          <DiagStatusBadge status={String(disk.status || "UNKNOWN")} />
          {(disk.drives || []).map((d) => (
            <div key={String(d.drive)} className="mt-2 space-y-0.5 font-mono text-[10px]">
              <div className="text-terminal-text">{String(d.drive)}</div>
              <Row k="Total" v={fmtBytes(d.total_bytes)} />
              <Row k="Used" v={fmtBytes(d.used_bytes)} />
              <Row k="Free" v={fmtBytes(d.free_bytes)} />
              <Row k="Usage" v={d.usage_pct != null ? `${d.usage_pct}%` : "—"} />
              <DiagStatusBadge status={String(d.status || "UNKNOWN")} />
            </div>
          ))}
        </InfoBlock>
        <InfoBlock title="MEMORY">
          <DiagStatusBadge status={String(mem.status || "UNKNOWN")} />
          <div className="mt-2 space-y-0.5">
            <Row k="Total" v={fmtBytes(mem.total_bytes)} />
            <Row k="Used" v={fmtBytes(mem.used_bytes)} />
            <Row k="Available" v={fmtBytes(mem.available_bytes)} />
            <Row
              k="Usage"
              v={mem.usage_pct != null ? `${mem.usage_pct}%` : "—"}
            />
            <Row k="Reason" v={String(mem.reason || "—")} />
          </div>
        </InfoBlock>
        <InfoBlock title="CPU">
          <DiagStatusBadge status={String(cpu.status || "UNKNOWN")} />
          <div className="mt-2 space-y-0.5">
            <Row
              k="Utilization"
              v={cpu.utilization_pct != null ? `${cpu.utilization_pct}%` : "—"}
            />
            <Row k="Reason" v={String(cpu.reason || "—")} />
            <Row
              k="Trend points (5m)"
              v={String(((cpu.trend_5m as unknown[]) || []).length)}
            />
          </div>
        </InfoBlock>
      </div>
      <InfoBlock title="GIT / MULTI-AGENT">
        {git ? (
          <div className="space-y-1">
            <Row k="Branch" v={String(git.branch || "—")} />
            <Row k="Commit" v={String(git.commit_short || git.commit || "—")} />
            <Row k="Worktree" v={String(git.worktree_status || "—")} />
            <Row k="Modified" v={String(git.modified_count ?? "—")} />
            <Row k="Untracked" v={String(git.untracked_count ?? "—")} />
            {git.dirty ? (
              <div className="mt-2">
                <DiagStatusBadge status="WARNING" />
                <span className="ml-2 text-[11px] text-amber-200">WORKTREE DIRTY</span>
                <ul className="mt-1 max-h-40 overflow-auto font-mono text-[10px] text-terminal-muted">
                  {((git.modified_files as string[]) || []).slice(0, 30).map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="mt-1 text-[11px] text-emerald-300/90">Worktree clean</div>
            )}
            {((git.watched_conflicts as string[]) || []).length ? (
              <div className="mt-2 text-[11px] text-amber-200">
                Watched conflict zones: {(git.watched_conflicts as string[]).join(", ")}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="text-[11px] text-terminal-muted">Loading git…</div>
        )}
      </InfoBlock>
    </div>
  );
}

function AiHandoffTab() {
  const [issueId, setIssueId] = useState("");
  const [include, setInclude] = useState({
    issue: true,
    logs: true,
    stack_trace: true,
    service_health: true,
    resources: true,
    git: true,
  });
  const [markdown, setMarkdown] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <div className="max-w-3xl space-y-3">
      <p className="text-xs text-terminal-muted">
        Generate a plain-Markdown package for Cursor / Claude / Codex. Secrets are redacted.
      </p>
      <label className="block text-[11px] text-terminal-muted">
        Issue ID (optional)
        <input
          value={issueId}
          onChange={(e) => setIssueId(e.target.value)}
          placeholder="DIAG-… or issue id"
          className="mt-1 w-full rounded border border-terminal-border bg-terminal-bg px-2 py-1.5 font-mono text-xs"
        />
      </label>
      <div className="flex flex-wrap gap-3 text-[11px]">
        {Object.entries(include).map(([k, v]) => (
          <label key={k} className="inline-flex items-center gap-1.5 text-terminal-muted">
            <input
              type="checkbox"
              checked={v}
              onChange={(e) => setInclude((s) => ({ ...s, [k]: e.target.checked }))}
            />
            {k}
          </label>
        ))}
      </div>
      <button
        type="button"
        disabled={busy}
        className="rounded border border-terminal-accent/50 bg-terminal-accent/15 px-3 py-1.5 text-xs text-terminal-accent"
        onClick={async () => {
          setBusy(true);
          try {
            const res = await postDiagnosticsAiHandoff({
              issue_id: issueId || undefined,
              include,
            });
            setMarkdown(res.markdown || "");
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Generating…" : "GENERATE PACKAGE"}
      </button>
      {markdown ? (
        <>
          <button
            type="button"
            className="ml-2 rounded border border-terminal-border px-2 py-1 text-[11px]"
            onClick={() => void copyText(markdown)}
          >
            Copy Markdown
          </button>
          <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap rounded border border-terminal-border bg-terminal-bg/60 p-3 font-mono text-[10px]">
            {markdown}
          </pre>
        </>
      ) : null}
    </div>
  );
}

export function DiagnosticsPanel() {
  const params = useParams();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const tab = tabFromPath(location.pathname, searchParams.get("tab"));
  // Prefer route param; fall back to path parse (avoids RR ranking edge cases).
  const issueIdFromPath = (() => {
    const m = location.pathname.match(/\/diagnostics\/issues\/([^/]+)$/);
    return m?.[1] ? decodeURIComponent(m[1]) : undefined;
  })();
  const selectedIssueId = params.issueId || issueIdFromPath;
  const [overview, setOverview] = useState<DiagnosticsOverview | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const o = (await fetchDiagnosticsOverview()) as unknown as DiagnosticsOverview;
        if (alive) setOverview(o);
      } catch {
        /* keep */
      }
    }
    void load();
    const sec = overview?.polling?.ui_refresh_seconds ?? 5;
    const id = window.setInterval(() => void load(), sec * 1000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const navigate = useNavigate();

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold">System Diagnostics</h2>
          <p className="mt-1 text-xs text-terminal-muted">
            Forensic Issue Center — measured health only. Never invents errors or fake green lights.
          </p>
        </div>
        {overview ? <DiagStatusBadge status={String(overview.system_status)} /> : null}
      </div>

      <nav className="nav-scroll mt-4 flex gap-1 border-b border-terminal-border pb-2">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => {
              if (t === "Issues") navigate("/diagnostics/issues");
              else if (t === "Overview") navigate("/diagnostics");
              else navigate(`/diagnostics?tab=${t.toLowerCase().replace(/[\s/]+/g, "-")}`);
            }}
            className={`shrink-0 rounded px-2.5 py-1.5 text-[11px] font-medium transition ${
              tab === t
                ? "bg-terminal-accent/15 text-terminal-accent"
                : "text-terminal-muted hover:bg-white/5 hover:text-terminal-text"
            }`}
          >
            {t}
          </button>
        ))}
      </nav>

      <div className="mt-4 min-w-0">
        {tab === "Overview" ? <OverviewTab overview={overview} /> : null}
        {tab === "Issues" ? <IssuesTab selectedId={selectedIssueId} /> : null}
        {tab === "Services" ? (
          <OverviewTab overview={overview} />
        ) : null}
        {tab === "Data Health" ? <DataHealthTab /> : null}
        {tab === "Database" ? <DatabaseTab /> : null}
        {tab === "WebSocket" ? <WebsocketTab /> : null}
        {tab === "REST/API" ? <RestTab /> : null}
        {tab === "Research" ? <ResearchJobsTab /> : null}
        {tab === "Resources" ? <ResourcesTab /> : null}
        {tab === "Backups" ? <BackupsTab /> : null}
        {tab === "Logs" ? <LogsTab /> : null}
        {tab === "AI Fix Package" ? <AiHandoffTab /> : null}
      </div>
    </div>
  );
}
