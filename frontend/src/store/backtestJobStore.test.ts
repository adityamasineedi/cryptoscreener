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

describe("backtestJobStore polling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useBacktestJobStore.setState({
      job: null,
      error: null,
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
    expect(useBacktestJobStore.getState().error).toMatch(/paused|ERROR|short/i);
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
});
