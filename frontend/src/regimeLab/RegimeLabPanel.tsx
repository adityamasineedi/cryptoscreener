/** Regime Lab: regime-routed rules-based strategies, backtest and walk-forward.

Research only. Signals fire on bar close and fill at the next open, with fees and
per-tier slippage. Safeguards are optional and start off, so a plain run is the
original plan and a "recommended" run shows what the protections change.
*/

import { useCallback, useEffect, useMemo, useState } from "react";
import { EquityChart } from "./EquityChart";
import {
  fetchJob,
  fetchJobs,
  fetchLabConfig,
  startRun,
  type Fold,
  type GroupRow,
  type Job,
  type JobRow,
  type LabConfig,
  type Mode,
  type RunRequest,
  type RunResult,
  type SafeguardValues,
  type Tier,
  type Trade,
} from "./client";

const LAST_JOB_KEY = "regimeLab.lastJob";
const TRADE_ROWS = 300;

type FieldDef = { key: string; label: string; hint?: string };

const SAFEGUARD_GROUPS: { title: string; fields: FieldDef[] }[] = [
  {
    title: "Regime lag",
    fields: [
      { key: "regime_method", label: "Regime method" },
      { key: "adx_enter", label: "Hysteresis: trend starts ADX >" },
      { key: "adx_exit", label: "Hysteresis: trend ends ADX <" },
      { key: "confirm_bars", label: "Bars to confirm a regime change" },
      { key: "transition_size", label: "Transition size", hint: "0 = flat" },
      { key: "daily_regime", label: "Daily chart sets the regime" },
      { key: "perf_cut", label: "Cold-strategy size", hint: "0 = off" },
      { key: "perf_loss_streak", label: "Cold after N losses in a row" },
      { key: "perf_window", label: "Rolling expectancy window (trades)" },
    ],
  },
  {
    title: "Chop",
    fields: [
      { key: "chop_filter", label: "Flat when ER low and ATR% high" },
      { key: "er_period", label: "Efficiency Ratio period" },
      { key: "er_min", label: "ER below this is chop" },
      { key: "cooldown_stops", label: "Cooldown after N stops on a coin", hint: "0 = off" },
      { key: "cooldown_bars", label: "Cooldown length (bars)" },
      { key: "max_entries_per_day", label: "Max entries per day", hint: "0 = off" },
    ],
  },
  {
    title: "Crashes and loss limits",
    fields: [
      { key: "crash_filter", label: "Crash filter" },
      { key: "crash_range_atr", label: "Coin pause: bar range > N × ATR" },
      { key: "crash_btc_drop", label: "Global pause: BTC bar low below open by", hint: "fraction" },
      { key: "crash_pause_bars", label: "Pause length (bars)" },
      { key: "dip_confirm", label: "Dip buys wait for a turn" },
      { key: "dip_confirm_bars", label: "Bars to wait for the turn" },
      { key: "slippage_buffer", label: "Size on stop + slippage + fees" },
      { key: "daily_loss_limit", label: "Daily loss limit", hint: "fraction, 0 = off" },
      { key: "daily_loss_basis", label: "Daily loss counts", hint: "equity includes open profit given back" },
      { key: "kill_drawdown", label: "Kill switch drawdown", hint: "fraction, 0 = off" },
    ],
  },
  {
    title: "BTC correlation and heat",
    fields: [
      { key: "heat_cap", label: "Max total open risk", hint: "fraction of equity, 0 = off" },
      { key: "max_positions", label: "Max open positions", hint: "0 = off" },
      { key: "corr_threshold", label: "30d BTC correlation counted as one trade", hint: "0 = off" },
      { key: "max_correlated", label: "Max same-side correlated positions" },
    ],
  },
  {
    title: "Liquidity and walk-forward",
    fields: [
      { key: "max_volume_participation", label: "Max order / avg hourly $ volume", hint: "fraction, 0 = off" },
      { key: "wf_selection", label: "Walk-forward parameter pick" },
    ],
  },
];

const RULE_FIELDS: FieldDef[] = [
  { key: "donchian_shorts", label: "Donchian shorts" },
  { key: "donchian_short_filter", label: "Shorts need 55-bar low + ADX > 25 + below 200 SMA" },
  { key: "donchian_entry_mode", label: "Donchian entry" },
  { key: "retest_bars", label: "Bars to wait for a retest" },
  { key: "rsi_exit_mode", label: "RSI(2) exit", hint: "sma = back across SMA(5)" },
  { key: "rsi_exit_level", label: "RSI(2) exit level" },
  { key: "rsi_max_bars", label: "RSI(2) time stop (bars)" },
];

/** "BTCUSDT 0.05, ETHUSDT 0.05" (percent) -> {BTCUSDT: 0.0005, ...} */
function parseSlippage(text: string): Record<string, number> {
  const out: Record<string, number> = {};
  const parts = text.toUpperCase().split(/[\s,;=:]+/).filter(Boolean);
  for (let k = 0; k + 1 < parts.length; k += 2) {
    const n = Number(parts[k + 1]);
    if (Number.isFinite(n)) out[parts[k]] = n / 100;
  }
  return out;
}

