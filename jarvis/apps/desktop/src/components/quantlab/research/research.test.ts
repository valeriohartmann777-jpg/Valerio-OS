import { describe, expect, it } from "vitest";

import { bytes, fmt, hhmm, price, share, tone, usd } from "./common";

describe("QuantLab research formatting", () => {
  it("never invents numbers: undefined reads n/a", () => {
    expect(usd(null)).toBe("n/a");
    expect(usd(Number.POSITIVE_INFINITY)).toBe("n/a");
    expect(share(undefined)).toBe("n/a");
    expect(fmt(Number.NaN)).toBe("n/a");
    expect(price(null)).toBe("—");
  });

  it("signs money with a real minus and an explicit plus when asked", () => {
    expect(usd(-1234.5)).toBe("−$1,234.50");
    expect(usd(104, true)).toBe("+$104.00");
    expect(usd(0, true)).toBe("$0.00");
    expect(fmt(-0.5, 3)).toBe("−0.5");
  });

  it("formats shares, prices, times and sizes", () => {
    expect(share(0.394)).toBe("39.4%");
    expect(price(21212.5)).toBe("21,212.50");
    expect(hhmm("2025-07-01T13:48:00Z")).toBe("13:48");
    expect(bytes(6_200_000)).toBe("6.2 MB");
    expect(tone(-1)).toBe("text-ql-danger");
    expect(tone(null)).toBe("text-fg");
  });
});
