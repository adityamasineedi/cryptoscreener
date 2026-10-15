import { describe, expect, it } from "vitest";
import type { BacktestJob } from "../api/client";
import {
  checkJobInternallyConsistent,
  checkRunMatchesRequest,
  directionLabel,
  htfRequirementLabel,
  type RunRequest,
} from "./runGuard";

const V1_REQUEST: RunRequest = {
  symbols: ["BTCUSDT", "ETHUSDT"],
  timeframes: ["1h"],
  direction: "LONG",
  strategy_id: "COMBO_02_V1",
  research_risk_override: false,
};

const V1_JOB: BacktestJob = {
  status: "done",
  job_id: "j1",
  symbols: ["ETHUSDT", "BTCUSDT"],
  timeframes: ["1h"],
  direction: "LONG",
  strategy_id: "COMBO_02_V1",
  production_comparable: true,
  research_only: false,
  rows: [],
};

const SHORT_REQUEST: RunRequest = {
  symbols: ["BTCUSDT", "ETHUSDT"],
  timeframes: ["1h"],
  direction: "SHORT",
  strategy_id: "COMBO_04_HTF_SHORT",
  research_risk_override: false,
};

describe("checkRunMatchesRequest", () => {
  it("returns no problems when the job or the request is missing", () => {
    expect(checkRunMatchesRequest(null, V1_REQUEST)).toEqual([]);
    expect(checkRunMatchesRequest(V1_JOB, null)).toEqual([]);
    expect(checkRunMatchesRequest(undefined, undefined)).toEqual([]);
  });

  it("accepts a production comparable job for a v1 LONG request with no override", () => {
    expect(checkRunMatchesRequest(V1_JOB, V1_REQUEST)).toEqual([]);
  });

  it("reports direction and strategy differences", () => {
    const problems = checkRunMatchesRequest(V1_JOB, SHORT_REQUEST);
    expect(problems).toContain("Direction: the form requested SHORT, the job ran LONG.");
    expect(problems).toContain(
      "Strategy: the form requested COMBO_04_HTF_SHORT, the job ran COMBO_02_V1.",
    );
  });

  it("reports symbol and timeframe set differences, ignoring order", () => {
    const job: BacktestJob = { ...V1_JOB, symbols: ["BTCUSDT"], timeframes: ["1h", "4h"] };
    const problems = checkRunMatchesRequest(job, V1_REQUEST);
    expect(problems).toHaveLength(2);
    expect(problems[0]).toMatch(/^Symbols:.*BTCUSDT, ETHUSDT.*the job ran BTCUSDT\.$/);
    expect(problems[1]).toMatch(/^Timeframes:.*the job ran 1h, 4h\.$/);
  });

  it("rejects production comparable and not-research-only flags for a non-v1 request", () => {
    const job: BacktestJob = {
      ...V1_JOB,
      direction: "SHORT",
      strategy_id: "COMBO_04_HTF_SHORT",
    };
    const problems = checkRunMatchesRequest(job, SHORT_REQUEST);
    expect(problems).toHaveLength(2);
    expect(problems[0]).toMatch(/marked production comparable/);
    expect(problems[1]).toMatch(/not marked research-only/);
  });

  it("treats a v1 LONG request with a risk override as not frozen v1", () => {
    const problems = checkRunMatchesRequest(V1_JOB, {
      ...V1_REQUEST,
      research_risk_override: true,
    });
    expect(problems).toHaveLength(2);
  });

  it("accepts a research-only job for a non-v1 request", () => {
    const job: BacktestJob = {
      ...V1_JOB,
      direction: "SHORT",
      strategy_id: "COMBO_04_HTF_SHORT",
      production_comparable: false,
      research_only: true,
    };
    expect(checkRunMatchesRequest(job, SHORT_REQUEST)).toEqual([]);
  });
});

describe("checkJobInternallyConsistent", () => {
  const row = {
    combination_id: "COMBO_02",
    symbol: "BTCUSDT",
    timeframe: "1h",
    direction: "LONG",
    sample_size: 1,
  };
  const trade = {
    trade_no: 4,
    symbol: "BTCUSDT",
    timeframe: "1h",
    direction: "LONG",
    entry_price: 1,
    stop_price: 1,
  };

  it("returns no problems for a missing job or a consistent one", () => {
    expect(checkJobInternallyConsistent(null)).toEqual([]);
    expect(
      checkJobInternallyConsistent({ ...V1_JOB, rows: [{ ...row, trades: [trade] }] }),
    ).toEqual([]);
  });

  it("flags comparable flags that are both true or both false", () => {
    expect(
      checkJobInternallyConsistent({ ...V1_JOB, research_only: true }),
    ).toEqual([
      "Job flags contradict: production_comparable and research_only are both true.",
    ]);
    expect(
      checkJobInternallyConsistent({ ...V1_JOB, production_comparable: false }),
    ).toEqual([
      "Job flags contradict: production_comparable and research_only are both false.",
    ]);
  });

  it("flags a row and a trade whose direction differs from the job, naming trade_no", () => {
    const problems = checkJobInternallyConsistent({
      ...V1_JOB,
      rows: [
        { ...row, direction: "SHORT", trades: [trade, { ...trade, trade_no: 9, direction: "SHORT" }] },
      ],
    });
    expect(problems).toEqual([
      "BTCUSDT 1h: row direction is SHORT in a LONG run.",
      "BTCUSDT 1h: trade_no 9 is SHORT in a LONG run.",
    ]);
  });

  it("does not apply the single-direction rule to V2.1-A", () => {
    expect(
      checkJobInternallyConsistent({
        status: "done",
        direction: "LONG",
        strategy_id: "COMBO_02_V2_1_A",
        production_comparable: false,
        research_only: true,
        rows: [{ ...row, trades: [trade, { ...trade, trade_no: 2, direction: "SHORT" }] }],
      }),
    ).toEqual([]);
  });

  it("flags a production comparable row inside a run that is not", () => {
    expect(
      checkJobInternallyConsistent({
        ...V1_JOB,
        production_comparable: false,
        research_only: true,
        rows: [{ ...row, production_comparable: true }],
      }),
    ).toEqual([
      "BTCUSDT 1h: row is marked production comparable inside a run that is not.",
    ]);
  });
});

describe("job labels", () => {
  it("labels direction from the job", () => {
    expect(directionLabel({ status: "done", strategy_id: "COMBO_02_V2_1_A", direction: "LONG" })).toBe(
      "LONG+SHORT (native)",
    );
    expect(directionLabel({ status: "done", direction: "SHORT" })).toBe("SHORT");
  });

  it("labels the HTF requirement from the job", () => {
    expect(htfRequirementLabel({ status: "done", strategy_id: "COMBO_02_V2_1_A" })).toBe(
      "soft 4h + 15m confirm (v2 playbooks)",
    );
    expect(htfRequirementLabel({ status: "done", direction: "SHORT" })).toBe(
      "1h + 4h bearish alignment",
    );
    expect(htfRequirementLabel({ status: "done", direction: "LONG" })).toBe(
      "1h + 4h bullish alignment",
    );
  });
});
