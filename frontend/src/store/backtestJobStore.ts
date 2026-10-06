import { create } from "zustand";
import {
  cancelBacktestJob,
  fetchBacktestJobStatus,
  startBacktestJob,
  type BacktestJob,
} from "../api/client";

type BacktestStore = {
  job: BacktestJob | null;
  error: string | null;
  /** True while a start request is in flight or a job is running */
  active: boolean;
  /** True only while POST /start is in flight — blocks poll from racing */
  starting: boolean;
  /** Bumped on each start so in-flight polls cannot overwrite newer state */
  _gen: number;
  setJob: (job: BacktestJob | null) => void;
  setError: (error: string | null) => void;
  poll: () => Promise<void>;
  start: (body: {
    symbols: string[];
    timeframes: string[];
    direction?: "LONG" | "SHORT";
    combination_id?: string;
    strategy_id?: string;
    combo_version?: string;
    setup_timeframe?: string;
    risk_mode?: string;
    research_risk_override?: boolean;
    limit?: number;
    risk_usd?: number;
    principal_usd?: number;
    leverage?: number;
    taker_fee_pct?: number;
    maker_fee_pct?: number;
    include_trades?: boolean;
    start_date?: string;
    end_date?: string;
  }) => Promise<void>;
  cancel: () => Promise<void>;
};

function optimisticJob(body: {
  symbols: string[];
  timeframes: string[];
  direction?: "LONG" | "SHORT";
  combination_id?: string;
  strategy_id?: string;
  combo_version?: string;
  setup_timeframe?: string;
  risk_mode?: string;
  research_risk_override?: boolean;
  limit?: number;
  risk_usd?: number;
  principal_usd?: number;
  leverage?: number;
  taker_fee_pct?: number;
  maker_fee_pct?: number;
  include_trades?: boolean;
  start_date?: string;
  end_date?: string;
}): BacktestJob {
  const total = body.symbols.length * body.timeframes.length;
  return {
    status: "running",
    symbols: body.symbols,
    timeframes: body.timeframes,
    direction: body.direction,
    combination_id: body.combination_id,
    strategy_id: body.strategy_id,
    combo_version: body.combo_version,
    setup_timeframe: body.setup_timeframe,
    risk_mode: body.risk_mode,
    limit: body.limit,
    risk_usd: body.risk_usd,
    principal_usd: body.principal_usd,
    leverage: body.leverage,
    taker_fee_pct: body.taker_fee_pct,
    maker_fee_pct: body.maker_fee_pct,
    include_trades: body.include_trades,
    start_date: body.start_date ?? null,
    end_date: body.end_date ?? null,
    short_status:
      body.direction === "SHORT" ? "RESEARCH_ONLY" : "RESEARCH_ENABLED",
    paper_trade_created: false,
    live_trade_created: false,
    telegram_sent: false,
    total_cells: total,
    done_cells: 0,
    pct: 0,
    progress_percent: 0,
    phase: "JOB_CREATED",
    bars_processed: 0,
    total_bars: 0,
    trades_generated: 0,
    current: "Starting…",
    rows: [],
  };
}

export const useBacktestJobStore = create<BacktestStore>((set, get) => ({
  job: null,
  error: null,
  active: false,
  starting: false,
  _gen: 0,
  setJob: (job) =>
    set({
      job,
      active: job?.status === "running",
    }),
  setError: (error) => set({ error }),
  poll: async () => {
    if (get().starting) return;
    const gen = get()._gen;
    try {
      const st = await fetchBacktestJobStatus();
      if (get().starting || get()._gen !== gen) return;

      if (!st.status || st.status === "idle") {
        // API restart / no job: clear optimistic "running" so the UI unlocks.
        if (get().active || get().job?.status === "running") {
          set({ active: false, job: null });
        }
        return;
      }

      const cur = get().job;
      if (
        cur?.status === "running" &&
        st.status !== "running" &&
        (!st.job_id || !cur.job_id || st.job_id !== cur.job_id)
      ) {
        return;
      }

      const terminal =
        st.status === "done" ||
        st.status === "error" ||
        st.status === "cancelled" ||
        st.status === "stalled" ||
        st.status === "idle";
      set({
        job: st,
        active: st.status === "running",
        error:
          st.status === "error" || st.status === "stalled"
            ? st.error || (st.status === "stalled" ? "Backtest stalled (no heartbeat)" : "Backtest failed")
            : get().error,
      });
      if (terminal && st.status !== "idle") {
        // Explicit stop — poller also gates on active/running.
        set({ active: false });
      }
    } catch {
      /* ignore transient poll errors */
    }
  },
  start: async (body) => {
    const gen = get()._gen + 1;
    set({
      error: null,
      active: true,
      starting: true,
      _gen: gen,
      job: optimisticJob(body),
    });
    try {
      const res = await startBacktestJob(body);
      if (get()._gen !== gen) return;
      if (res.status === "BUSY") {
        set({
          error: res.error || "Another backtest is already running",
          job: res.job || get().job,
          active: res.job?.status === "running",
          starting: false,
        });
        return;
      }
      if (res.status === "ERROR") {
        set({
          error: res.error_code || res.error || "Failed to start backtest",
          active: false,
          starting: false,
          // Clear optimistic running job so Run stays usable.
          job: null,
        });
        return;
      }
      if (res.job) {
        set({
          job: res.job,
          active: res.job.status === "running",
          starting: false,
        });
      } else {
        set({ starting: false });
      }
    } catch (e) {
      if (get()._gen !== gen) return;
      const raw = e instanceof Error ? e.message : "Failed to start backtest";
      const msg =
        /failed to fetch|networkerror|load failed/i.test(raw)
          ? "Backend unreachable (Failed to fetch). Restart API on :8000 and retry."
          : raw;
      set({
        error: msg,
        active: false,
        starting: false,
        job: null,
      });
    }
  },
  cancel: async () => {
    try {
      const res = await cancelBacktestJob();
      if (res.job) {
        set({
          job: res.job,
          active: res.job.status === "running",
        });
      } else {
        set({ active: false });
      }
    } catch (e) {
      set({
        error: e instanceof Error ? e.message : "Cancel failed",
      });
    }
  },
}));

/** App-level poller — keeps status fresh across route changes. */
export function startBacktestBackgroundPoller(): () => void {
  let timer: number | null = null;
  let stopped = false;
  let inFlight = false;

  const tick = async () => {
    if (stopped || inFlight) return;
    const { active, job, starting, poll } = useBacktestJobStore.getState();
    const shouldPoll =
      !starting && (active || job?.status === "running");
    if (!shouldPoll) return;
    inFlight = true;
    try {
      await poll();
    } finally {
      inFlight = false;
    }
  };

  // Initial restore (in case user refreshed mid-job)
  void useBacktestJobStore.getState().poll();

  timer = window.setInterval(() => {
    void tick();
  }, 750);

  return () => {
    stopped = true;
    if (timer != null) window.clearInterval(timer);
  };
}
