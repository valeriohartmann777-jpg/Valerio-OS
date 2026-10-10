import { describe, expect, it } from "vitest";

import { TASK, usd } from "./parts";

describe("ULTRON display helpers", () => {
  it("formats spend without inventing precision", () => {
    expect(usd(0)).toBe("$0.00");
    expect(usd(2.5)).toBe("$2.50");
    expect(usd(0.0031)).toBe("$0.0031"); // tiny real costs stay visible
    expect(usd(Number.NaN)).toBe("—");
    expect(usd(null)).toBe("—");
  });

  it("labels every task state in words, not colour alone", () => {
    for (const state of Object.values(TASK)) {
      expect(state.label.length).toBeGreaterThan(2);
      expect(state.icon.length).toBeGreaterThan(0);
    }
  });
});
