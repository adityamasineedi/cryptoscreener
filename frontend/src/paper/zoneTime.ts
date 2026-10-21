const IST = "Asia/Kolkata";

function parsed(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Clock time in India, labelled IST. */
export function formatIst(iso: string | null | undefined): string {
  const date = parsed(iso);
  if (!date) return iso ? String(iso) : "—";
  const text = new Intl.DateTimeFormat("en-IN", {
    timeZone: IST,
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
  return `${text} IST`;
}

/** Same instant in UTC, for the tooltip. */
export function formatUtc(iso: string | null | undefined): string {
  const date = parsed(iso);
  if (!date) return iso ? `${iso} UTC` : "UTC";
  return `${date.toISOString().replace(".000Z", "Z")} UTC`;
}
