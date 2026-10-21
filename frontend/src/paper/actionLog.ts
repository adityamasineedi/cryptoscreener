export type PaperActionEntry = { at: string; action: string };

const KEY = "cs.paper.actions";

export function readPaperActions(): PaperActionEntry[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "[]") as unknown;
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((row) => row && typeof row.at === "string" && typeof row.action === "string")
      .slice(0, 30);
  } catch {
    return [];
  }
}

export function recordPaperAction(action: string, at = new Date().toISOString()): PaperActionEntry[] {
  const next = [{ at, action }, ...readPaperActions()].slice(0, 30);
  localStorage.setItem(KEY, JSON.stringify(next));
  return next;
}
