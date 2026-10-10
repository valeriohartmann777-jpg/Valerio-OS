import { describe, expect, it } from "vitest";

import { valueAt } from "./BlueprintView";
import { CLASS_LABEL, STAGE_LABEL, tc } from "./common";

describe("Idea-to-Edge helpers", () => {
  it("formats source timecodes and never invents one", () => {
    expect(tc(0)).toBe("0:00.0");
    expect(tc(9500)).toBe("0:09.5");
    expect(tc(75_250)).toBe("1:15.3");
    expect(tc(null)).toBe("—");
    expect(tc(Number.NaN)).toBe("—");
  });

  it("reads blueprint fields by path and reports missing ones as undefined", () => {
    const spec = { rule: { type: "level_sweep_reclaim", sweep_min_ticks: 4 }, stop: { type: "setup_extreme" } };
    expect(valueAt(spec, "rule.type")).toBe("level_sweep_reclaim");
    expect(valueAt(spec, "rule.sweep_min_ticks")).toBe(4);
    expect(valueAt(spec, "target.r_multiple")).toBeUndefined();
  });

  it("labels research defaults as defaults, never as the source", () => {
    expect(CLASS_LABEL.DEFAULT_RESEARCH_ASSUMPTION.label).toBe("Research default");
    expect(CLASS_LABEL.EXPLICIT_SOURCE.hint).toMatch(/word for word/);
    expect(CLASS_LABEL.MISSING_BLOCKING.label).toBe("Missing");
    for (const stage of ["register", "claims", "blueprint", "boundary", "freeze", "protocol", "data", "baseline", "audit", "verdict"]) {
      expect(STAGE_LABEL[stage]).toBeTruthy();
    }
  });
});
