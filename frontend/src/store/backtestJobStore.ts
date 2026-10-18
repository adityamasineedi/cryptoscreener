import { create } from "zustand";
import {
  cancelBacktestJob,
  fetchBacktestJobStatus,
  startBacktestJob,
  type BacktestJob,
} from "../api/client";

export type BacktestRunRequest = Parameters<typeof startBacktestJob>[0];

/** Failed polls in a row (750 ms apart) before a running job reports lost contact. */
export const POLL_FAILURE_NOTICE = 8;
const LOST_CONTACT_PREFIX = "Lost contact with the backtest API";

type BacktestStore = {
  job: BacktestJob | null;
  /** The request this screen sent for `job`. Null when the job was not started here. */
  request: BacktestRunRequest | null;
  /** True when `job` is not the result of this screen's last Run (reload, other tab, BUSY). */
  foreignJob: boolean;
  /** job_id returned by this screen's last successful start */
  ownJobId: string | null;
  error: string | null;
  /** True while a start request is in flight or a job is running */
  active: boolean;
  /** True only while POST /start is in flight — blocks poll from racing */
  starting: boolean;
  /** Bumped on each start so in-flight polls cannot overwrite newer state */
  _gen: number;
  setJob: (job: BacktestJob | null) => void;
  setError: (error: string | null) => void;
  /** Resolves true when the status request succeeded (the screen knows the server's job). */
  poll: () => Promise<boolean>;
  /** Job a BUSY reply pointed at; its "already running" error clears once it finishes. */
  busyJobId: string | null;
  /** Consecutive failed status polls; at POLL_FAILURE_NOTICE a running job says so. */
  pollFailures: number;
  start: (body: BacktestRunRequest) => Promise<void>;
  cancel: () => Promise<void>;
};

