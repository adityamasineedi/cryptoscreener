/**
 * In-flight + result cache for on-demand research panel fetches.
 * Keyed by (runId, panelId). Stale responses for older runIds are ignored by callers.
 */
type CacheEntry<T> = { runId: string; data: T; at: number };

const inflight = new Map<string, Promise<unknown>>();
const cache = new Map<string, CacheEntry<unknown>>();

function key(runId: string, panelId: string): string {
  return `${runId}::${panelId}`;
}

export function getCachedPanelResult<T>(runId: string, panelId: string): T | null {
  const hit = cache.get(key(runId, panelId));
  if (!hit || hit.runId !== runId) return null;
  return hit.data as T;
}

export function invalidatePanelCache(runId?: string): void {
  if (!runId) {
    cache.clear();
    inflight.clear();
    return;
  }
  for (const k of [...cache.keys()]) {
    if (k.startsWith(`${runId}::`)) cache.delete(k);
  }
  for (const k of [...inflight.keys()]) {
    if (k.startsWith(`${runId}::`)) inflight.delete(k);
  }
}

export async function dedupedPanelFetch<T>(
  runId: string,
  panelId: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  const cached = getCachedPanelResult<T>(runId, panelId);
  if (cached != null) return cached;

  const k = key(runId, panelId);
  const existing = inflight.get(k) as Promise<T> | undefined;
  if (existing) return existing;

  const ac = new AbortController();
  const onAbort = () => ac.abort();
  if (signal) {
    if (signal.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    signal.addEventListener("abort", onAbort, { once: true });
  }

  const promise = fetcher(ac.signal)
    .then((data) => {
      cache.set(k, { runId, data, at: Date.now() });
      return data;
    })
    .finally(() => {
      inflight.delete(k);
      if (signal) signal.removeEventListener("abort", onAbort);
    });

  inflight.set(k, promise);
  return promise;
}
