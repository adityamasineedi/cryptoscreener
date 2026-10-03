import { useMemo, useState, type ReactNode } from "react";
import type { ScreenerRow } from "../types/market";
import {
  buildTradePlan,
  computePositionSize,
  type TradePlanState,
  type TradePlanView,
} from "../tradePlan/buildTradePlan";

const TOOLTIPS = {
  mtfConflict:
    "Different timeframes currently have different directional states. This does not by itself create a trade.",
  dataAvailable:
    "Provider data is available. Availability is not a directional confirmation.",
  missing: "Required engine condition has not been satisfied yet.",
  na: "Not confirmed or not applicable with the currently available data.",
  entryReady:
    "Only shown when the existing entry engine returns a valid entry candidate and all required gates pass.",
  marketSignal:
    "Directional classification from the existing market-signal engine. It is not an entry order.",
} as const;

function Tip({ text, children }: { text: string; children: ReactNode }) {
  return (
    <span className="cursor-help border-b border-dotted border-terminal-muted/50" title={text}>
      {children}
    </span>
  );
}

function stateColor(state: TradePlanState): string {
  switch (state) {
    case "ENTRY_READY":
      return "text-terminal-up";
    case "LONG_ENTRY_CANDIDATE":
    case "BUY_BIAS":
      return "text-emerald-300";
    case "SHORT_ENTRY_CANDIDATE":
    case "SELL_BIAS":
      return "text-rose-300";
    case "INVALIDATED":
    case "CONFLICT":
      return "text-amber-300";
    case "WAITING":
    case "NO_SETUP":
    default:
      return "text-terminal-muted";
  }
}

function marketColor(signal: string): string {
  if (signal.includes("BUY")) return "text-terminal-up";
  if (signal.includes("SELL")) return "text-terminal-down";
  return "text-terminal-muted";
}

function mtfToneClass(tone: TradePlanView["mtfAlignment"]["tone"]): string {
  switch (tone) {
    case "aligned":
      return "text-terminal-up";
    case "mixed":
    case "conflict":
      return "text-amber-300";
    default:
      return "text-terminal-muted";
  }
}

function fmtMoney(n: number | null, currency: "USD" | "INR"): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const locale = currency === "INR" ? "en-IN" : "en-US";
  const code = currency === "INR" ? "INR" : "USD";
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: code,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return `${n.toFixed(2)}`;
  }
}