function optimisticJob(body: BacktestRunRequest): BacktestJob {
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
  request: null,
  foreignJob: false,
  ownJobId: null,
  error: null,
  busyJobId: null,
  pollFailures: 0,
  active: false,
  starting: false,
  _gen: 0,
  setJob: (job) =>
    set({
      job,
      active: job?.status === "running",
      request: null,
      foreignJob: false,
      ownJobId: null,
    }),
  setError: (error) => set({ error }),
  poll: async () => {
    if (get().starting) return false;
    const gen = get()._gen;
    try {
      const st = await fetchBacktestJobStatus();
      // Contact is back: drop the lost-contact notice (the status below may set another error).
      if (get().pollFailures || get().error?.startsWith(LOST_CONTACT_PREFIX)) {
        set({
          pollFailures: 0,
          ...(get().error?.startsWith(LOST_CONTACT_PREFIX) ? { error: null } : {}),
        });
      }
      if (get().starting || get()._gen !== gen) return true;

      if (!st.status || st.status === "idle") {
        // API restart / no job: clear optimistic "running" so the UI unlocks.
        if (get().active || get().job?.status === "running") {
          set({
            active: false,
            job: null,
            request: null,
            foreignJob: false,
            ownJobId: null,
          });
        }
        return true;
      }

      const cur = get().job;
      if (
        cur?.status === "running" &&
        st.status !== "running" &&
        (!st.job_id || !cur.job_id || st.job_id !== cur.job_id)
      ) {
        return true;
      }

      const terminal =
        st.status === "done" ||
        st.status === "error" ||
        st.status === "cancelled" ||
        st.status === "stalled" ||
        st.status === "idle";
      // Only the job this screen started keeps its request. A job restored
      // after a reload or started from another tab is foreign to the form.
      const { ownJobId, request, busyJobId } = get();
      const owned = ownJobId != null && st.job_id === ownJobId && request != null;
      // The "already running" error is about that job; once it ends the error is stale.
      const busyOver = terminal && busyJobId != null && st.job_id === busyJobId;
      set({
        job: st,
        active: st.status === "running",
        ...(owned ? {} : { foreignJob: true, request: null, ownJobId: null }),
        ...(busyOver ? { busyJobId: null } : {}),
        error:
          st.status === "error" || st.status === "stalled"
            ? st.error || (st.status === "stalled" ? "Backtest stalled (no heartbeat)" : "Backtest failed")
            : busyOver
              ? null
              : get().error,
      });
      if (terminal && st.status !== "idle") {
        // Explicit stop — poller also gates on active/running.
        set({ active: false });
      }
      return true;
    } catch (e) {
      // One failed poll is transient; a run of them while a job runs is a frozen screen.
      const failures = get().pollFailures + 1;
      set({ pollFailures: failures });
      if (failures === POLL_FAILURE_NOTICE && get().job?.status === "running") {
        const why = e instanceof Error ? e.message : "status request failed";
        set({
          error: `${LOST_CONTACT_PREFIX} (${why}). Still retrying; if the API restarted, this run is gone.`,
        });
      }
      return false;
    }
  },
  start: async (body) => {
    const gen = get()._gen + 1;
    set({
      error: null,
      busyJobId: null,
      active: true,
      starting: true,
      _gen: gen,
      job: optimisticJob(body),
      request: body,
      foreignJob: false,
      ownJobId: null,
    });
    try {
      const res = await startBacktestJob(body);
      if (get()._gen !== gen) return;
      if (res.status === "BUSY") {
        set({
          error: res.error || "Another backtest is already running",
          busyJobId: res.job?.job_id ?? null,
          job: res.job || get().job,
          active: res.job?.status === "running",
          starting: false,
          // The in-flight job is someone else's run, not the result of this click.
          foreignJob: true,
          request: null,
          ownJobId: null,
        });
        return;
      }
      if (res.status === "ERROR") {
        set({
          error: res.error || res.error_code || "Failed to start backtest",
          active: false,
          starting: false,
          // Clear optimistic running job so Run stays usable.
          job: null,
          request: null,
          foreignJob: false,
          ownJobId: null,
        });
        return;
      }
      if (res.job) {
        set({
          job: res.job,
          active: res.job.status === "running",
          starting: false,
          foreignJob: false,
          ownJobId: res.job.job_id ?? null,
        });
      } else {
        set({ starting: false });
      }
    } catch (e) {
      if (get()._gen !== gen) return;
      // AbortSignal.timeout rejects with a DOMException, which is not an Error everywhere.
      const errLike = e as { name?: unknown; message?: unknown } | null;
      const raw =
        typeof errLike?.message === "string" && errLike.message
          ? errLike.message
          : "Failed to start backtest";
      const timedOut =
        errLike?.name === "TimeoutError" || /signal timed out|timeouterror/i.test(raw);
      const msg = timedOut
        ? "Backtest API did not respond in time. The server is busy or still reloading — retry in a moment."
        : /failed to fetch|networkerror|load failed/i.test(raw)
          ? "Backend unreachable (Failed to fetch). Restart API on :8000 and retry."
          : raw;
      set({
        error: msg,
        active: false,
        starting: false,
        job: null,
        request: null,
        foreignJob: false,
        ownJobId: null,
      });
      if (timedOut) {
        // The server may have started the job after all: show it rather than lose it.
        try {
          const st = await fetchBacktestJobStatus();
          if (get()._gen === gen && !get().starting && st.status === "running") {
            set({
              job: st,
              active: true,
              foreignJob: true,
              error: `The start request timed out, but the server is running job ${st.job_id ?? "?"}. Its progress is shown here.`,
            });
          }
        } catch {
          /* still unreachable: the timeout message stands */
        }
      }
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
  // Until one status request succeeds, a job running on the server may be unknown here.
  let restored = false;
  let ticks = 0;
  const RESTORE_RETRY_TICKS = 7; // ~5 s at 750 ms

  const tick = async () => {
    if (stopped || inFlight) return;
    ticks += 1;
    const { active, job, starting, poll } = useBacktestJobStore.getState();
    const retryRestore = !restored && !job && ticks % RESTORE_RETRY_TICKS === 0;
    const shouldPoll =
      !starting && (active || job?.status === "running" || retryRestore);
    if (!shouldPoll) return;
    inFlight = true;
    try {
      if (await poll()) restored = true;
    } finally {
      inFlight = false;
    }
  };

  // Initial restore (in case user refreshed mid-job)
  inFlight = true;
  void useBacktestJobStore
    .getState()
    .poll()
    .then((ok) => {
      if (ok) restored = true;
    })
    .finally(() => {
      inFlight = false;
    });

  timer = window.setInterval(() => {
    void tick();
  }, 750);

  return () => {
    stopped = true;
    if (timer != null) window.clearInterval(timer);
  };
}
