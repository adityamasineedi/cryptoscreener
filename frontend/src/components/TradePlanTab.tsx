import { useMemo, useState } from "react";
import type { ScreenerRow } from "../types/market";
import {
  buildTradePlan,
  computePositionSize,
  type TradePlanState,
  type TradePlanView,
} from "../tradePlan/buildTradePlan";

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

function Lifecycle({ active }: { active: TradePlanView["lifecycleStep"] }) {
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

  const hideLevels =
    plan.planState === "WAITING" &&
    plan.entry.price == null &&
    plan.stop.price == null;

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-terminal-muted">
        Analysis / decision-support only. Does not place trades. Market Signal ≠ immediate entry.
      </p>

      {/* Header card */}
      <div className="rounded border border-terminal-border/70 bg-black/20 p-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="font-display text-lg font-semibold">{plan.symbol}</div>
          <div className="font-mono text-xs text-terminal-muted">{plan.timeframe.toUpperCase()}</div>
        </div>

        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-terminal-muted">
              Market signal
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
              {plan.planState}
            </div>
            <p className="mt-0.5 text-[10px] text-terminal-muted">
              Raw engine: {plan.setupStatusRaw}
            </p>
          </div>
        </div>

        <div className="mt-3 rounded border border-terminal-border/50 px-2 py-1.5">
          <div className="text-[10px] uppercase tracking-wide text-terminal-muted">Status</div>
          <div className={`font-mono text-sm ${stateColor(plan.planState)}`}>
            {plan.statusMessage}
          </div>
        </div>

        {plan.conflictDetail ? (
          <div className="mt-2 font-mono text-[11px] text-amber-300">
            MTF: {plan.conflictDetail}
          </div>
        ) : null}
      </div>

      {/* Levels */}
      {!hideLevels ? (
        <div className="rounded border border-terminal-border/60 p-3 font-mono text-xs">
          <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
            Levels (from setup engine)
          </div>
          <div className="space-y-1">
            <div className="flex justify-between gap-2">
              <span className="text-terminal-muted">ENTRY</span>
              <span>{plan.entry.display}</span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-terminal-muted">STOP LOSS</span>
              <span className="text-terminal-down">{plan.stop.display}</span>
            </div>
            {plan.targets.map((t) => (
              <div key={t.label} className="flex justify-between gap-2">
                <span className="text-terminal-muted">{t.label}</span>
                <span style={{ color: "#6cb6ff" }}>{t.display}</span>
              </div>
            ))}
            <div className="mt-2 border-t border-terminal-border/40 pt-2">
              <div className="text-terminal-muted">R:R</div>
              {plan.rrLines.map((line) => (
                <div key={line}>{line}</div>
              ))}
            </div>
            {plan.riskPerUnit != null ? (
              <div className="pt-1 text-terminal-muted">
                Risk / unit: {plan.riskPerUnit.toLocaleString(undefined, { maximumFractionDigits: 6 })}
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="rounded border border-terminal-border/60 p-3 text-xs text-terminal-muted">
          No entry / SL / TP while status is WAITING — levels appear when the setup engine provides them.
        </div>
      )}

      {/* Confirmations */}
      <div className="rounded border border-terminal-border/60 p-3">
        <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">Why</div>
        <div className="mb-2 text-[10px] font-medium uppercase text-terminal-muted">
          Confirmations
        </div>
        <ul className="mb-3 space-y-0.5 font-mono text-[11px]">
          {plan.confirmations.length === 0 ? (
            <li className="text-terminal-muted">No confirmations yet</li>
          ) : (
            plan.confirmations.map((c) => (
              <li key={c.id} className="text-terminal-up">
                ✓ {c.label}
                {c.detail ? <span className="text-terminal-muted"> — {c.detail}</span> : null}
              </li>
            ))
          )}
        </ul>
        <div className="mb-2 text-[10px] font-medium uppercase text-terminal-muted">Missing</div>
        <ul className="mb-3 space-y-0.5 font-mono text-[11px]">
          {plan.missing.length === 0 ? (
            <li className="text-terminal-muted">None</li>
          ) : (
            plan.missing.map((c) => (
              <li key={c.id} className="text-terminal-muted">
                ○ {c.label}
              </li>
            ))
          )}
        </ul>
        <div className="mb-2 text-[10px] font-medium uppercase text-terminal-muted">
          Unavailable data
        </div>
        <ul className="space-y-0.5 font-mono text-[11px]">
          {plan.unavailable.length === 0 ? (
            <li className="text-terminal-muted">None flagged</li>
          ) : (
            plan.unavailable.map((c) => (
              <li key={c.id} className="text-terminal-muted">
                N/A — {c.label} unavailable
              </li>
            ))
          )}
        </ul>
        {plan.failures.length > 0 ? (
          <>
            <div className="mb-2 mt-3 text-[10px] font-medium uppercase text-terminal-muted">
              Failed checks
            </div>
            <ul className="space-y-0.5 font-mono text-[11px]">
              {plan.failures.map((c) => (
                <li key={c.id} className="text-terminal-down">
                  ✗ {c.label}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </div>

      {/* Invalidation */}
      <div className="rounded border border-terminal-border/60 p-3">
        <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
          What invalidates it
        </div>
        <p className="font-mono text-xs text-amber-200">{plan.invalidation}</p>
        {plan.stop.price != null ? (
          <p className="mt-1 text-[10px] text-terminal-muted">
            SL from stop engine: {plan.stop.display} (not invented in the UI)
          </p>
        ) : null}
      </div>

      <Lifecycle active={plan.lifecycleStep} />

      <div className="rounded border border-terminal-border/60 p-2">
        <div className="mb-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          Candle-1 / Candle-2 (explanatory)
        </div>
        <ol className="space-y-2 font-mono text-[11px]">
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

      {plan.mtfLines.length > 0 ? (
        <div className="rounded border border-terminal-border/60 p-2 font-mono text-[11px]">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-terminal-muted">
            Timeframes
          </div>
          {plan.mtfLines.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </div>
      ) : null}

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
                : sizing.error || "—"}
            </span>
          </div>
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
        <div className="rounded border border-terminal-border/60 p-2 font-mono text-[10px] text-terminal-muted">
          <div className="mb-1 uppercase tracking-wide">Data notes</div>
          {plan.dataNotes.map((n) => (
            <div key={n}>{n}</div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