function Section({
  title,
  tip,
  children,
  defaultOpen = true,
  tone,
}: {
  title: string;
  tip?: string;
  children: ReactNode;
  defaultOpen?: boolean;
  tone?: "default" | "warn" | "muted";
}) {
  const border =
    tone === "warn"
      ? "border-amber-500/40"
      : tone === "muted"
        ? "border-terminal-border/40"
        : "border-terminal-border/60";
  return (
    <details open={defaultOpen} className={`rounded border ${border} p-2`}>
      <summary className="cursor-pointer text-[11px] uppercase tracking-wide text-terminal-muted">
        {tip ? <Tip text={tip}>{title}</Tip> : title}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

function Lifecycle({
  active,
  waitingNote,
}: {
  active: TradePlanView["lifecycleStep"];
  waitingNote: string | null;
}) {
  const steps: Array<{ key: TradePlanView["lifecycleStep"]; label: string }> = [
    { key: "SETUP", label: "SETUP" },
    { key: "CONFIRMATION", label: "CONFIRMATION" },
    { key: "ENTRY", label: "ENTRY" },
    { key: "STOP_TARGET", label: "STOP / TARGET" },
    { key: "EXIT", label: "EXIT" },
  ];
  const order = ["WAIT", "SETUP", "CONFIRMATION", "ENTRY", "STOP_TARGET", "EXIT"];
  const activeIdx = order.indexOf(active);
  return (
    <div className="rounded border border-terminal-border/60 p-2">
      <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
        Trade lifecycle
      </div>
      <ol className="space-y-1 font-mono text-[11px]">
        {steps.map((s, i) => {
          const idx = order.indexOf(s.key);
          const on = idx === activeIdx || (active === "WAIT" && i === 0 && activeIdx <= 0);
          const passed = activeIdx > 0 && idx < activeIdx && active !== "WAIT";
          return (
            <li key={s.key} className="flex items-center gap-2">
              <span
                className={
                  on
                    ? "text-terminal-accent"
                    : passed
                      ? "text-terminal-up"
                      : "text-terminal-muted/70"
                }
              >
                {on ? "●" : passed ? "✓" : "○"}
              </span>
              <span className={on ? "text-terminal-text" : "text-terminal-muted"}>{s.label}</span>
              {i < steps.length - 1 ? (
                <span className="text-terminal-muted/50">↓</span>
              ) : null}
            </li>
          );
        })}
      </ol>
      {waitingNote ? (
        <p className="mt-2 font-mono text-[10px] text-amber-300">{waitingNote}</p>
      ) : null}
    </div>
  );
}

function SidePlan({ plan }: { plan: TradePlanView }) {
  const long = plan.direction !== "SHORT";
  return (
    <div className="rounded border border-terminal-border/60 p-2 font-mono text-[11px]">
      <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
        {long ? "LONG path" : "SHORT path"}
      </div>
      <div className="space-y-0.5 text-terminal-text">
        <div>Entry → {plan.entry.display}</div>
        <div>
          SL {long ? "below" : "above"} invalidation → {plan.stop.display}
        </div>
        {plan.targets.map((t) => (
          <div key={t.label}>
            {t.label} → {t.display}
            {t.rMultiple != null ? ` (${t.rMultiple.toFixed(1)}R)` : ""}
          </div>
        ))}
      </div>
    </div>
  );
}

function MtfBlock({ plan }: { plan: TradePlanView }) {
  const m = plan.mtfAlignment;
  const icon =
    m.tone === "aligned" ? "✓" : m.tone === "mixed" || m.tone === "conflict" ? "⚠" : "○";
  return (
    <Section
      title="MTF Alignment"
      tip={TOOLTIPS.mtfConflict}
      tone={m.tone === "mixed" || m.tone === "conflict" ? "warn" : "default"}
    >
      <div className={`font-mono text-sm ${mtfToneClass(m.tone)}`}>
        {icon} {m.display}
      </div>
      {m.htf.length > 0 ? (
        <div className="mt-2">
          <div className="text-[10px] uppercase text-terminal-muted">HTF</div>
          {m.htf.map((l) => (
            <div key={l.tf} className="font-mono text-[11px]">
              {l.tf}{" "}
              <span
                className={
                  l.trend === "BULLISH"
                    ? "text-terminal-up"
                    : l.trend === "BEARISH"
                      ? "text-terminal-down"
                      : "text-terminal-muted"
                }
              >
                {l.trend}
              </span>
            </div>
          ))}
        </div>
      ) : null}
      {m.setup ? (
        <div className="mt-1">
          <div className="text-[10px] uppercase text-terminal-muted">Setup TF</div>
          <div className="font-mono text-[11px]">
            {m.setup.tf}{" "}
            <span
              className={
                m.setup.trend === "BULLISH"
                  ? "text-terminal-up"
                  : m.setup.trend === "BEARISH"
                    ? "text-terminal-down"
                    : "text-terminal-muted"
              }
            >
              {m.setup.trend}
            </span>
          </div>
        </div>
      ) : null}
      {m.confirmation ? (
        <div className="mt-1">
          <div className="text-[10px] uppercase text-terminal-muted">Confirmation TF</div>
          <div className="font-mono text-[11px]">
            {m.confirmation.tf}{" "}
            <span
              className={
                m.confirmation.trend === "BULLISH"
                  ? "text-terminal-up"
                  : m.confirmation.trend === "BEARISH"
                    ? "text-terminal-down"
                    : "text-terminal-muted"
              }
            >
              {m.confirmation.trend}
            </span>
          </div>
        </div>
      ) : (
        m.allLines.length > 0 && m.htf.length === 0 ? (
          <div className="mt-2 space-y-0.5 font-mono text-[11px]">
            {m.allLines.map((l) => (
              <div key={l.tf}>
                {l.tf} {l.trend}
              </div>
            ))}
          </div>
        ) : null
      )}
      {m.conflictNote ? (
        <div className="mt-2 font-mono text-[11px] text-amber-300">
          Conflict
          <div className="text-amber-200/90">⚠ {m.conflictNote}</div>
        </div>
      ) : null}
    </Section>
  );
}

export function TradePlanTab({
  payload,
  row,
}: {
  payload?: Record<string, unknown>;
  row: ScreenerRow;
}) {
  const [accountSize, setAccountSize] = useState(500000);
  const [riskPercent, setRiskPercent] = useState(0.5);
  const [currency, setCurrency] = useState<"USD" | "INR">("INR");

  const plan = useMemo(
    () =>
      buildTradePlan({
        symbol: row.symbol,
        setupTab: payload || null,
        setupSignalFresh: row.setup_signal,
        marketSignalFresh: row.market_signal,
        defaultTimeframe: "15m",
      }),
    [payload, row.symbol, row.setup_signal, row.market_signal],
  );

  const sizing = useMemo(
    () =>
      computePositionSize({
        accountSize,
        riskPercent,
        entry: plan.entry.price,
        stop: plan.stop.price,
      }),
    [accountSize, riskPercent, plan.entry.price, plan.stop.price],
  );

  if (!payload && plan.planState === "WAITING" && !row.setup_signal?.value) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-terminal-muted">TRADE PLAN: Waiting for setup engine…</p>
        <p className="font-mono text-sm text-amber-300">WAITING</p>
      </div>
    );
  }

  const hideLevels = !plan.levelsActionable;

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-terminal-muted">
        Analysis / decision-support only. Does not place trades. Market Signal ≠ immediate entry.
      </p>

      {/* Header — immediate state */}
      <div className="rounded border border-terminal-border/70 bg-black/20 p-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="font-display text-lg font-semibold">{plan.symbol}</div>
          <div className="font-mono text-xs text-terminal-muted">{plan.timeframe.toUpperCase()}</div>
        </div>

        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
              <Tip text={TOOLTIPS.marketSignal}>Market signal</Tip>
            </div>
            <div className={`font-display text-xl font-bold ${marketColor(plan.marketSignal)}`}>
              {plan.marketSignalDisplay}
            </div>
            <p className="mt-0.5 text-[10px] text-terminal-muted">
              Directional classification — not an entry order
            </p>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-terminal-muted">Setup</div>
            <div className={`font-display text-xl font-bold ${stateColor(plan.planState)}`}>
              {plan.planState === "CONFLICT" ? "CONFLICT" : plan.planState}
            </div>
            <p className="mt-0.5 text-[10px] text-terminal-muted">
              Raw engine: {plan.setupStatusRaw}
            </p>
          </div>
        </div>

        <div className="mt-3 rounded border border-terminal-border/50 px-2 py-1.5">
          <div className="text-[10px] uppercase tracking-wide text-terminal-muted">Status</div>
          <div className={`font-mono text-sm ${stateColor(plan.planState)}`}>
            {plan.planState === "CONFLICT" ? "CONFLICT — WAIT" : plan.statusMessage}
          </div>
          {plan.planState === "CONFLICT" ? (
            <div className="mt-0.5 font-mono text-[10px] uppercase tracking-wide text-amber-200/80">
              WAIT — NO ENTRY
            </div>
          ) : null}
          {plan.statusWhy ? (
            <div className="mt-1 text-[10px] text-terminal-muted">
              Why? <span className="text-amber-200/90">{plan.statusWhy}</span>
            </div>
          ) : null}
          {plan.planState === "ENTRY_READY" ? (
            <div className="mt-1 text-[10px] text-terminal-muted">
              <Tip text={TOOLTIPS.entryReady}>Entry ready</Tip> — analysis only
            </div>
          ) : null}
        </div>

        {plan.mtfAlignment.summaryLines.length > 0 ? (
          <div className="mt-2 font-mono text-[11px]">
            <div className="text-[10px] uppercase text-terminal-muted">MTF</div>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5">
              {plan.mtfAlignment.summaryLines.map((line) => (
                <span key={line}>{line}</span>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <MtfBlock plan={plan} />

      {/* Trade plan levels */}
      <div className="rounded border border-terminal-border/60 p-3 font-mono text-xs">
        <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Trade Plan
        </div>
        <div className="space-y-1">
          <div className="flex justify-between gap-2">
            <span className="text-terminal-muted">Entry</span>
            <span>{hideLevels ? "—" : plan.entry.display}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-terminal-muted">SL</span>
            <span className={hideLevels ? "" : "text-terminal-down"}>
              {hideLevels ? "—" : plan.stop.display}
            </span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-terminal-muted">TP</span>
            <span style={hideLevels ? undefined : { color: "#6cb6ff" }}>
              {hideLevels
                ? "—"
                : plan.targets
                    .filter((t) => t.price != null)
                    .map((t) => t.display)
                    .join(" / ") || "—"}
            </span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-terminal-muted">R:R</span>
            <span>{hideLevels || plan.rrLines.length === 0 ? "—" : plan.rrLines.join(" · ")}</span>
          </div>
        </div>
        {plan.provisionalInvalidation ? (
          <div className="mt-3 rounded border border-amber-500/35 bg-amber-500/5 px-2 py-2">
            <div className="text-[10px] uppercase tracking-wide text-amber-200/90">
              Provisional Invalidation Reference
            </div>
            <div className="mt-0.5 font-mono text-sm text-amber-100">
              {plan.provisionalInvalidation.display}
            </div>
            <div className="mt-1 text-[10px] font-semibold uppercase tracking-wide text-amber-300">
              NOT AN ACTIVE STOP
            </div>
            <p className="mt-1 text-[10px] leading-snug text-terminal-muted">
              No position exists because entry conditions are not satisfied. Not used for
              position-size calculation.
            </p>
          </div>
        ) : null}
      </div>

      <Section title="Directional Evidence" defaultOpen>
        <ul className="space-y-0.5 font-mono text-[11px]">
          {plan.directionalEvidence.length === 0 ? (
            <li className="text-terminal-muted">None</li>
          ) : (
            plan.directionalEvidence.map((c) => (
              <li key={c.id} className="text-terminal-up">
                ✓ {c.label}
                {c.detail ? <span className="text-terminal-muted"> — {c.detail}</span> : null}
              </li>
            ))
          )}
        </ul>
      </Section>

      <Section title="Data Available" tip={TOOLTIPS.dataAvailable} tone="muted">
        <ul className="space-y-1 font-mono text-[11px]">
          {plan.dataAvailable.length === 0 ? (
            <li className="text-terminal-muted">No optional feeds marked live</li>
          ) : (
            plan.dataAvailable.map((d) => (
              <li key={d.id} className="flex justify-between gap-2 text-terminal-text">
                <span>{d.label}</span>
                <span className="text-terminal-muted">{d.status}</span>
              </li>
            ))
          )}
        </ul>
        <p className="mt-1 text-[10px] text-terminal-muted">
          Availability is not a BUY/SELL or bullish/bearish confirmation.
        </p>
      </Section>

      <Section title="Missing Entry Confirmations" tip={TOOLTIPS.missing} tone="muted">
        <ul className="space-y-1 font-mono text-[11px]">
          {plan.missingEntryConfirmations.length === 0 ? (
            <li className="text-terminal-muted">None</li>
          ) : (
            plan.missingEntryConfirmations.map((c) => (
              <li key={c.id} className="text-terminal-muted">
                <div>○ {c.label}</div>
                {c.detail ? <div className="pl-3 text-[10px] opacity-80">{c.detail}</div> : null}
              </li>
            ))
          )}
        </ul>
      </Section>

      <Section title="Failed Entry Gates" defaultOpen={plan.failedEntryGates.length > 0}>
        <ul className="space-y-1 font-mono text-[11px]">
          {plan.failedEntryGates.length === 0 ? (
            <li className="text-terminal-muted">None</li>
          ) : (
            plan.failedEntryGates.map((c) => (
              <li key={c.id} className="text-terminal-down">
                <div>✗ {c.label}</div>
                {c.detail ? (
                  <div className="pl-3 text-[10px] text-terminal-muted">
                    Reason: {c.detail}
                  </div>
                ) : null}
              </li>
            ))
          )}
        </ul>
      </Section>

      <Section
        title="Unavailable / Not Confirmed"
        tip={TOOLTIPS.na}
        tone="muted"
        defaultOpen={plan.unavailableNotConfirmed.length > 0}
      >
        <ul className="space-y-0.5 font-mono text-[11px] text-terminal-muted">
          {plan.unavailableNotConfirmed.length === 0 ? (
            <li>None flagged</li>
          ) : (
            plan.unavailableNotConfirmed.map((c) => (
              <li key={c.id}>
                {c.detail
                  ? `N/A — ${c.label}: ${c.detail}`
                  : c.id === "choch" || c.id === "supply_demand"
                    ? `N/A — ${c.label}`
                    : `N/A — ${c.label}`}
              </li>
            ))
          )}
        </ul>
      </Section>

      {/* Invalidation */}
      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
          What invalidates it
        </div>
        <p className="font-mono text-xs text-amber-200">{plan.invalidation}</p>
        {plan.stop.price != null ? (
          <p className="mt-1 text-[10px] text-terminal-muted">
            Active SL from stop engine: {plan.stop.display}
          </p>
        ) : null}
      </div>

      <Lifecycle active={plan.lifecycleStep} waitingNote={plan.lifecycleWaitingNote} />

      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-1 flex items-center justify-between gap-2">
          <div className="text-[11px] uppercase tracking-wide text-terminal-muted">
            Candle-1 / Candle-2 (explanatory)
          </div>
          <div className="font-mono text-[10px] text-terminal-muted">
            Current stage: {plan.currentStage}
          </div>
        </div>
        <ol className="space-y-1 font-mono text-[11px]">
          {plan.candleLifecycle.map((c) => (
            <li key={c.title}>
              <div className="text-terminal-text">{c.title}</div>
              <div className="text-terminal-muted">{c.detail}</div>
              <div className="text-terminal-muted/50">↓</div>
            </li>
          ))}
        </ol>
      </div>

      {plan.direction ? <SidePlan plan={plan} /> : null}

      {/* Position size calculator */}
      <div className="rounded border border-terminal-border/60 p-3">
        <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Position size (optional)
        </div>
        <div className="mb-2 flex flex-wrap gap-2">
          <label className="text-[10px] text-terminal-muted">
            Currency
            <select
              className="ml-1 rounded border border-terminal-border bg-black/30 px-1 py-0.5 text-[11px] text-terminal-text"
              value={currency}
              onChange={(e) => setCurrency(e.target.value as "USD" | "INR")}
            >
              <option value="INR">INR</option>
              <option value="USD">USD</option>
            </select>
          </label>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-[10px] text-terminal-muted">
            Account size
            <input
              type="number"
              className="mt-0.5 w-full rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono text-[11px] text-terminal-text"
              value={accountSize}
              min={0}
              onChange={(e) => setAccountSize(Number(e.target.value))}
            />
          </label>
          <label className="text-[10px] text-terminal-muted">
            Risk %
            <input
              type="number"
              className="mt-0.5 w-full rounded border border-terminal-border bg-black/30 px-2 py-1 font-mono text-[11px] text-terminal-text"
              value={riskPercent}
              min={0}
              step={0.1}
              onChange={(e) => setRiskPercent(Number(e.target.value))}
            />
          </label>
        </div>
        <div className="mt-2 space-y-1 font-mono text-[11px]">
          <div className="flex justify-between gap-2">
            <span className="text-terminal-muted">Maximum risk</span>
            <span>{fmtMoney(sizing.maxRisk, currency)}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-terminal-muted">Entry</span>
            <span>{plan.entry.display}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-terminal-muted">SL</span>
            <span>{plan.stop.display}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-terminal-muted">Risk / unit</span>
            <span>
              {sizing.riskPerUnit != null
                ? sizing.riskPerUnit.toLocaleString(undefined, { maximumFractionDigits: 6 })
                : "—"}
            </span>
          </div>
          <div className="flex justify-between gap-2 border-t border-terminal-border/40 pt-1">
            <span className="text-terminal-muted">Position size</span>
            <span>
              {sizing.valid && sizing.positionSize != null
                ? `${sizing.positionSize.toLocaleString(undefined, { maximumFractionDigits: 4 })}  (${sizing.formula})`
                : "Unavailable"}
            </span>
          </div>
          {!sizing.valid && sizing.error ? (
            <div className="text-[10px] text-terminal-muted">Reason: {sizing.error}</div>
          ) : null}
        </div>
        <p className="mt-2 text-[10px] leading-snug text-amber-200/90">
          Position size is calculated from stop distance and account risk. Leverage changes margin
          requirements, not the underlying stop-loss risk. Leverage is NOT the same as risk.
        </p>
        <p className="mt-1 text-[10px] text-terminal-muted">
          Calculator only — does not place or execute trades.
        </p>
      </div>

      {plan.dataNotes.length > 0 ? (
        <Section title="Data Notes" tone="muted" defaultOpen={false}>
          <div className="font-mono text-[10px] text-terminal-muted">
            {plan.dataNotes.map((n) => (
              <div key={n}>{n}</div>
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  );
}
