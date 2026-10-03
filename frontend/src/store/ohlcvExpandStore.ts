import { create } from "zustand";
import {
  cancelOhlcvExpand,
  fetchOhlcvExpandStatus,
  startOhlcvExpand,
  type OhlcvExpandJob,
} from "../api/client";

type ExpandStore = {
  job: OhlcvExpandJob | null;
  error: string | null;
  /** True while a start request is in flight or a job is running */
  active: boolean;
  /** True only while POST /start is in flight — blocks poll from racing */
  starting: boolean;
  /** Bumped on each start so in-flight polls cannot overwrite newer state */
  _gen: number;
  setJob: (job: OhlcvExpandJob | null) => void;
  setError: (error: string | null) => void;
  poll: () => Promise<void>;
  start: (body: {
    symbols: string[];
    timeframes: string[];
    until?: string;
    refresh_tip?: boolean;
    max_pages?: number;
  }) => Promise<void>;
  cancel: () => Promise<void>;
};

function optimisticJob(body: {
  symbols: string[];
  timeframes: string[];
  until?: string;
  refresh_tip?: boolean;
  max_pages?: number;
}): OhlcvExpandJob {
  const total = body.symbols.length * body.timeframes.length;
  return {
    status: "running",
    symbols: body.symbols,
    timeframes: body.timeframes,
    until: body.until ?? null,
    refresh_tip: body.refresh_tip,
    max_pages: body.max_pages,
    total_cells: total,
    done_cells: 0,
    pct: 0,
    current: "Starting…",
    written_total: 0,
    results: [],
  };
}

export const useOhlcvExpandStore = create<ExpandStore>((set, get) => ({
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
      const st = await fetchOhlcvExpandStatus();
      // Start() bumped generation or a newer start began — drop this response.
      if (get().starting || get()._gen !== gen) return;

      if (!st.status || st.status === "idle") {
        if (get().active) set({ active: false });
        return;
      }

      // Stale poll of a finished job must not clobber an optimistic/running job
      // that has a different (or not-yet-known) job_id.
      const cur = get().job;
      if (
        cur?.status === "running" &&
        st.status !== "running" &&
        (!st.job_id || !cur.job_id || st.job_id !== cur.job_id)
      ) {
        return;
      }

      set({
        job: st,
        active: st.status === "running",
        error: st.status === "error" ? st.error || "Expand failed" : get().error,
      });
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
      const res = await startOhlcvExpand(body);
      if (get()._gen !== gen) return;
      if (res.status === "BUSY") {
        set({
          error: res.error || "Another expand is already running",
          job: res.job || get().job,
          active: res.job?.status === "running",
          starting: false,
        });
        return;
      }
      if (res.status === "ERROR") {
        set({
          error: res.error || "Failed to start expand",
          active: false,
          starting: false,
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
      set({
        error: e instanceof Error ? e.message : "Failed to start expand",
        active: false,
        starting: false,
      });
    }
  },
  cancel: async () => {
    try {
      const res = await cancelOhlcvExpand();
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
export function startOhlcvExpandBackgroundPoller(): () => void {
  let timer: number | null = null;
  let stopped = false;
  let inFlight = false;

  const tick = async () => {
    if (stopped || inFlight) return;
    const { active, job, starting, poll } = useOhlcvExpandStore.getState();
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
  void useOhlcvExpandStore.getState().poll();

  timer = window.setInterval(() => {
    void tick();
  }, 750);

  return () => {
    stopped = true;
    if (timer != null) window.clearInterval(timer);
  };
}
