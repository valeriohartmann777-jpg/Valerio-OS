import { describe, expect, it } from "vitest";

import { duration, missionNumber, missionStatusLabel, stateLabel, uptime } from "./format";

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
  });
});
