import type { ApexAnalysis, ApexCandidate, ApexMapZone, ApexSignal } from "@jarvis/protocol";
import { describe, expect, it } from "vitest";

import { checkpoints, dataAgeMinutes, decide, pathLines, zoneBands } from "./apexView";

function zone(id: string, side: "SUPPORT" | "RESISTANCE", rank: number, low: number): ApexMapZone {
  return {
    zone_id: id, market: "NQ", side, role_at_confirmation: side.toLowerCase(), bounds: { low, high: low + 5 },
    formation_time_utc: null, confirmed_time_utc: null, state: "ACTIVE", quality_score: 60, rank, distance_atr: 1,
    inside: false, timeframe_of_origin: "15m", kinds: ["swing"], labels: [], source_ids: [], evidence: [],
    counterevidence: [], episodes: [], touches: 1, rejections: 1, last_tested_at: null, broken_at: null,
    flipped_at: null, transitions: [], version: "zonemap-1.1.0", invalidation_reason: null,
  };
}

function analysis(over: Partial<ApexAnalysis> = {}): ApexAnalysis {
  return {
    id: "apa-1", engine: "apex-1.4.0", market: "NQ", as_of: 6000, replay: false, blocked: false,
    data: { source: "vault", label: "NQ", synthetic: false, proxy: null, sha256: "x", provenance: {}, last_bar: 5700 },
    quality: { status: "OK", stale: false, findings: [], summary: {} as ApexAnalysis["quality"]["summary"] },
    spec: { label: "NQ", tick: 0.25, point_value: 20, unit: "contract", cost_points: 1 },
    structure: {}, zones: [], scenarios: [], candidates: [], rejected: [], chart: [], mode: "DELAYED",
    execution: { assumption: "futures_trade_prices", quotes: "trades", spread: 0, spread_source: "inside_costs", costs: 1, total_cost: 1, tradable: true, reason: "" },
    zone_map: {
      version: "zonemap-1.1.0", market: "NQ", as_of: 6000, as_of_utc: "", price: 20010, atr15: 10,
      support: [zone("s1", "SUPPORT", 1, 19995), zone("s2", "SUPPORT", 2, 19950)],
      resistance: [zone("r1", "RESISTANCE", 1, 20060)], others: [],
      buy_zone: { zone_id: "s1", rank: 1, bounds: { low: 19995, high: 20000 }, distance_atr: 1, state: "WATCH", quality_score: 60 },
      sell_zone: null, notes: ["Only 1 resistance zone(s) meet the evidence bar"], min_score: 30, score_is_probability: false,
    },
    factors: [
      { factor: "price_structure", label: "Structure", family: "structure", status: "CONFIRMED", detail: "", source_id: "x" },
      { factor: "delta", label: "Delta", family: "orderflow", status: "UNAVAILABLE", detail: "", source_id: "x" },
    ],
    ...over,
  } as ApexAnalysis;
}

const online = { text: "Backend online", tone: "success" as const };
const plan = (over: Partial<ApexCandidate> = {}): ApexCandidate =>
  ({ id: "apx-1", setup: "rejection_rotation", direction: "long", entry: { type: "stop", price: 20003.25 }, stop: 19993.25,
     targets: [{ price: 20059.75, rr: 5.5, zone: "r1" }], trade_success_probability: { validated: false },
     eligibility: { eligible: false, checks: [{ check: "PROBABILITY", passed: false, reason: "PROBABILITY NOT VALIDATED" }, { check: "DATA_FIT", passed: true, reason: "ok" }], net_expected_r: null }, ...over }) as ApexCandidate;

