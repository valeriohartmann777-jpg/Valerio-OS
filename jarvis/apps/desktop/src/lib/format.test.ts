import { describe, expect, it } from "vitest";

import {
  duration,
  holdoutVerdict,
  missionNumber,
  missionStatusLabel,
  modelLabel,
  signedPercent,
  stateLabel,
  trainingVerdict,
  uptime,
} from "./format";

describe("format", () => {
  it("formats durations", () => {
    const start = new Date(0).toISOString();
    expect(duration(start, new Date(420).toISOString())).toBe("420 ms");
    expect(duration(start, new Date(2500).toISOString())).toBe("2.5 s");
    expect(duration(start, new Date(65_000).toISOString())).toBe("1m 05s");
    expect(duration(start, null, 1000)).toBe("1.0 s");
  });

  it("formats uptime", () => {
    expect(uptime(59)).toBe("0m");
    expect(uptime(3720)).toBe("1h 2m");
    expect(uptime(90_000)).toBe("1d 1h");
  });

  it("labels", () => {
    expect(missionNumber(14)).toBe("014");
    expect(stateLabel("DORMANT")).toBe("Online");
    expect(stateLabel("WAITING_FOR_APPROVAL")).toBe("Awaiting approval");
    expect(missionStatusLabel("waiting_for_approval", true)).toBe("Approval");
    expect(missionStatusLabel("complete")).toBe("Complete");
    expect(modelLabel("claude-sonnet-5-5")).toBe("Claude Sonnet 5.5");
    expect(modelLabel("claude-opus-5-5")).toBe("Claude Opus 5.5");
    expect(modelLabel("claude-haiku-4-5")).toBe("Claude Haiku 4.5");
  });
});

describe("holdoutVerdict", () => {
  it("only calls a significant holdout result confirmed", () => {
    expect(holdoutVerdict({ avg_r: 0.2, t_stat: 2.4 }, true)?.label).toBe("Significant on unseen data · t 2.4");
    const weak = holdoutVerdict({ avg_r: 0.05, t_stat: 0.8 }, false);
    expect(weak?.label).toBe("Positive on unseen data, not significant · t 0.8");
    expect(weak?.tone).toBe("warning");
    expect(holdoutVerdict({ avg_r: -0.1, t_stat: -1.2 }, false)?.tone).toBe("danger");
    expect(holdoutVerdict(null, null)).toBeNull();
  });
});

describe("trainingVerdict", () => {
  it("only calls a model confirmed when unseen months confirm it", () => {
    expect(trainingVerdict("confirmed").tone).toBe("success");
    expect(trainingVerdict("unconfirmed").label).toBe("Not confirmed on unseen months");
    expect(trainingVerdict("no_edge").label).toBe("No edge over the baseline");
    expect(trainingVerdict("too_little_data").tone).toBe("faint");
    expect(signedPercent(0.0123)).toBe("+1.2%");
    expect(signedPercent(-0.004)).toBe("−0.4%");
  });
});
