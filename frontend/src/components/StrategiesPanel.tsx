/**
 * Working strategies catalog — clear status, gates, and worked examples.
 * Read-only operator view; does not place orders.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchStrategyCatalog,
  type StrategyCatalogItem,
  type StrategyCatalogResponse,
} from "../api/client";

function statusClass(status: string): string {
  switch (status.toUpperCase()) {
    case "WORKING":
      return "border-emerald-500/50 bg-emerald-500/15 text-emerald-300";
    case "RESEARCH":
      return "border-sky-500/40 bg-sky-500/10 text-sky-300";
    case "BASELINE":
      return "border-amber-500/40 bg-amber-500/10 text-amber-200";
    case "EXPERIMENTAL":
      return "border-violet-500/40 bg-violet-500/10 text-violet-300";
    default:
      return "border-terminal-border bg-white/5 text-terminal-muted";
  }
}

function StrategyCard({
  item,
  selected,
  onSelect,
}: {
  item: StrategyCatalogItem;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-lg border px-3 py-3 text-left transition ${
        selected
          ? "border-terminal-accent bg-terminal-accent/10"
          : "border-terminal-border/70 bg-terminal-panel/40 hover:border-terminal-border hover:bg-white/[0.03]"
      }`}
      data-testid={`strategy-card-${item.id}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide ${statusClass(
            item.status
          )}`}
        >
          {item.status}
        </span>
        {item.setup_timeframe ? (
          <span className="font-mono text-[10px] text-terminal-muted">
            {item.setup_timeframe}
            {item.direction ? ` · ${item.direction}` : ""}
          </span>
        ) : null}
      </div>
      <div className="mt-1.5 text-sm font-medium text-terminal-text">{item.name}</div>
      <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-terminal-muted">
        {item.summary}
      </p>
    </button>
  );
}

function ExampleBlock({ item }: { item: StrategyCatalogItem }) {
  const example = item.example;
  if (!example) return null;

  const scenario = Array.isArray(example.scenario) ? example.scenario : null;
  const steps = Array.isArray(example.steps)
    ? example.steps.map(String)
    : null;
  const fails = Array.isArray(example.fail_closed_examples)
    ? example.fail_closed_examples.map(String)
    : null;

  return (
    <section className="rounded-lg border border-terminal-border/70 bg-black/20 p-4">
      <h3 className="font-mono text-xs font-semibold uppercase tracking-wide text-terminal-text">
        {String(example.title || "Example")}
      </h3>
      {example.symbol || example.setup_tf ? (
        <p className="mt-1 font-mono text-[11px] text-terminal-muted">
          {[example.symbol, example.setup_tf].filter(Boolean).join(" · ")}
        </p>
      ) : null}

      {scenario ? (
        <ol className="mt-3 space-y-2">
          {scenario.map((row) => {
            const step = row as Record<string, unknown>;
            return (
              <li
                key={String(step.step)}
                className="grid grid-cols-[2rem_1fr] gap-2 rounded border border-terminal-border/40 bg-terminal-panel/30 px-2 py-1.5 text-[11px]"
              >
                <span className="font-mono text-terminal-accent">
                  {String(step.step)}
                </span>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-terminal-text">
                      {String(step.check)}
                    </span>
                    <span
                      className={
                        step.pass
                          ? "text-emerald-300"
                          : "text-terminal-muted"
                      }
                    >
                      {step.pass ? "PASS" : "—"}
                    </span>
                  </div>
                  <div className="mt-0.5 text-terminal-muted">
                    {String(step.result)}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}

      {steps ? (
        <ol className="mt-3 list-decimal space-y-1 pl-4 text-[11px] text-terminal-muted">
          {steps.map((s) => (
            <li key={s} className="leading-relaxed">
              {s}
            </li>
          ))}
        </ol>
      ) : null}

      {fails ? (
        <div className="mt-3">
          <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
            Fail-closed examples
          </div>
          <ul className="mt-1 space-y-1 text-[11px] text-amber-200/90">
            {fails.map((f) => (
              <li key={f}>• {f}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {example.note ? (
        <p className="mt-3 text-[10px] leading-relaxed text-terminal-muted">
          {String(example.note)}
        </p>
      ) : null}
    </section>
  );
}

export function StrategiesPanel() {
  const [data, setData] = useState<StrategyCatalogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchStrategyCatalog()
      .then((payload) => {
        if (cancelled) return;
        setData(payload);
        setError(null);
        setSelectedId(
          payload.primary_working || payload.strategies?.[0]?.id || null
        );
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = useMemo(
    () => data?.strategies?.find((s) => s.id === selectedId) ?? null,
    [data, selectedId]
  );

  const sizing = data?.sizing_example;

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      data-testid="strategies-panel"
    >
      <header className="shrink-0 border-b border-terminal-border bg-terminal-panel/50 px-4 py-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="font-display text-lg font-semibold text-terminal-text">
              Strategies
            </h1>
            <p className="mt-0.5 max-w-3xl text-[11px] leading-relaxed text-terminal-muted">
              {data?.disclaimer ||
                "Working vs research playbooks. Read-only — no auto execution."}
            </p>
          </div>
          {data?.freeze_tags ? (
            <div className="font-mono text-[10px] text-terminal-muted">
              <div>Logic freeze: {data.freeze_tags.strategy_logic}</div>
              <div>
                Code freeze: {data.freeze_tags.pre_research_baseline}
              </div>
            </div>
          ) : null}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {loading ? (
          <div className="text-sm text-terminal-muted">Loading strategies…</div>
        ) : null}
        {error ? (
          <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        {data ? (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
            <aside className="space-y-2">
              <div className="mb-2 text-[10px] uppercase tracking-wide text-terminal-muted">
                Catalog ({data.strategies?.length ?? 0})
              </div>
              {(data.strategies || []).map((item) => (
                <StrategyCard
                  key={item.id}
                  item={item}
                  selected={item.id === selectedId}
                  onSelect={() => setSelectedId(item.id)}
                />
              ))}
            </aside>

            <div className="space-y-4">
              {selected ? (
                <>
                  <section className="rounded-lg border border-terminal-border/70 bg-terminal-panel/40 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase ${statusClass(
                          selected.status
                        )}`}
                      >
                        {selected.status}
                      </span>
                      {selected.combo_version ? (
                        <span className="font-mono text-[10px] text-terminal-muted">
                          {selected.combo_version}
                        </span>
                      ) : null}
                    </div>
                    <h2 className="mt-2 text-base font-semibold text-terminal-text">
                      {selected.name}
                    </h2>
                    <p className="mt-1 text-[12px] leading-relaxed text-terminal-muted">
                      {selected.summary}
                    </p>

                    {selected.gates?.length ? (
                      <div className="mt-3">
                        <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
                          Gates / checklist
                        </div>
                        <ul className="mt-1.5 space-y-1">
                          {selected.gates.map((g) => (
                            <li
                              key={g}
                              className="flex gap-2 text-[11px] text-terminal-text"
                            >
                              <span className="text-emerald-400">✓</span>
                              <span>{g}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {selected.symbols ? (
                      <div className="mt-3 grid gap-2 text-[11px] sm:grid-cols-2">
                        <div className="rounded border border-terminal-border/50 bg-black/20 px-2 py-1.5">
                          <div className="text-[10px] uppercase text-terminal-muted">
                            Freeze claim set
                          </div>
                          <div className="mt-0.5 font-mono text-terminal-text">
                            {(selected.symbols.freeze_claim || []).join(" · ") ||
                              "—"}
                          </div>
                        </div>
                        <div className="rounded border border-terminal-border/50 bg-black/20 px-2 py-1.5">
                          <div className="text-[10px] uppercase text-terminal-muted">
                            Paper watch ({selected.symbols.paper_watch?.length ?? 0})
                          </div>
                          <div className="mt-0.5 font-mono text-[10px] leading-relaxed text-terminal-muted">
                            {(selected.symbols.paper_watch || [])
                              .slice(0, 12)
                              .join(", ")}
                            {(selected.symbols.paper_watch?.length || 0) > 12
                              ? "…"
                              : ""}
                          </div>
                        </div>
                      </div>
                    ) : null}

                    {selected.risk ? (
                      <div className="mt-3 rounded border border-terminal-border/50 bg-black/20 px-2 py-1.5 text-[11px]">
                        <span className="text-terminal-muted">Risk: </span>
                        <span className="text-terminal-text">
                          {selected.risk.display}
                        </span>
                        {selected.risk.example_equity_usd != null ? (
                          <span className="text-terminal-muted">
                            {" "}
                            · e.g. ${selected.risk.example_equity_usd} → $
                            {selected.risk.example_risk_usd} = 1R
                          </span>
                        ) : null}
                      </div>
                    ) : null}

                    {selected.where_to_run?.length ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {selected.where_to_run.map((link) => (
                          <Link
                            key={link.path + link.label}
                            to={link.path}
                            className="rounded border border-sky-500/30 bg-sky-500/10 px-2.5 py-1 text-[11px] text-sky-300 hover:bg-sky-500/20"
                          >
                            {link.label} →
                          </Link>
                        ))}
                      </div>
                    ) : null}

                    {selected.do_not?.length ? (
                      <div className="mt-3">
                        <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
                          Do not
                        </div>
                        <ul className="mt-1 space-y-1 text-[11px] text-red-200/80">
                          {selected.do_not.map((d) => (
                            <li key={d}>• {d}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </section>

                  <ExampleBlock item={selected} />
                </>
              ) : (
                <div className="text-sm text-terminal-muted">
                  Select a strategy to see details and examples.
                </div>
              )}

              {sizing ? (
                <section className="rounded-lg border border-terminal-border/70 bg-terminal-panel/40 p-4">
                  <h3 className="font-mono text-xs font-semibold uppercase tracking-wide text-terminal-text">
                    {sizing.title}
                  </h3>
                  <div className="mt-2 overflow-x-auto">
                    <table className="w-full min-w-[28rem] text-left text-[11px]">
                      <tbody className="text-terminal-muted">
                        <tr className="border-b border-terminal-border/30">
                          <td className="py-1 pr-3">Equity</td>
                          <td className="py-1 font-mono text-terminal-text">
                            ${sizing.equity_usd}
                          </td>
                        </tr>
                        <tr className="border-b border-terminal-border/30">
                          <td className="py-1 pr-3">Risk</td>
                          <td className="py-1 font-mono text-terminal-text">
                            {(Number(sizing.risk_percent) * 100).toFixed(0)}% = $
                            {sizing.risk_usd} (1R)
                          </td>
                        </tr>
                        <tr className="border-b border-terminal-border/30">
                          <td className="py-1 pr-3">Entry / Stop</td>
                          <td className="py-1 font-mono text-terminal-text">
                            {sizing.entry} / {sizing.stop}
                          </td>
                        </tr>
                        <tr className="border-b border-terminal-border/30">
                          <td className="py-1 pr-3">Qty</td>
                          <td className="py-1 font-mono text-terminal-text">
                            {sizing.risk_usd} / {sizing.risk_per_unit} ={" "}
                            {sizing.qty}
                          </td>
                        </tr>
                        <tr>
                          <td className="py-1 pr-3">TP1 (2R)</td>
                          <td className="py-1 font-mono text-terminal-text">
                            {sizing.tp1_2r} — {sizing.outcome}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </section>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
