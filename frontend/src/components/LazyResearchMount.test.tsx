/**
 * @vitest-environment jsdom
 *
 * Lazy research mount: unopened panels do not render children.
 */
import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LazyResearchMount } from "./LazyResearchMount";
import {
  dedupedPanelFetch,
  getCachedPanelResult,
  invalidatePanelCache,
} from "../lib/researchPanelCache";

describe("LazyResearchMount", () => {
  it("does not mount children until expanded", () => {
    render(
      <LazyResearchMount panelId="candidate" label="Candidate">
        <div data-testid="panel-body">loaded</div>
      </LazyResearchMount>,
    );
    expect(screen.queryByTestId("panel-body")).toBeNull();
    expect(screen.getByText(/loaded on demand/i)).toBeTruthy();
    fireEvent.click(screen.getByTestId("lazy-research-toggle-candidate"));
    expect(screen.getByTestId("panel-body")).toBeTruthy();
  });
});

describe("researchPanelCache", () => {
  it("dedupes in-flight fetches and caches by runId+panelId", async () => {
    invalidatePanelCache();
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      await new Promise((r) => setTimeout(r, 20));
      return { ok: true, calls };
    };
    const [a, b] = await Promise.all([
      dedupedPanelFetch("run1", "candidate", fetcher),
      dedupedPanelFetch("run1", "candidate", fetcher),
    ]);
    expect(calls).toBe(1);
    expect(a).toEqual(b);
    expect(getCachedPanelResult("run1", "candidate")).toEqual(a);
    const c = await dedupedPanelFetch("run1", "candidate", fetcher);
    expect(calls).toBe(1);
    expect(c).toEqual(a);
  });

  it("does not return old run cache for a new run id", async () => {
    invalidatePanelCache();
    await dedupedPanelFetch("run-old", "short", async () => ({ v: 1 }));
    expect(getCachedPanelResult("run-new", "short")).toBeNull();
  });
});
