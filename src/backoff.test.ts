import { describe, expect, test } from "bun:test";
import { backoffDelayMs } from "./backoff";

describe("backoffDelayMs", () => {
  test("follows 1s -> 2s -> 4s -> 8s -> 16s -> 30s and stays capped", () => {
    const seq = [0, 1, 2, 3, 4, 5, 6, 7, 20].map((a) => backoffDelayMs(a, 30_000));
    expect(seq).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000, 30000]);
  });

  test("honours a custom cap", () => {
    expect(backoffDelayMs(0, 5000)).toBe(1000);
    expect(backoffDelayMs(3, 5000)).toBe(5000);
  });

  test("never overflows on absurd attempt counts", () => {
    expect(backoffDelayMs(10_000, 30_000)).toBe(30_000);
    expect(Number.isFinite(backoffDelayMs(10_000, 30_000))).toBe(true);
  });

  test("negative attempt is treated as the first attempt", () => {
    expect(backoffDelayMs(-5, 30_000)).toBe(1000);
  });
});