describe("APEX workstation checkpoints", () => {
  it("are eight independent checks — an online app never makes a signal", () => {
    const cps = checkpoints(online, analysis());
    expect(cps.map((c) => c.key)).toEqual([
      "APPLICATION_READY", "DATA_FITNESS", "ZONE_EVIDENCE", "FEATURE_AVAILABILITY",
      "STRATEGY_VALIDATED", "PROBABILITY_CALIBRATED", "PAPER_FORWARD", "SIGNAL_ELIGIBLE",
    ]);
    expect(cps.map((c) => c.passed)).toEqual([true, true, true, true, false, false, false, false]);
    expect(cps[2]?.text).toBe("2 support · 1 resistance"); // fewer than four: shown, never invented
    expect(cps[2]?.tone).toBe("warning");
    expect(cps[5]?.text).toBe("PROBABILITY NOT VALIDATED");
    expect(cps[7]?.text).toBe("NO TRADE");
    expect(cps[3]?.detail).toContain("Delta");
  });

  it("test data, a replay and no analysis never pass the data checkpoint", () => {
    expect(checkpoints(online, analysis({ data: { ...analysis().data, synthetic: true } }))[1]?.passed).toBe(false);
    expect(checkpoints(online, analysis({ mode: "REPLAY" }))[1]?.passed).toBe(false);
    expect(checkpoints(online, null).slice(1).every((c) => !c.passed)).toBe(true);
  });

  it("only an eligible signal passes SIGNAL_ELIGIBLE", () => {
    const eligible = { strategy_id: "sweep_reclaim", eligible: true, validation_state: "UNVALIDATED" } as ApexSignal;
    expect(checkpoints(online, analysis({ signals: [eligible] }))[7]).toMatchObject({ passed: true, text: "1 qualified (paper)" });
  });
});

describe("APEX decision", () => {
  it("a research plan that failed the gate is NO TRADE with the failed checks", () => {
    const d = decide(analysis({ candidates: [plan()] }));
    expect(d.verdict).toBe("NO TRADE");
    expect(d.failed.map((c) => c.check)).toEqual(["PROBABILITY"]);
    expect(d.reasons[0]).toContain("PROBABILITY NOT VALIDATED");
  });

  it("LONG only for a qualified plan, WAIT while a setup builds", () => {
    const ok = plan({ eligibility: { eligible: true, checks: [], failed: [], setup_state: "ENTRY_ARMED", blocked: false, net_expected_r: 1.2, conviction: "QUALIFIED (≥ 0.70)", probability_label: null } });
    expect(decide(analysis({ candidates: [ok] })).verdict).toBe("LONG");
    const watching = analysis({
      strategies: {
        rejection_rotation: {
          strategy: "rejection_rotation", version: "1.0.0", status: "WAITING_FOR_TRIGGER", direction: "none",
          setups: [{ zone: "s1", direction: "long", stage: "WATCHING_SETUP", setup_state: "WATCH", reasons: ["Touched S1 without confirmation"] }],
          trigger: null, candidates: [], rejected: [], reasons: [], stage: "WATCHING_SETUP", setup_state: "WATCH",
        },
      },
    });
    const d = decide(watching);
    expect(d.verdict).toBe("WAIT");
    expect(d.reasons[0]).toContain("without confirmation");
    expect(decide(analysis()).verdict).toBe("NO TRADE");
  });
});

describe("APEX chart overlays", () => {
  it("ranked bands S1/S2/R1 with the buy zone marked", () => {
    const bands = zoneBands(analysis());
    expect(bands.map((b) => [b.label, b.emphasis])).toEqual([["S1", "buy"], ["S2", null], ["R1", null]]);
  });

  it("path barriers carry their counted share and say not validated", () => {
    const lines = pathLines(
      analysis({
        paths: {
          version: "path-1.0.0", available: true, as_of: 6000, horizon_minutes: 120, status: "NOT_VALIDATED", calibrated: false, note: "",
          barriers: { upper: 20060, lower: 20000, upper_source: "R1", lower_source: "S1" },
          shares: { UPPER_FIRST: 0.25, LOWER_FIRST: 0.7, NEITHER: 0.04, AMBIGUOUS: 0.01 },
        },
      }),
    );
    expect(lines.map((l) => l.label)).toEqual(["upper first 25% in 120m · not validated", "lower first 70% in 120m · not validated"]);
    expect(pathLines(analysis())).toEqual([]);
  });

  it("data age is measured from the last bar's close", () => {
    expect(dataAgeMinutes(analysis())).toBe(4);
  });
});