const SELECT_OPTIONS: Record<string, string[]> = {
  regime_method: ["threshold", "hysteresis", "vote"],
  wf_selection: ["best", "plateau"],
  daily_loss_basis: ["equity", "realized"],
  rsi_exit_mode: ["sma", "rsi"],
  donchian_entry_mode: ["breakout", "retest_level", "retest_mid"],
};

function f(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${f(value, digits)}%`;
}

function ratioPct(value: number | null | undefined): string {
  return value == null ? "—" : pct(value * 100);
}

function signClass(value: number | null | undefined): string {
  if (value == null || value === 0) return "";
  return value > 0 ? "text-terminal-up" : "text-terminal-down";
}

function day(iso: string): string {
  return iso.slice(0, 10);
}

function optionalNumber(text: string, scale = 1): number | null {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n / scale : null;
}

const inputClass =
  "h-8 w-full rounded border border-terminal-border bg-terminal-bg px-2 font-mono text-xs outline-none focus:border-terminal-accent";

export function RegimeLabPanel() {
  const [config, setConfig] = useState<LabConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);

  const [mode, setMode] = useState<Mode>("backtest");
  const [tier, setTier] = useState<Tier>("large");
  const [timeframe, setTimeframe] = useState<RunRequest["timeframe"]>("4h");
  const [start, setStart] = useState("2023-06-01");
  const [end, setEnd] = useState("");
  const [includeHoldout, setIncludeHoldout] = useState(false);
  const [symbolsText, setSymbolsText] = useState("");
  const [strategies, setStrategies] = useState<string[]>(["donchian", "rsi2", "bollinger"]);
  const [equity, setEquity] = useState("10000");
  const [riskPct, setRiskPct] = useState("");
  const [stopMult, setStopMult] = useState("");
  const [slipPct, setSlipPct] = useState("");
  const [feePct, setFeePct] = useState("0.04");
  const [maxLev, setMaxLev] = useState("2");
  const [btcGate, setBtcGate] = useState(true);
  const [shorts, setShorts] = useState<"tier" | "on" | "off">("tier");
  const [trainMonths, setTrainMonths] = useState("12");
  const [testMonths, setTestMonths] = useState("3");
  const [sgPreset, setSgPreset] = useState<"off" | "recommended">("off");
  const [sgOverrides, setSgOverrides] = useState<SafeguardValues>({});
  const [paramOverrides, setParamOverrides] = useState<SafeguardValues>({});
  const [btcDaily, setBtcDaily] = useState(false);
  const [slipText, setSlipText] = useState("");

  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(LAST_JOB_KEY);
    } catch {
      return null;
    }
  });
  const [runError, setRunError] = useState<string | null>(null);
  const [history, setHistory] = useState<JobRow[]>([]);

  useEffect(() => {
    fetchLabConfig()
      .then(setConfig)
      .catch((err: Error) => setConfigError(err.message));
  }, []);

  const refreshHistory = useCallback(() => {
    fetchJobs()
      .then((body) => setHistory(body.jobs))
      .catch(() => setHistory([]));
  }, []);

  useEffect(() => refreshHistory(), [refreshHistory]);

  useEffect(() => {
    if (!jobId) return;
    try {
      localStorage.setItem(LAST_JOB_KEY, jobId);
    } catch {
      /* ignore */
    }
    let alive = true;
    let timer: number | undefined;
    const poll = () => {
      fetchJob(jobId)
        .then((body) => {
          if (!alive) return;
          setJob(body);
          if (body.status === "running") {
            timer = window.setTimeout(poll, 1000);
          } else {
            refreshHistory();
          }
        })
        .catch((err: Error) => {
          if (!alive) return;
          setJob(null);
          setRunError(err.message);
        });
    };
    poll();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [jobId, refreshHistory]);

  const tierCfg = config?.tiers[tier];
  const sgValues: SafeguardValues = useMemo(
    () => ({ ...(config?.safeguards[sgPreset] ?? {}), ...sgOverrides }),
    [config, sgPreset, sgOverrides],
  );
  const running = job?.status === "running";

  function submit() {
    setRunError(null);
    const req: RunRequest = {
      mode,
      tier,
      timeframe,
      symbols: symbolsText.trim()
        ? symbolsText.split(/[\s,]+/).filter(Boolean).map((s) => s.toUpperCase())
        : null,
      start,
      end: end || null,
      include_holdout: includeHoldout,
      strategies,
      initial_equity: Number(equity) || 10000,
      risk_pct: optionalNumber(riskPct, 100),
      stop_atr_mult: optionalNumber(stopMult),
      slippage: optionalNumber(slipPct, 100),
      fee_rate: (optionalNumber(feePct, 100) ?? 0.0004),
      max_leverage: Number(maxLev) || 2,
      btc_gate: btcGate,
      allow_short: shorts === "tier" ? null : shorts === "on",
      train_months: Number(trainMonths) || 12,
      test_months: Number(testMonths) || 3,
      safeguard_preset: sgPreset,
      safeguards: sgOverrides,
      params: paramOverrides,
      symbol_slippage: parseSlippage(slipText),
      symbol_timeframes: btcDaily ? { BTCUSDT: "1d" } : {},
    };
    startRun(req)
      .then(({ job_id }) => {
        setJob(null);
        setJobId(job_id);
        refreshHistory();
      })
      .catch((err: Error) => setRunError(err.message));
  }

  function toggleStrategy(id: string) {
    setStrategies((cur) => (cur.includes(id) ? cur.filter((s) => s !== id) : [...cur, id]));
  }

  function setSg(key: string, value: number | boolean | string) {
    setSgOverrides((cur) => ({ ...cur, [key]: value }));
  }

  return (
    <section className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-terminal-text" data-testid="regime-lab">
      <header>
        <h1 className="font-display text-xl">Regime Lab</h1>
        <p className="mt-1 max-w-4xl text-sm text-terminal-muted">
          A regime classifier (200 SMA, ADX, ATR percentile) decides which strategies may trade each bar.
          Signals fire on the bar close and fill at the next open, with fees, per-tier slippage and ATR stops
          that fill at the open when price gaps through. Research only: no orders are placed.
        </p>
        {configError && <p className="mt-2 text-sm text-terminal-down">Config: {configError}</p>}
      </header>

      {config && (
        <div className="grid gap-3 rounded border border-terminal-border bg-terminal-panel/60 p-3 text-xs md:grid-cols-4">
          {Object.entries(config.regimes).map(([name, rule]) => (
            <div key={name}>
              <div className="font-mono text-[11px] text-terminal-accent">{name}</div>
              <div className="text-terminal-muted">{rule}</div>
              <div className="mt-0.5 text-terminal-muted">
                Runs:{" "}
                {Object.entries(config.routing)
                  .filter(([, m]) => m[name]?.length)
                  .map(([s, m]) => `${s} ${m[name].join("/").toLowerCase()}`)
                  .join(", ") || "nothing yet"}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-4 term-lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-3 rounded border border-terminal-border p-3">
          <h2 className="text-xs uppercase tracking-wide text-terminal-muted">Run</h2>
          <div className="flex flex-wrap gap-1">
            {(["backtest", "walk_forward"] as Mode[]).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`h-8 rounded border px-3 text-xs ${
                  mode === m
                    ? "border-terminal-accent bg-terminal-accent/15"
                    : "border-terminal-border text-terminal-muted hover:text-terminal-text"
                }`}
              >
                {m === "backtest" ? "Backtest" : "Walk-forward"}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Labeled label="Tier">
              <select className={inputClass} value={tier} onChange={(e) => setTier(e.target.value as Tier)}>
                {config
                  ? Object.values(config.tiers).map((t) => (
                      <option key={t.name} value={t.name}>
                        {t.label} (rank {t.rank_min}–{t.rank_max})
                      </option>
                    ))
                  : null}
              </select>
            </Labeled>
            <Labeled label="Timeframe">
              <select
                className={inputClass}
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value as RunRequest["timeframe"])}
              >
                {(config?.timeframes ?? ["4h"]).map((tf) => (
                  <option key={tf}>{tf}</option>
                ))}
              </select>
            </Labeled>
            <Labeled label="Start">
              <input type="date" className={inputClass} value={start} onChange={(e) => setStart(e.target.value)} />
            </Labeled>
            <Labeled label="End (blank = latest)">
              <input type="date" className={inputClass} value={end} onChange={(e) => setEnd(e.target.value)} />
            </Labeled>
          </div>
          <Labeled label="Symbols (blank = top coins by liquidity before the start date)">
            <input
              className={inputClass}
              value={symbolsText}
              placeholder="BTCUSDT ETHUSDT SOLUSDT"
              onChange={(e) => setSymbolsText(e.target.value)}
            />
          </Labeled>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            {config?.strategies.map((s) => {
              const allowed = tierCfg?.strategies.includes(s.id) ?? true;
              return (
                <label key={s.id} className={`flex items-center gap-1.5 ${allowed ? "" : "opacity-40"}`}>
                  <input
                    type="checkbox"
                    checked={strategies.includes(s.id)}
                    onChange={() => toggleStrategy(s.id)}
                  />
                  {s.label}
                  {!allowed && " (not in this tier)"}
                </label>
              );
            })}
          </div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Labeled label="Equity">
              <input className={inputClass} value={equity} onChange={(e) => setEquity(e.target.value)} />
            </Labeled>
            <Labeled label="Risk per trade %">
              <input
                className={inputClass}
                value={riskPct}
                placeholder={tierCfg ? `${tierCfg.risk_pct * 100}` : ""}
                onChange={(e) => setRiskPct(e.target.value)}
              />
            </Labeled>
            <Labeled label="Stop × ATR">
              <input
                className={inputClass}
                value={stopMult}
                placeholder={tierCfg ? `${tierCfg.stop_atr_mult}` : ""}
                onChange={(e) => setStopMult(e.target.value)}
              />
            </Labeled>
            <Labeled label="Slippage % per fill">
              <input
                className={inputClass}
                value={slipPct}
                placeholder={tierCfg ? `${tierCfg.slippage * 100}` : ""}
                onChange={(e) => setSlipPct(e.target.value)}
              />
            </Labeled>
            <Labeled label="Fee % per fill">
              <input className={inputClass} value={feePct} onChange={(e) => setFeePct(e.target.value)} />
            </Labeled>
            <Labeled label="Max leverage">
              <input className={inputClass} value={maxLev} onChange={(e) => setMaxLev(e.target.value)} />
            </Labeled>
            <Labeled label="Shorts">
              <select
                className={inputClass}
                value={shorts}
                onChange={(e) => setShorts(e.target.value as "tier" | "on" | "off")}
              >
                <option value="tier">Tier default ({tierCfg?.allow_short ? "on" : "off"})</option>
                <option value="on">On</option>
                <option value="off">Off</option>
              </select>
            </Labeled>
            <Labeled label="BTC regime gate">
              <label className="flex h-8 items-center gap-1.5 text-xs">
                <input type="checkbox" checked={btcGate} onChange={(e) => setBtcGate(e.target.checked)} />
                Gate alts by BTC
              </label>
            </Labeled>
          </div>
          {mode === "walk_forward" && (
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Labeled label="Train months">
                <input className={inputClass} value={trainMonths} onChange={(e) => setTrainMonths(e.target.value)} />
              </Labeled>
              <Labeled label="Test months">
                <input className={inputClass} value={testMonths} onChange={(e) => setTestMonths(e.target.value)} />
              </Labeled>
              <p className="col-span-2 self-end text-[11px] text-terminal-muted">
                {config?.walk_forward_grid_size ?? "—"} parameter sets are scored on each train window; the
                pick runs untouched on the next test window.
              </p>
            </div>
          )}
          {config && (
            <fieldset className="space-y-1.5 rounded border border-terminal-border/70 p-2">
              <legend className="px-1 text-[11px] text-terminal-accent">Strategy rules</legend>
              <div className="grid gap-x-4 gap-y-1.5 md:grid-cols-2">
                {RULE_FIELDS.map((fd) => (
                  <SafeguardField
                    key={fd.key}
                    def={fd}
                    value={paramOverrides[fd.key] ?? config.defaults.params[fd.key]}
                    changed={fd.key in paramOverrides}
                    onChange={(v) => setParamOverrides((cur) => ({ ...cur, [fd.key]: v }))}
                  />
                ))}
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={btcDaily} onChange={(e) => setBtcDaily(e.target.checked)} />
                  <span className="text-[11px] text-terminal-muted">Trade BTC on 1d bars</span>
                </label>
              </div>
              <Labeled label="Per-symbol slippage % (e.g. BTCUSDT 0.05 ETHUSDT 0.05 XRPUSDT 0.15)">
                <input className={inputClass} value={slipText} onChange={(e) => setSlipText(e.target.value)} />
              </Labeled>
            </fieldset>
          )}
          <label className="flex items-center gap-1.5 text-xs text-terminal-muted">
            <input type="checkbox" checked={includeHoldout} onChange={(e) => setIncludeHoldout(e.target.checked)} />
            Include the reserved holdout (from {config ? day(config.holdout_start) : "—"}). Leave this off until
            the final check.
          </label>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={submit}
              disabled={running || !config || strategies.length === 0}
              className="h-9 rounded bg-terminal-accent px-4 text-sm font-medium text-white disabled:opacity-50"
            >
              {running ? "Running…" : mode === "backtest" ? "Run backtest" : "Run walk-forward"}
            </button>
            {running && job && (
              <div className="flex min-w-0 flex-1 items-center gap-2 text-xs text-terminal-muted">
                <div className="h-1.5 w-40 overflow-hidden rounded bg-terminal-border">
                  <div className="h-full bg-terminal-accent" style={{ width: `${job.progress * 100}%` }} />
                </div>
                {job.stage}
              </div>
            )}
          </div>
          {runError && <p className="text-sm text-terminal-down">{runError}</p>}
        </div>

        <div className="space-y-3 rounded border border-terminal-border p-3">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="mr-2 text-xs uppercase tracking-wide text-terminal-muted">Safeguards</h2>
            {(["off", "recommended"] as const).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => {
                  setSgPreset(p);
                  setSgOverrides({});
                }}
                className={`h-7 rounded border px-3 text-xs ${
                  sgPreset === p && Object.keys(sgOverrides).length === 0
                    ? "border-terminal-accent bg-terminal-accent/15"
                    : "border-terminal-border text-terminal-muted hover:text-terminal-text"
                }`}
              >
                {p === "off" ? "Off (plan as written)" : "Recommended"}
              </button>
            ))}
            {Object.keys(sgOverrides).length > 0 && (
              <span className="text-[11px] text-terminal-warn">
                custom: {Object.keys(sgOverrides).length} change(s) on {sgPreset}
              </span>
            )}
          </div>
          {config && (
            <div className="grid gap-3 md:grid-cols-2">
              {SAFEGUARD_GROUPS.map((group) => (
                <fieldset key={group.title} className="space-y-1.5 rounded border border-terminal-border/70 p-2">
                  <legend className="px-1 text-[11px] text-terminal-accent">{group.title}</legend>
                  {group.fields.map((fd) => (
                    <SafeguardField
                      key={fd.key}
                      def={fd}
                      value={sgValues[fd.key]}
                      changed={fd.key in sgOverrides}
                      onChange={(v) => setSg(fd.key, v)}
                    />
                  ))}
                </fieldset>
              ))}
            </div>
          )}
          {config && (
            <p className="text-[11px] text-terminal-muted">
              Not modeled: {config.not_modeled.join("; ")}. Exchange-side safety (API key scope, retries,
              reconciliation, spread checks) belongs in the live bot.
            </p>
          )}
        </div>
      </div>

      <RecentRuns rows={history} activeId={jobId} onPick={setJobId} />

      {job?.status === "error" && (
        <p className="rounded border border-terminal-down/50 p-3 text-sm text-terminal-down">{job.error}</p>
      )}
      {job?.status === "done" && job.result && <Results job={job} result={job.result} />}
    </section>
  );
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block min-w-0 space-y-1">
      <span className="block truncate text-[10px] uppercase tracking-wide text-terminal-muted">{label}</span>
      {children}
    </label>
  );
}

function SafeguardField({
  def,
  value,
  changed,
  onChange,
}: {
  def: FieldDef;
  value: number | boolean | string | undefined;
  changed: boolean;
  onChange: (v: number | boolean | string) => void;
}) {
  const label = (
    <span className={`min-w-0 flex-1 text-[11px] ${changed ? "text-terminal-warn" : "text-terminal-muted"}`}>
      {def.label}
      {def.hint && <span className="opacity-60"> ({def.hint})</span>}
    </span>
  );
  if (typeof value === "boolean") {
    return (
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
        {label}
      </label>
    );
  }
  if (SELECT_OPTIONS[def.key]) {
    return (
      <label className="flex items-center gap-2">
        {label}
        <select
          className="h-7 w-28 rounded border border-terminal-border bg-terminal-bg px-1 font-mono text-[11px]"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        >
          {SELECT_OPTIONS[def.key].map((o) => (
            <option key={o}>{o}</option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label className="flex items-center gap-2">
      {label}
      <input
        type="number"
        step="any"
        className="h-7 w-20 rounded border border-terminal-border bg-terminal-bg px-1 text-right font-mono text-[11px]"
        value={value == null ? "" : String(value)}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (e.target.value !== "" && Number.isFinite(n)) onChange(n);
        }}
      />
    </label>
  );
}

function RecentRuns({
  rows,
  activeId,
  onPick,
}: {
  rows: JobRow[];
  activeId: string | null;
  onPick: (id: string) => void;
}) {
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto rounded border border-terminal-border">
      <div className="px-3 pt-2 text-xs uppercase tracking-wide text-terminal-muted">
        Recent runs (kept in API memory until restart) — click to open, compare side by side
      </div>
      <table className="w-full text-left text-xs">
        <thead className="text-terminal-muted">
          <tr>
            <th className="px-3 py-1.5">Mode</th>
            <th>Tier / TF</th>
            <th>Safeguards</th>
            <th>Window</th>
            <th className="text-right">Return</th>
            <th className="text-right">Max DD</th>
            <th className="text-right">Trades</th>
            <th className="text-right">PF</th>
            <th className="pr-3 text-right">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const custom = Object.keys(r.request.safeguards ?? {}).length;
            return (
              <tr
                key={r.job_id}
                onClick={() => onPick(r.job_id)}
                className={`cursor-pointer border-t border-terminal-border hover:bg-white/5 ${
                  r.job_id === activeId ? "bg-terminal-accent/10" : ""
                }`}
              >
                <td className="px-3 py-1.5">{r.request.mode === "backtest" ? "Backtest" : "Walk-forward"}</td>
                <td>
                  {r.request.tier} / {r.request.timeframe}
                </td>
                <td>
                  {r.request.safeguard_preset}
                  {custom ? ` +${custom}` : ""}
                </td>
                <td className="font-mono">
                  {r.request.start} → {r.request.end ?? "latest"}
                </td>
                <td className={`text-right font-mono ${signClass(r.summary?.return_pct)}`}>
                  {pct(r.summary?.return_pct)}
                </td>
                <td className="text-right font-mono">{pct(r.summary?.max_drawdown_pct)}</td>
                <td className="text-right font-mono">{r.summary?.trades ?? "—"}</td>
                <td className="text-right font-mono">{f(r.summary?.profit_factor)}</td>
                <td className="pr-3 text-right">
                  {r.status === "running" ? `${Math.round(r.progress * 100)}%` : r.status}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const BREAKDOWNS: [string, string][] = [
  ["strategy_regime_side", "Strategy × regime × side"],
  ["strategy", "Strategy"],
  ["regime", "Regime at entry"],
  ["symbol", "Symbol"],
  ["exit_reason", "Exit reason"],
  ["year", "Year"],
];

function Results({ job, result }: { job: Job; result: RunResult }) {
  const s = result.summary;
  const [breakdown, setBreakdown] = useState("strategy_regime_side");
  const counters = Object.entries(result.counters).sort((a, b) => b[1] - a[1]);
  const isWf = job.request.mode === "walk_forward";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 text-sm">
        <h2 className="font-display text-lg">
          {isWf ? "Walk-forward (out-of-sample test windows only)" : "Backtest"}
        </h2>
        <span className="text-terminal-muted">
          {day(result.window.start)} → {day(result.window.end)} · {result.universe.symbols.join(", ")}
        </span>
      </div>
      <p className="text-xs text-terminal-muted">
        Risk {pct(result.effective.risk_pct * 100, 2)} · stop {result.effective.stop_atr_mult}×ATR · slippage{" "}
        {pct(result.effective.slippage * 100, 2)} · fee {pct(result.effective.fee_rate * 100, 2)} · shorts{" "}
        {result.effective.allow_short ? "on" : "off"} · BTC gate {result.effective.btc_gate ? "on" : "off"} · regime{" "}
        {String(result.effective.safeguards.regime_method)}
        {result.effective.safeguards.daily_regime ? " (daily)" : ""} · strategies{" "}
        {result.effective.strategies.join(", ")}
      </p>
      {result.kill_switch_at && (
        <p className="rounded border border-terminal-down/60 p-2 text-sm text-terminal-down">
          Kill switch tripped at {result.kill_switch_at}; no trades after that.
        </p>
      )}

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 term-lg:grid-cols-8">
        <Stat label="Net PnL" value={f(s.net_pnl)} tone={s.net_pnl} />
        <Stat label="Return" value={pct(s.return_pct)} tone={s.return_pct} />
        <Stat label="CAGR" value={pct(s.cagr_pct)} tone={s.cagr_pct} />
        <Stat label="Max drawdown" value={pct(s.max_drawdown_pct)} />
        <Stat label="Sharpe" value={f(s.sharpe)} />
        <Stat label="Profit factor" value={f(s.profit_factor)} />
        <Stat label="Trades" value={String(s.trades)} />
        <Stat label="Win rate" value={ratioPct(s.win_rate)} />
        <Stat label="Avg R" value={f(s.avg_r, 3)} tone={s.avg_r} />
        <Stat label="PnL / 30d" value={f(s.pnl_per_30d)} tone={s.pnl_per_30d} />
        <Stat label="Fees" value={f(s.fees)} />
        <Stat label="Slippage" value={f(s.slippage)} />
        <Stat label="Exposure" value={pct(s.exposure_pct)} />
        <Stat label="Days" value={f(s.days, 0)} />
        <Stat label="Live pause DD (1.5×)" value={pct(s.live_pause_drawdown_pct)} />
        <Stat label="Max DD $" value={f(s.max_drawdown_usd)} />
        <Stat label="Max losing streak" value={String(s.max_losing_streak ?? "—")} />
        <Stat label="Quick stops (≤2 bars)" value={String(s.quick_stops ?? "—")} />
        <Stat label="PnL without top 5" value={f(s.pnl_ex_top5)} tone={s.pnl_ex_top5} />
        {isWf && <Stat label="Positive folds" value={`${result.positive_folds} / ${result.folds?.length ?? 0}`} />}
      </div>

      <div className="rounded border border-terminal-border p-3">
        <div className="mb-2 text-xs uppercase tracking-wide text-terminal-muted">Equity (marked to market)</div>
        <EquityChart points={result.equity_curve} />
      </div>

      {result.monte_carlo && (
        <div className="rounded border border-terminal-border p-3 text-xs">
          <div className="mb-2 uppercase tracking-wide text-terminal-muted">
            Monte Carlo ({result.monte_carlo.runs} runs over {result.monte_carlo.trades} trades)
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            <div>
              Max drawdown with trades reshuffled: median {pct(result.monte_carlo.shuffle_max_dd_pct.p50)}, 95th{" "}
              {pct(result.monte_carlo.shuffle_max_dd_pct.p95)}, 99th {pct(result.monte_carlo.shuffle_max_dd_pct.p99)}
            </div>
            <div>
              Return with trades resampled: 5th {pct(result.monte_carlo.bootstrap_return_pct.p5)}, median{" "}
              {pct(result.monte_carlo.bootstrap_return_pct.p50)}, 95th {pct(result.monte_carlo.bootstrap_return_pct.p95)}
            </div>
            <div>Chance the resampled run loses money: {ratioPct(result.monte_carlo.prob_loss)}</div>
          </div>
        </div>
      )}

      {isWf && result.folds && <FoldsTable folds={result.folds} selection={result.selection} />}

      <div className="rounded border border-terminal-border">
        <div className="flex flex-wrap gap-1 border-b border-terminal-border p-2">
          {BREAKDOWNS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setBreakdown(key)}
              className={`h-7 rounded px-2.5 text-xs ${
                breakdown === key ? "bg-terminal-accent text-white" : "text-terminal-muted hover:bg-white/5"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <GroupTable rows={result.breakdowns[breakdown] ?? []} />
      </div>

      <div className="grid gap-4 term-lg:grid-cols-2">
        <div className="rounded border border-terminal-border p-3 text-xs">
          <div className="mb-2 uppercase tracking-wide text-terminal-muted">What the rules and safeguards did</div>
          {counters.length ? (
            <table className="w-full">
              <tbody>
                {counters.map(([k, v]) => (
                  <tr key={k} className="border-t border-terminal-border/60">
                    <td className="py-1">{k.replace(/_/g, " ")}</td>
                    <td className="text-right font-mono">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-terminal-muted">Nothing was blocked or capped.</p>
          )}
        </div>
        <UniverseTable result={result} />
      </div>

      <TradesTable trades={result.trades} total={result.trade_count} />
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: number | null }) {
  return (
    <div className="rounded border border-terminal-border px-3 py-2">
      <div className="truncate text-[10px] uppercase tracking-wide text-terminal-muted">{label}</div>
      <div className={`mt-1 font-mono text-sm ${signClass(tone)}`}>{value}</div>
    </div>
  );
}

function GroupTable({ rows }: { rows: GroupRow[] }) {
  if (!rows.length) return <p className="p-3 text-sm text-terminal-muted">No trades.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-terminal-muted">
          <tr>
            <th className="px-3 py-1.5">Group</th>
            <th className="text-right">Trades</th>
            <th className="text-right">Net PnL</th>
            <th className="text-right">Win rate</th>
            <th className="text-right">Avg R</th>
            <th className="text-right">PF</th>
            <th className="pr-3 text-right">Fees</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-terminal-border">
              <td className="px-3 py-1.5 font-mono">{r.key}</td>
              <td className="text-right font-mono">{r.trades}</td>
              <td className={`text-right font-mono ${signClass(r.net_pnl)}`}>{f(r.net_pnl)}</td>
              <td className="text-right font-mono">{ratioPct(r.win_rate)}</td>
              <td className={`text-right font-mono ${signClass(r.avg_r)}`}>{f(r.avg_r, 3)}</td>
              <td className="text-right font-mono">{f(r.profit_factor)}</td>
              <td className="pr-3 text-right font-mono">{f(r.fees)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FoldsTable({ folds, selection }: { folds: Fold[]; selection?: string }) {
  return (
    <div className="overflow-x-auto rounded border border-terminal-border">
      <div className="px-3 pt-2 text-xs uppercase tracking-wide text-terminal-muted">
        Folds · parameter pick: {selection === "plateau" ? "best neighbourhood (plateau)" : "best single point"}
      </div>
      <table className="w-full text-left text-xs">
        <thead className="text-terminal-muted">
          <tr>
            <th className="px-3 py-1.5">#</th>
            <th>Train</th>
            <th>Test</th>
            <th>Chosen params</th>
            <th className="text-right">Train ret</th>
            <th className="text-right">Test ret</th>
            <th className="text-right">Test DD</th>
            <th className="pr-3 text-right">Test trades</th>
          </tr>
        </thead>
        <tbody>
          {folds.map((r) => (
            <tr key={r.fold} className="border-t border-terminal-border">
              <td className="px-3 py-1.5">{r.fold}</td>
              <td className="font-mono">
                {day(r.train_start)} → {day(r.train_end)}
              </td>
              <td className="font-mono">
                {day(r.test_start)} → {day(r.test_end)}
              </td>
              <td className="font-mono">
                {typeof r.params === "string"
                  ? r.params
                  : Object.entries(r.params)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(" ")}
              </td>
              <td className={`text-right font-mono ${signClass(r.train_return_pct)}`}>{pct(r.train_return_pct)}</td>
              <td className={`text-right font-mono ${signClass(r.test_return_pct)}`}>{pct(r.test_return_pct)}</td>
              <td className="text-right font-mono">{pct(r.test_max_drawdown_pct)}</td>
              <td className="pr-3 text-right font-mono">{r.test_trades}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UniverseTable({ result }: { result: RunResult }) {
  const share = result.regime_share ?? {};
  return (
    <div className="overflow-x-auto rounded border border-terminal-border p-3 text-xs">
      <div className="mb-2 uppercase tracking-wide text-terminal-muted">Universe and time in each regime</div>
      <table className="w-full text-left">
        <thead className="text-terminal-muted">
          <tr>
            <th className="py-1">Symbol</th>
            <th>Liquidity rank</th>
            <th>First regime bar</th>
            <th className="text-right">Up</th>
            <th className="text-right">Down</th>
            <th className="text-right">Side</th>
            <th className="text-right">Chop</th>
            <th className="text-right">Trans</th>
          </tr>
        </thead>
        <tbody>
          {result.universe.coverage.map((c) => {
            const rank = result.universe.ranking.find((r) => r.symbol === c.symbol)?.rank;
            const sh = share[c.symbol] ?? {};
            return (
              <tr key={c.symbol} className="border-t border-terminal-border/60">
                <td className="py-1 font-mono">{c.symbol}</td>
                <td>{rank ?? "manual"}</td>
                <td className={c.late_start ? "text-terminal-warn" : ""}>
                  {c.note ?? (c.first_regime_bar ? day(c.first_regime_bar) : "never")}
                </td>
                <td className="text-right font-mono">{ratioPct(sh.UPTREND)}</td>
                <td className="text-right font-mono">{ratioPct(sh.DOWNTREND)}</td>
                <td className="text-right font-mono">{ratioPct(sh.SIDEWAYS)}</td>
                <td className="text-right font-mono">{ratioPct(sh.CHOPPY)}</td>
                <td className="text-right font-mono">{ratioPct(sh.TRANSITION)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!share || Object.keys(share).length === 0 ? (
        <p className="mt-2 text-terminal-muted">Regime shares are reported for single backtests.</p>
      ) : null}
    </div>
  );
}

function downloadTradesCsv(trades: Trade[]) {
  const head = ["symbol", "strategy", "side", "regime", "entry time", "exit time", "entry price",
    "exit price", "P&L", "R", "bars held", "exit reason"];
  const when = (iso: string) => iso.slice(0, 16).replace("T", " ");
  const lines = [...trades].reverse().map((t) =>
    [t.symbol, t.strategy, t.side, t.regime, when(t.entry_time), when(t.exit_time), t.entry_price.toFixed(4),
      t.exit_price.toFixed(4), t.net_pnl.toFixed(2), t.r_multiple.toFixed(2), t.bars_held, t.exit_reason].join(","),
  );
  const blob = new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "regime-lab-trades.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function TradesTable({ trades, total }: { trades: Trade[]; total: number }) {
  const rows = useMemo(() => [...trades].reverse().slice(0, TRADE_ROWS), [trades]);
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto rounded border border-terminal-border">
      <div className="px-3 pt-2 text-xs uppercase tracking-wide text-terminal-muted">
        Trades (latest {rows.length} of {total}){" "}
        <button
          type="button"
          onClick={() => downloadTradesCsv(trades)}
          className="ml-2 rounded border border-terminal-border px-2 py-0.5 normal-case text-terminal-text hover:bg-white/5"
        >
          Export CSV
        </button>
      </div>
      <table className="w-full text-left text-xs">
        <thead className="text-terminal-muted">
          <tr>
            <th className="px-3 py-1.5">Symbol</th>
            <th>Strategy</th>
            <th>Side</th>
            <th>Regime</th>
            <th>Entry</th>
            <th>Exit</th>
            <th className="text-right">Entry px</th>
            <th className="text-right">Exit px</th>
            <th className="text-right">Net</th>
            <th className="text-right">R</th>
            <th className="text-right">Bars</th>
            <th className="pr-3">Exit reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t, k) => (
            <tr key={`${t.symbol}-${t.entry_time}-${k}`} className="border-t border-terminal-border">
              <td className="px-3 py-1 font-mono">{t.symbol}</td>
              <td>{t.strategy}</td>
              <td className={t.side === "LONG" ? "text-terminal-up" : "text-terminal-down"}>{t.side}</td>
              <td>{t.regime}</td>
              <td className="font-mono">{t.entry_time.slice(0, 16).replace("T", " ")}</td>
              <td className="font-mono">{t.exit_time.slice(0, 16).replace("T", " ")}</td>
              <td className="text-right font-mono">{f(t.entry_price, 4)}</td>
              <td className="text-right font-mono">{f(t.exit_price, 4)}</td>
              <td className={`text-right font-mono ${signClass(t.net_pnl)}`}>{f(t.net_pnl)}</td>
              <td className={`text-right font-mono ${signClass(t.r_multiple)}`}>{f(t.r_multiple)}</td>
              <td className="text-right font-mono">{t.bars_held}</td>
              <td className="pr-3">{t.exit_reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
