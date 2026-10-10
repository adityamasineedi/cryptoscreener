import type { GridFill } from "./client";

type EquityPoint = { time: number | string | null; equity: number; drawdown: number };

function cell(value: unknown): string {
  if (value == null) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function fillsToCsv(fills: GridFill[]): string {
  const header = ["time", "side", "slot", "level", "price", "qty", "fee", "reason"];
  const lines = fills.map((fill) =>
    [fill.time, fill.side, fill.slot, fill.level, fill.price, fill.qty, fill.fee, fill.reason ?? ""].map(cell).join(","),
  );
  return [header.join(","), ...lines].join("\n");
}

export function equityToCsv(points: EquityPoint[]): string {
  const header = ["time", "equity", "drawdown"];
  const lines = points.map((point) => [point.time, point.equity, point.drawdown].map(cell).join(","));
  return [header.join(","), ...lines].join("\n");
}

export function bookToJson(fills: GridFill[], points: EquityPoint[]): string {
  return JSON.stringify({ fills, equity_curve: points }, null, 2);
}
