import { useState } from "react";
import { Link } from "react-router-dom";
import { fetchDiagnosticsWhy } from "../../api/client";
import { DiagStatusBadge } from "./DiagStatusBadge";
import type { WhyChain } from "./types";

export function WhyBrokenButton({
  dataset,
  label = "Why?",
  className = "",
}: {
  dataset: string;
  label?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [chain, setChain] = useState<WhyChain | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = (await fetchDiagnosticsWhy(dataset)) as WhyChain;
      setChain(data);
      setOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load chain");
      setOpen(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => void load()}
        disabled={loading}
        title={`Why is ${dataset} broken? — forensic dependency chain`}
        className={`inline-flex h-6 items-center rounded border border-terminal-border px-1.5 text-[10px] font-medium text-terminal-muted transition hover:border-terminal-accent hover:text-terminal-accent ${className}`}
      >
        {loading ? "…" : label}
      </button>
      {open ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          role="dialog"
          aria-label="Why is this broken"
        >
          <div className="max-h-[85vh] w-full max-w-lg overflow-auto rounded border border-terminal-border bg-terminal-panel p-4 shadow-xl">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="font-display text-sm font-semibold text-terminal-text">
                  Why is this broken?
                </div>
                <div className="mt-0.5 text-[11px] text-terminal-muted">
                  {chain?.dataset || dataset} forensic chain
                </div>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded border border-terminal-border px-2 py-0.5 text-[11px] text-terminal-muted hover:text-terminal-text"
              >
                Close
              </button>
            </div>
            {error ? (
              <p className="mt-3 text-xs text-rose-300">{error}</p>
            ) : null}
            {chain ? (
              <div className="mt-3 space-y-2">
                <div className="flex items-center gap-2">
                  <DiagStatusBadge status={chain.status} />
                  <span className="text-[11px] text-terminal-muted">{chain.reason}</span>
                </div>
                <ol className="space-y-0">
                  {chain.nodes.map((n, idx) => {
                    const bad = !n.ok;
                    return (
                      <li key={n.key} className="relative pl-4">
                        {idx < chain.nodes.length - 1 ? (
                          <span
                            className="absolute left-[7px] top-5 h-[calc(100%-8px)] w-px bg-terminal-border"
                            aria-hidden
                          />
                        ) : null}
                        <div className="flex items-start gap-2 py-1.5">
                          <span
                            className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                              bad ? "bg-rose-400" : "bg-emerald-400/70"
                            }`}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="font-mono text-xs text-terminal-text">
                                {bad ? "❌ " : ""}
                                {n.label}
                              </span>
                              <DiagStatusBadge status={n.status} />
                            </div>
                            {n.reason ? (
                              <div className="mt-0.5 text-[10px] text-terminal-muted">
                                {n.reason}
                              </div>
                            ) : null}
                            {n.issue ? (
                              <div className="mt-1 font-mono text-[10px] text-amber-200/90">
                                {n.issue.location || n.issue.file}
                                {n.issue.line != null ? `:${n.issue.line}` : ""}
                                {n.issue.function ? ` (${n.issue.function})` : ""}
                                <br />
                                {n.issue.message}
                                {n.issue.diagnostic_id ? (
                                  <>
                                    {" · "}
                                    <Link
                                      to={`/diagnostics/issues/${n.issue.id || n.issue.diagnostic_id}`}
                                      className="text-terminal-accent underline"
                                      onClick={() => setOpen(false)}
                                    >
                                      {n.issue.diagnostic_id}
                                    </Link>
                                  </>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ol>
                <Link
                  to="/diagnostics"
                  className="mt-2 inline-block text-[11px] text-terminal-accent underline"
                  onClick={() => setOpen(false)}
                >
                  Open System Diagnostics →
                </Link>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}
