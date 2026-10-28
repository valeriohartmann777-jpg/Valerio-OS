import type { ApexAnalysis, ApexSignal } from "@jarvis/protocol";
import { describe, expect, it } from "vitest";

import { dataReadiness, executionText, statusChips } from "./analyst";

function analysis(over: Partial<ApexAnalysis> = {}): ApexAnalysis {
  return {
    id: "apa-1",
    engine: "apex-1.2.0",
    market: "XAUUSD",
    as_of: 1,
    replay: false,
    blocked: false,
    data: { source: "dukascopy", label: "Dukascopy XAU/USD", synthetic: false, proxy: null, sha256: "abc", provenance: {} },
    quality: { status: "OK", stale: false, findings: [], summary: {} as ApexAnalysis["quality"]["summary"] },
    spec: { label: "Gold", tick: 0.01, point_value: 100, unit: "lot", cost_points: 0.4 },
    structure: {},
    zones: [],
    scenarios: [],
    candidates: [],
    rejected: [],
    chart: [],
    execution: { assumption: "execution_assumption_limited", quotes: "bid", spread: 0.3, spread_source: "estimate", costs: 0.1, total_cost: 0.4, tradable: true, reason: "" },
    ...over,
  };
}

const signal = (over: Partial<ApexSignal> = {}): ApexSignal =>
  ({ status: "CANDIDATE", validation_state: "UNVALIDATED", entry: 1, ...over }) as ApexSignal;

const online = { text: "Backend online", tone: "success" as const };

describe("APEX analyst status", () => {
  it("keeps app online, data fresh and signal validated apart", () => {
    const chips = statusChips(online, analysis(), signal());
    expect(chips.map((c) => [c.key, c.tone])).toEqual([
      ["app", "success"],
      ["data", "success"],
      ["signal", "muted"], // a candidate of an unvalidated rule is never shown green
    ]);
    expect(chips[2]?.text).toContain("unvalidated");
    expect(statusChips(online, analysis(), signal({ validation_state: "OOS_SUPPORTED", status: "PAPER_VALIDATED" }))[2]?.tone).toBe("success");
  });

  it("blocks on the gate, on stale data and on a proxy — with the reason", () => {
    const blocked = analysis({
      blocked: true,
      quality: { status: "BLOCK", stale: true, findings: [{ level: "BLOCK", code: "NO_RECENT_DATA", message: "No bar in 21 days.", count: null }], summary: {} as ApexAnalysis["quality"]["summary"] },
    });
    expect(dataReadiness(blocked)).toMatchObject({ ready: false, text: "Data blocked" });
    expect(dataReadiness(blocked).reason).toContain("NO_RECENT_DATA");
    const stale = analysis({ quality: { status: "WARN", stale: true, findings: [{ level: "WARN", code: "STALE", message: "40 minutes old", count: 40 }], summary: {} as ApexAnalysis["quality"]["summary"] } });
    expect(dataReadiness(stale)).toMatchObject({ ready: false, text: "Data stale" });
    const proxy = analysis({ execution: { ...analysis().execution!, tradable: false, reason: "Not NQ futures data." } });
    expect(dataReadiness(proxy)).toMatchObject({ ready: false, text: "Data unavailable", reason: "Not NQ futures data." });
    // App online does not make the data ready.
    expect(statusChips(online, blocked, null).map((c) => c.tone)).toEqual(["success", "danger", "muted"]);
  });

  it("test data is never plain ready, and bid-only gold says how it is filled", () => {
    expect(dataReadiness(analysis({ data: { ...analysis().data, synthetic: true } })).tone).toBe("warning");
    expect(executionText(analysis())).toMatch(/Bid prices only.*bid \+ 0\.3.*estimate/);
  });
});
