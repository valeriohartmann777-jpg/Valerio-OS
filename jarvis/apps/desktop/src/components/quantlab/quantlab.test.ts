import { describe, expect, it } from "vitest";

import { niceTicks } from "./EquityChart";
import { money, num, pct, utc } from "./ui";

describe("QuantLab formatting", () => {
  it("never shows NaN or Infinity — missing values read n/a", () => {
    expect(money(Number.NaN)).toBe("n/a");
    expect(money(null)).toBe("n/a");
    expect(pct(Number.POSITIVE_INFINITY)).toBe("n/a");
    expect(pct(undefined)).toBe("n/a");
    expect(num(null)).toBe("—");
  });

  it("signs money and percentages with a real minus", () => {
    expect(money(-7.5)).toBe("−7.50 USD");
    expect(money(1234.5, "EUR")).toBe("+1,234.50 EUR");
    expect(money(0)).toBe("0.00 USD");
    expect(money(92.5, "USD", false)).toBe("92.50 USD");
    expect(pct(-0.075)).toBe("−7.50%");
    expect(pct(0.42, 0, false)).toBe("42%");
  });

  it("shows UTC timestamps as UTC", () => {
    expect(utc("2026-09-16T09:30:00Z")).toBe("2026-09-16 09:30 UTC");
    expect(utc(null)).toBe("—");
  });

  it("picks round axis ticks inside the range", () => {
    expect(niceTicks(91.5, 100.5)).toEqual([92.5, 95, 97.5, 100]);
    expect(niceTicks(0, 20_000)).toEqual([0, 5000, 10_000, 15_000, 20_000]);
    expect(niceTicks(5, 5)).toEqual([5]);
  });
});
