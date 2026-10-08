export const BACKOFF_BASE_MS = 1_000;

/**
 * Exponential backoff: base * 2^attempt, capped at maxMs.
 * With defaults: 1s -> 2s -> 4s -> 8s -> 16s -> 30s -> 30s ...
 */
export function backoffDelayMs(
  attempt: number,
  maxMs: number,
  baseMs: number = BACKOFF_BASE_MS,
): number {
  // Clamp the exponent so a long outage can never overflow to Infinity.
  const exp = Math.min(Math.max(attempt, 0), 30);
  return Math.min(baseMs * 2 ** exp, maxMs);
}
