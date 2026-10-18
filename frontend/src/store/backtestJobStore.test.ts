/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  startBacktestBackgroundPoller,
  useBacktestJobStore,
} from "./backtestJobStore";

const fetchBacktestJobStatus = vi.fn();
const startBacktestJob = vi.fn();
const cancelBacktestJob = vi.fn();

vi.mock("../api/client", () => ({
  fetchBacktestJobStatus: (...args: unknown[]) => fetchBacktestJobStatus(...args),
  startBacktestJob: (...args: unknown[]) => startBacktestJob(...args),
  cancelBacktestJob: (...args: unknown[]) => cancelBacktestJob(...args),
}));

const SHORT_REQUEST = {
  symbols: ["BTCUSDT"],
  timeframes: ["1h"],
  direction: "SHORT" as const,
  strategy_id: "COMBO_04_HTF_SHORT",
};

describe("backtestJobStore polling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useBacktestJobStore.setState({
      job: null,
      request: null,
      foreignJob: false,
      ownJobId: null,
      error: null,
      busyJobId: null,
      active: false,
      starting: false,
      _gen: 0,
    });
  });

  it("receives phase/progress fields from status poll", async () => {
    fetchBacktestJobStatus.mockResolvedValue({
      status: "running",
      job_id: "j1",
      total_cells: 1,
      done_cells: 0,
      pct: 12.5,
      progress_percent: 12.5,
      phase: "STRUCTURE_SCAN_HEARTBEAT",
      bars_processed: 10000,
      total_bars: 80000,
      trades_generated: 4,
      last_heartbeat: "2024-01-01T00:00:00+00:00",
      rows: [],
    });
    useBacktestJobStore.setState({ active: true, starting: false });
    await useBacktestJobStore.getState().poll();
    const job = useBacktestJobStore.getState().job;
    expect(job?.phase).toBe("STRUCTURE_SCAN_HEARTBEAT");
    expect(job?.bars_processed).toBe(10000);
    expect(job?.total_bars).toBe(80000);
    expect(job?.progress_percent).toBe(12.5);
    expect(useBacktestJobStore.getState().active).toBe(true);
  });

  it("stops polling activity on completion", async () => {
    fetchBacktestJobStatus.mockResolvedValue({
      status: "done",
      job_id: "j2",
      total_cells: 1,
      done_cells: 1,
      pct: 100,
      phase: "JOB_COMPLETED",
      rows: [],
    });
    useBacktestJobStore.setState({
      active: true,
      starting: false,
      job: {
        status: "running",
        job_id: "j2",
        total_cells: 1,
        done_cells: 0,
        rows: [],
      },
    });
    await useBacktestJobStore.getState().poll();
    expect(useBacktestJobStore.getState().active).toBe(false);
    expect(useBacktestJobStore.getState().job?.status).toBe("done");
  });

  it("does not start duplicate jobs while one is already running (BUSY)", async () => {
    startBacktestJob.mockResolvedValue({
      status: "BUSY",
      error: "Backtest already running (job_id=abc)",
      job: {
        status: "running",
        job_id: "abc",
        total_cells: 1,
        done_cells: 0,
        rows: [],
      },
    });
    await useBacktestJobStore.getState().start({
      symbols: ["BTCUSDT"],
      timeframes: ["15m"],
      direction: "LONG",
      start_date: "2022-10-01",
      end_date: "2024-12-31",
    });
    expect(useBacktestJobStore.getState().error).toMatch(/already running/i);
    expect(useBacktestJobStore.getState().job?.job_id).toBe("abc");
  });

  it("BUSY shows the in-flight job as foreign and drops the request", async () => {
    let seenWhileStarting: unknown = undefined;
    startBacktestJob.mockImplementation(async () => {
      seenWhileStarting = useBacktestJobStore.getState().request;
      return {
        status: "BUSY",
        error: "Backtest already running (job_id=abc)",
        job: { status: "running", job_id: "abc", direction: "LONG", rows: [] },
      };
    });
    useBacktestJobStore.setState({ foreignJob: true, ownJobId: "old" });
    await useBacktestJobStore.getState().start(SHORT_REQUEST);
    // The request is stored before the API answers.
    expect(seenWhileStarting).toEqual(SHORT_REQUEST);
    const state = useBacktestJobStore.getState();
    expect(state.job?.job_id).toBe("abc");
    expect(state.foreignJob).toBe(true);
    expect(state.request).toBeNull();
    expect(state.ownJobId).toBeNull();
    expect(state.active).toBe(true);
  });

  it("STARTED records the returned job_id and keeps the request", async () => {
    startBacktestJob.mockResolvedValue({
      status: "STARTED",
      job: { status: "running", job_id: "mine", direction: "SHORT", rows: [] },
    });
    useBacktestJobStore.setState({ foreignJob: true, ownJobId: "old" });
    await useBacktestJobStore.getState().start(SHORT_REQUEST);
    const state = useBacktestJobStore.getState();
    expect(state.ownJobId).toBe("mine");
    expect(state.foreignJob).toBe(false);
    expect(state.request).toEqual(SHORT_REQUEST);
    expect(state.job?.job_id).toBe("mine");
    expect(state.active).toBe(true);
  });

  it("poll keeps the request while the status is the job this screen started", async () => {
    startBacktestJob.mockResolvedValue({
      status: "STARTED",
      job: { status: "running", job_id: "mine", rows: [] },
    });
    await useBacktestJobStore.getState().start(SHORT_REQUEST);
    fetchBacktestJobStatus.mockResolvedValue({
      status: "done",
      job_id: "mine",
      direction: "SHORT",
      rows: [],
    });
    await useBacktestJobStore.getState().poll();
    const state = useBacktestJobStore.getState();
    expect(state.job?.status).toBe("done");
    expect(state.foreignJob).toBe(false);
    expect(state.request).toEqual(SHORT_REQUEST);
    expect(state.ownJobId).toBe("mine");
  });

  it("poll marks a job with a different id as foreign", async () => {
    startBacktestJob.mockResolvedValue({
      status: "STARTED",
      job: { status: "running", job_id: "mine", rows: [] },
    });
    await useBacktestJobStore.getState().start(SHORT_REQUEST);
    fetchBacktestJobStatus.mockResolvedValue({
      status: "running",
      job_id: "other-tab",
      direction: "LONG",
      rows: [],
    });
    await useBacktestJobStore.getState().poll();
    const state = useBacktestJobStore.getState();
    expect(state.job?.job_id).toBe("other-tab");
    expect(state.foreignJob).toBe(true);
    expect(state.request).toBeNull();
    expect(state.ownJobId).toBeNull();
  });

  it("a job restored by the first poll after a reload is foreign", async () => {
    fetchBacktestJobStatus.mockResolvedValue({
      status: "done",
      job_id: "before-reload",
      direction: "LONG",
      rows: [],
    });
    await useBacktestJobStore.getState().poll();
    const state = useBacktestJobStore.getState();
    expect(state.job?.job_id).toBe("before-reload");
    expect(state.foreignJob).toBe(true);
    expect(state.request).toBeNull();
  });

  it("setJob clears the request, foreign flag, and own job id", () => {
    useBacktestJobStore.setState({
      request: SHORT_REQUEST,
      foreignJob: true,
      ownJobId: "mine",
    });
    useBacktestJobStore.getState().setJob({ status: "done", job_id: "x", rows: [] });
    const state = useBacktestJobStore.getState();
    expect(state.job?.job_id).toBe("x");
    expect(state.request).toBeNull();
    expect(state.foreignJob).toBe(false);
    expect(state.ownJobId).toBeNull();
  });

  it("background poller skips when inactive", async () => {
    fetchBacktestJobStatus.mockResolvedValue({ status: "idle", rows: [] });
    const stop = startBacktestBackgroundPoller();
    await new Promise((r) => setTimeout(r, 20));
    stop();
    // Initial restore poll may call once; subsequent ticks should not keep active true.
    expect(useBacktestJobStore.getState().active).toBe(false);
  });

  it("clears optimistic running job when start returns ERROR", async () => {
    startBacktestJob.mockResolvedValue({
      status: "ERROR",
      error_code: "short_research_paused",
      error: "SHORT research is paused",
    });
    await useBacktestJobStore.getState().start({
      symbols: ["BTCUSDT"],
      timeframes: ["1h"],
      direction: "SHORT",
    });
    expect(useBacktestJobStore.getState().active).toBe(false);
    expect(useBacktestJobStore.getState().job).toBeNull();
    expect(useBacktestJobStore.getState().request).toBeNull();
    expect(useBacktestJobStore.getState().error).toMatch(/paused|ERROR|short/i);
  });

  it("shows the server sentence for engine_request_unsupported, not the code", async () => {
    startBacktestJob.mockResolvedValue({
      status: "ERROR",
      error_code: "engine_request_unsupported",
      error: "DAILY_TREND_01_SMA200 supports timeframes 1d; got 1h",
    });
    await useBacktestJobStore.getState().start({
      symbols: ["BTCUSDT"],
      timeframes: ["1h"],
      direction: "LONG",
    });
    expect(useBacktestJobStore.getState().error).toBe(
      "DAILY_TREND_01_SMA200 supports timeframes 1d; got 1h",
    );
  });

  it("clears stuck running job when API reports idle", async () => {
    fetchBacktestJobStatus.mockResolvedValue({ status: "idle", rows: [] });
    useBacktestJobStore.setState({
      active: false,
      starting: false,
      job: {
        status: "running",
        symbols: ["BTCUSDT"],
        timeframes: ["1h"],
        rows: [],
      },
    });
    await useBacktestJobStore.getState().poll();
    expect(useBacktestJobStore.getState().job).toBeNull();
    expect(useBacktestJobStore.getState().active).toBe(false);
  });
  it("the BUSY error clears once the job it points at finishes", async () => {
    startBacktestJob.mockResolvedValue({
      status: "BUSY",
      error: "Backtest already running (job_id=other)",
      job: { status: "running", job_id: "other", rows: [] },
    });
    await useBacktestJobStore.getState().start(SHORT_REQUEST);
    expect(useBacktestJobStore.getState().error).toMatch(/already running/);

    fetchBacktestJobStatus.mockResolvedValue({ status: "done", job_id: "other", rows: [] });
    await useBacktestJobStore.getState().poll();
    expect(useBacktestJobStore.getState().error).toBeNull();
    expect(useBacktestJobStore.getState().job?.status).toBe("done");
  });

  it("a failed first restore is retried, so a running job is still found after a reload", async () => {
    vi.useFakeTimers();
    try {
      fetchBacktestJobStatus.mockRejectedValueOnce(new Error("Failed to fetch"));
      fetchBacktestJobStatus.mockResolvedValue({ status: "running", job_id: "j9", rows: [] });
      const stop = startBacktestBackgroundPoller();
      await vi.advanceTimersByTimeAsync(10);
      expect(useBacktestJobStore.getState().job).toBeNull();

      await vi.advanceTimersByTimeAsync(6000);
      stop();
      expect(useBacktestJobStore.getState().job?.job_id).toBe("j9");
      expect(useBacktestJobStore.getState().active).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
