import { useEffect, useState } from "react";
import {
  fetchStructureLabels,
  type StructureLabelSnapshot,
  type StructureSwingRef,
} from "../api/client";

const BASELINE = "structure-baseline-v1";
const CANDIDATE = "structure-candidate-distinct-pivots-v1";
const STATES = ["bullish", "bearish", "transition", "range", "insufficient_data"] as const;
const TIMEFRAMES = ["4h", "1h", "15m"] as const;
const LABEL_TIMEOUT_MS = 20000;

function pct(count: number, denominator: number): string {
  if (!denominator) return "—";
  return `${((100 * count) / denominator).toFixed(1)}%`;
}

function SwingLine({ label, swing }: { label: string; swing: StructureSwingRef | null }) {
  if (!swing) return null;
  return (
    <div className="text-terminal-muted">
      {label} {swing.price} · pivot {swing.pivot_time ?? "—"} · known {swing.known_at ?? "—"}
    </div>
  );
}

export function StructureLabelPanel() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [ruleVersion, setRuleVersion] = useState(BASELINE);
  const [snapshot, setSnapshot] = useState<StructureLabelSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), LABEL_TIMEOUT_MS);
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSnapshot(null);
    fetchStructureLabels(symbol, ruleVersion, controller.signal)
      .then((payload) => {
        if (!cancelled) setSnapshot(payload);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") {
          setError("Structure labels timed out");
        } else {
          setError(err instanceof Error ? err.message : "Structure labels failed");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [symbol, ruleVersion]);

  return (
    <section className="space-y-3 border border-terminal-border p-3 text-[12px] text-terminal-text">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium">Closed structure labels</h2>
        <span className="text-terminal-muted">{snapshot?.rule_version ?? ruleVersion}</span>
        {ruleVersion === CANDIDATE ? (
          <span className="border border-terminal-border px-1 text-terminal-muted">Experimental</span>
        ) : null}
        <span className="text-terminal-muted">Not a trade signal</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <label>
          Symbol{" "}
          <select
            aria-label="Structure symbol"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
            className="border border-terminal-border bg-transparent px-1"
          >
            {["BTCUSDT", "LINKUSDT", "SOLUSDT", "TRXUSDT"].map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
        </label>
        <label>
          Rule{" "}
          <select
            aria-label="Structure rule"
            value={ruleVersion}
            onChange={(event) => setRuleVersion(event.target.value)}
            className="border border-terminal-border bg-transparent px-1"
          >
            <option value={BASELINE}>Baseline</option>
            <option value={CANDIDATE}>Experimental distinct pivots</option>
          </select>
        </label>
      </div>
      {loading ? (
        <p role="status">Loading structure labels</p>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      {snapshot?.process_id ? (
        <p className="text-terminal-muted">Backend process {snapshot.process_id}</p>
      ) : null}
      {snapshot
        ? TIMEFRAMES.map((timeframe) => {
            const frame = snapshot.timeframes[timeframe];
            if (!frame) return null;
            const latest = frame.latest;
            return (
              <article key={timeframe} className="space-y-1 border-t border-terminal-border pt-2">
                <div className="flex flex-wrap gap-2">
                  <strong>{timeframe}</strong>
                  <span>{latest?.state ?? "—"}</span>
                  {latest?.initialization === "warming_up" || latest?.initialization === "gap_reset" ? (
                    <span className="border border-terminal-border px-1">Insufficient data</span>
                  ) : null}
                  {latest?.ambiguity_flags?.length ? (
                    <span className="border border-terminal-border px-1">Ambiguous</span>
                  ) : null}
                </div>
                <p>{latest?.explanation ?? latest?.reason ?? "—"}</p>
                <p className="text-terminal-muted">
                  Range rule: {frame.range_explanation}. Display {frame.coverage.start_open} to{" "}
                  {frame.coverage.end_known_at}. Calculation history{" "}
                  {frame.coverage.calculation_start_open ?? "same window"} (
                  {frame.coverage.calculation_bars ?? frame.denominator} bars). Counts use the display
                  denominator {frame.denominator}. Fingerprint {frame.dataset_fingerprint}. Warm-up{" "}
                  {frame.coverage.warmup_bars} of {frame.denominator} bars.
                </p>
                <SwingLine label="Designated" swing={latest?.designated_level ?? null} />
                <SwingLine label="Support" swing={latest?.supporting_swing ?? null} />
                <SwingLine label="Resistance" swing={latest?.resisting_swing ?? null} />
                <div className="font-mono text-[11px] text-terminal-muted">
                  {STATES.map((state) => (
                    <span key={state} className="mr-3">
                      {state} {frame.counts[state] ?? 0} ({pct(frame.counts[state] ?? 0, frame.denominator)})
                    </span>
                  ))}
                </div>
              </article>
            );
          })
        : null}
    </section>
  );
}
