/**
 * Colour for a money value (owner D206): a gain is green, a loss red, and zero, including
 * anything that rounds to $0.00, neutral grey. Zero is neither gain nor loss.
 */
export const MONEY_GAIN = "text-emerald-400";
export const MONEY_LOSS = "text-red-400";
export const MONEY_ZERO = "text-terminal-muted";

export function moneyTone(
  value: number | null | undefined,
  tones: { gain?: string; loss?: string; zero?: string } = {},
): string {
  const zero = tones.zero ?? MONEY_ZERO;
  if (value == null) return zero;
  const n = Number(value);
  if (!Number.isFinite(n)) return zero;
  const cents = Math.round(n * 100);
  if (cents > 0) return tones.gain ?? MONEY_GAIN;
  if (cents < 0) return tones.loss ?? MONEY_LOSS;
  return zero;
}
