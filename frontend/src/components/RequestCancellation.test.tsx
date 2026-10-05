/**
 * @vitest-environment jsdom
 * AbortSignal reaches fetch; collapse/unmount aborts; stale discarded.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { LazyResearchMount } from "./LazyResearchMount";
import {
  dedupedPanelFetch,
  invalidatePanelCache,
} from "../lib/researchPanelCache";

function ProbePanel({
  panelId,
  onEvent,
}: {
  panelId: string;
  onEvent: (e: Record<string, unknown>) => void;
}) {
  const [data, setData] = useState<string | null>(null);
  const [runId] = useState("run-A");
  useEffect(() => {
    const ac = new AbortController();
    let cancelled = false;
    onEvent({ type: "request_started", panelId, runId });
    dedupedPanelFetch(
      runId,
      panelId,
      async (signal) => {
        // Simulate a slow fetch that observes abort
        await new Promise<void>((resolve, reject) => {
          const t = setTimeout(() => resolve(), 500);
          signal.addEventListener(
            "abort",
            () => {
              clearTimeout(t);
              onEvent({ type: "request_aborted", panelId, runId });
              reject(new DOMException("Aborted", "AbortError"));
            },
            { once: true },
          );
        });
        onEvent({ type: "request_completed", panelId, runId });
        return { ok: true, runId };
      },
      ac.signal,
    )
      .then((payload) => {
        if (!cancelled) {
          setData(String((payload as { runId: string }).runId));
          onEvent({
            type: "visible_result_run_id",
            panelId,
            runId: (payload as { runId: string }).runId,
          });
        } else {
          onEvent({ type: "stale_response_discarded", panelId, runId });
        }
      })
      .catch((e) => {
        if ((e as Error)?.name === "AbortError") return;
        onEvent({ type: "error", message: String(e) });
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [panelId, runId, onEvent]);
  return <div data-testid={`probe-${panelId}`}>{data || "loading"}</div>;
}

describe("request cancellation", () => {
  beforeEach(() => {
    invalidatePanelCache();
  });
  afterEach(() => {
    invalidatePanelCache();
  });

  it("aborts in-flight fetch when LazyResearchMount collapses", async () => {
    const events: Record<string, unknown>[] = [];
    const onEvent = (e: Record<string, unknown>) => events.push(e);
    render(
      <LazyResearchMount panelId="candidate" label="Candidate">
        <ProbePanel panelId="candidate" onEvent={onEvent} />
      </LazyResearchMount>,
    );
    fireEvent.click(screen.getByTestId("lazy-research-toggle-candidate"));
    await waitFor(() => {
      expect(events.some((e) => e.type === "request_started")).toBe(true);
    });
    // Collapse before completion
    fireEvent.click(screen.getByTestId("lazy-research-toggle-candidate"));
    await waitFor(() => {
      expect(events.some((e) => e.type === "request_aborted")).toBe(true);
    });
    expect(events.some((e) => e.type === "request_completed")).toBe(false);
  });

  it("does not show old run results after cache invalidate for new run", async () => {
    await dedupedPanelFetch("run-old", "short", async () => ({ v: 1 }));
    invalidatePanelCache("run-old");
    const next = await dedupedPanelFetch("run-new", "short", async () => ({ v: 2 }));
    expect(next).toEqual({ v: 2 });
  });
});

describe("AbortSignal reaches fetch helpers", () => {
  it("getJson/fetch receives signal via research helpers", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ results: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { fetchCombo02CandidateResults } = await import("../api/client");
    const ac = new AbortController();
    await fetchCombo02CandidateResults(undefined, { signal: ac.signal });
    expect(fetchSpy).toHaveBeenCalled();
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.signal).toBe(ac.signal);
    fetchSpy.mockRestore();
  });
});
