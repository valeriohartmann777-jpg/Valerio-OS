import type {
  ApexAnalysis,
  ApexCandidate,
  ApexDecision,
  ApexGateCheck,
  ApexMapZone,
  ApexProbabilityModel,
  ApexSignal,
  ApexZoneSetupName,
} from "@jarvis/protocol";

import type { PillTone } from "../../ui/shell";
import { dataReadiness } from "./analyst";

/**
 * Pure helpers for the APEX workstation: the eight independent checkpoints, the decision a
 * person reads in five seconds (LONG / SHORT / WAIT / NO TRADE), the ranked zone bands and the
 * scenario barrier lines on the chart. Nothing here invents a number: what the backend didn't
 * measure is shown as unavailable or not validated.
 */

export const ZONE_SETUPS: { id: ApexZoneSetupName; label: string }[] = [
  { id: "sweep_reclaim", label: "Sweep → reclaim" },
  { id: "rejection_rotation", label: "Rejection → rotation" },
  { id: "breakout_retest", label: "Breakout → retest" },
];

export type CheckpointKey =
  | "APPLICATION_READY"
  | "DATA_FITNESS"
  | "ZONE_EVIDENCE"
  | "FEATURE_AVAILABILITY"
  | "STRATEGY_VALIDATED"
  | "PROBABILITY_CALIBRATED"
  | "PAPER_FORWARD"
  | "SIGNAL_ELIGIBLE";

export interface Checkpoint {
  key: CheckpointKey;
  label: string;
  passed: boolean;
  text: string;
  tone: PillTone;
  detail: string;
}

const LABELS: Record<CheckpointKey, string> = {
  APPLICATION_READY: "App",
  DATA_FITNESS: "Data",
  ZONE_EVIDENCE: "Zones",
  FEATURE_AVAILABILITY: "Features",
  STRATEGY_VALIDATED: "Strategy",
  PROBABILITY_CALIBRATED: "Probability",
  PAPER_FORWARD: "Forward",
  SIGNAL_ELIGIBLE: "Signal",
};

const SUPPORTED = new Set(["OOS_SUPPORTED", "FORWARD_SUPPORTED"]);

function zoneSetupSignals(analysis: ApexAnalysis): ApexSignal[] {
  const ids = new Set<string>(ZONE_SETUPS.map((s) => s.id));
  return (analysis.signals ?? []).filter((s) => ids.has(s.strategy_id));
}

/** The eight checkpoints — each judged on its own, never merged into one "online". */
export function checkpoints(
  app: { text: string; tone: "success" | "warning" | "danger" },
  analysis: ApexAnalysis | null,
  extra: { forwardPassed?: boolean | null; models?: ApexProbabilityModel[] } = {},
): Checkpoint[] {
  const cp = (key: CheckpointKey, passed: boolean, text: string, tone: PillTone, detail: string): Checkpoint => ({
    key,
    label: LABELS[key],
    passed,
    text,
    tone,
    detail,
  });
  const out: Checkpoint[] = [cp("APPLICATION_READY", app.tone === "success", app.text, app.tone, "Backend running with a known build.")];
  if (!analysis) {
    for (const key of Object.keys(LABELS).slice(1) as CheckpointKey[]) {
      out.push(cp(key, false, "no analysis yet", "muted", "Analyse first."));
    }
    return out;
  }
  const data = dataReadiness(analysis);
  const fit = data.ready && !analysis.data.synthetic && analysis.mode !== "REPLAY";
  out.push(cp("DATA_FITNESS", fit, data.text, fit ? "success" : data.ready ? "warning" : "danger", data.reason));

  const zm = analysis.zone_map;
  const s = zm?.support.length ?? 0;
  const r = zm?.resistance.length ?? 0;
  out.push(
    cp(
      "ZONE_EVIDENCE",
      s + r > 0,
      zm ? `${s} support · ${r} resistance` : "no zone map",
      s + r > 0 ? (s < 4 || r < 4 ? "warning" : "success") : "danger",
      zm?.notes.join(" ") || "Ranked zones with confirmed causal evidence (the score is not a probability).",
    ),
  );

  const factors = analysis.factors ?? [];
  const count = (status: string) => factors.filter((f) => f.status === status).length;
  const missing = factors.filter((f) => f.status === "UNAVAILABLE").map((f) => f.label);
  out.push(
    cp(
      "FEATURE_AVAILABILITY",
      factors.length > 0 && count("CONFIRMED") > 0,
      factors.length ? `${count("CONFIRMED")} confirmed · ${count("PROXY")} proxy · ${count("UNAVAILABLE")} unavailable` : "unknown",
      factors.length ? (count("UNAVAILABLE") || count("PROXY") ? "warning" : "success") : "muted",
      missing.length ? `Unavailable: ${missing.join(", ")}.` : "Every factor measured on this source.",
    ),
  );

  const setupSignals = zoneSetupSignals(analysis);
  const validated = setupSignals.filter((x) => SUPPORTED.has(x.validation_state));
  out.push(
    cp(
      "STRATEGY_VALIDATED",
      validated.length > 0,
      validated.length ? `${new Set(validated.map((x) => x.strategy_id)).size} setup(s) supported` : "not validated",
      validated.length ? "success" : "muted",
      "Out-of-sample support from the pre-registered validation protocol, per setup version.",
    ),
  );

  const calibratedPlans = analysis.candidates.filter((c) => c.trade_success_probability?.validated);
  const calibratedModels = (extra.models ?? []).filter((m) => m.validated && !m.synthetic);
  const calibrated = calibratedPlans.length > 0 || calibratedModels.length > 0;
  out.push(
    cp(
      "PROBABILITY_CALIBRATED",
      calibrated,
      calibrated ? "validated head" : "PROBABILITY NOT VALIDATED",
      calibrated ? "success" : "muted",
      "P(TP before SL after the fill) from a validated, calibrated head — a zone score or path share never counts.",
    ),
  );

  out.push(
    cp(
      "PAPER_FORWARD",
      extra.forwardPassed === true,
      extra.forwardPassed ? "forward confirmed" : "not yet",
      extra.forwardPassed ? "success" : "muted",
      "Immutable paper predictions recorded before their outcomes, on new real data.",
    ),
  );

  const eligible = setupSignals.filter((x) => x.eligible);
  out.push(
    cp(
      "SIGNAL_ELIGIBLE",
      eligible.length > 0,
      eligible.length ? `${eligible.length} qualified (paper)` : "NO TRADE",
      eligible.length ? "success" : "muted",
      "Every eligibility check passed — still paper only, never an order.",
    ),
  );
  return out;
}

export type Verdict = "LONG" | "SHORT" | "WAIT" | "NO TRADE";

export interface Decision {
  verdict: Verdict;
  headline: string;
  plan: ApexCandidate | null;
  failed: ApexGateCheck[];
  reasons: string[];
}

function zoneDecisions(analysis: ApexAnalysis): ApexDecision[] {
  return ZONE_SETUPS.map((s) => analysis.strategies?.[s.id]).filter((d): d is ApexDecision => !!d);
}

/** LONG / SHORT only for a plan the gate qualified; WAIT while a setup is building; else NO TRADE. */
export function decide(analysis: ApexAnalysis): Decision {
  const plans = analysis.candidates.filter((c) => c.setup);
  const qualified = plans.filter((c) => c.eligibility?.eligible);
  const best = qualified.sort((a, b) => (b.eligibility?.net_expected_r ?? 0) - (a.eligibility?.net_expected_r ?? 0))[0];
  if (best) {
    return {
      verdict: best.direction === "long" ? "LONG" : "SHORT",
      headline: `${best.direction === "long" ? "Long" : "Short"} ${best.setup?.replace(/_/g, " ")} — qualified paper signal`,
      plan: best,
      failed: [],
      reasons: [best.trigger?.rule ?? ""].filter(Boolean),
    };
  }
  if (analysis.blocked) {
    return { verdict: "NO TRADE", headline: "Data blocked — no signal", plan: null, failed: [], reasons: analysis.rejected.map((r) => r.reason) };
  }
  const research = plans[0] ?? null;
  if (research) {
    const failed = (research.eligibility?.checks ?? []).filter((c) => !c.passed);
    return {
      verdict: "NO TRADE",
      headline: "A research plan exists, but it is not a qualified signal",
      plan: research,
      failed,
      reasons: failed.map((c) => `${c.check}: ${c.reason}`),
    };
  }
  const building = zoneDecisions(analysis).flatMap((d) =>
    d.setups.filter((s) => s.setup_state === "WATCH" || s.setup_state === "TRIGGER_PENDING").map((s) => ({ d, s })),
  );
  const pending = building.find((b) => b.s.setup_state === "TRIGGER_PENDING") ?? building[0];
  if (pending) {
    return {
      verdict: "WAIT",
      headline: `Watching: ${pending.d.strategy.replace(/_/g, " ")} ${pending.s.direction}${
        pending.s.setup_state === "TRIGGER_PENDING" ? " — trigger pending" : ""
      }`,
      plan: null,
      failed: [],
      reasons: Array.from(new Set(building.flatMap((b) => b.s.reasons ?? []))).slice(0, 4),
    };
  }
  return {
    verdict: "NO TRADE",
    headline: "No setup near a ranked zone",
    plan: null,
    failed: [],
    reasons: zoneDecisions(analysis).flatMap((d) => d.reasons).slice(0, 3),
  };
}

export interface ZoneBand {
  id: string;
  label: string;
  side: "support" | "resistance";
  low: number;
  high: number;
  emphasis: "buy" | "sell" | null;
  state: string;
}

/** The ranked zones S1-S4 / R1-R4 (with the current buy / sell zone marked) for the chart. */
export function zoneBands(analysis: ApexAnalysis): ZoneBand[] {
  const zm = analysis.zone_map;
  if (!zm || zm.price == null) {
    return analysis.zones.map((z) => ({ id: z.id, label: z.id, side: z.side, low: z.low, high: z.high, emphasis: null, state: "" }));
  }
  const band = (z: ApexMapZone, side: "support" | "resistance"): ZoneBand => ({
    id: z.zone_id,
    label: `${side === "support" ? "S" : "R"}${z.rank ?? "?"}`,
    side,
    low: z.bounds.low,
    high: z.bounds.high,
    emphasis: zm.buy_zone?.zone_id === z.zone_id ? "buy" : zm.sell_zone?.zone_id === z.zone_id ? "sell" : null,
    state: z.state,
  });
  return [...zm.support.map((z) => band(z, "support")), ...zm.resistance.map((z) => band(z, "resistance"))];
}

export interface PathLine {
  price: number;
  label: string;
  kind: "upper" | "lower";
}

/** The path scenario's two barriers as dashed lines: shares are counted, never calibrated. */
export function pathLines(analysis: ApexAnalysis): PathLine[] {
  const p = analysis.paths;
  if (!p?.available || !p.barriers || !p.shares) return [];
  const share = (k: "UPPER_FIRST" | "LOWER_FIRST") => `${Math.round((p.shares?.[k] ?? 0) * 100)}%`;
  const tag = p.calibrated ? "" : " · not validated";
  return [
    { price: p.barriers.upper, kind: "upper", label: `upper first ${share("UPPER_FIRST")} in ${p.horizon_minutes}m${tag}` },
    { price: p.barriers.lower, kind: "lower", label: `lower first ${share("LOWER_FIRST")} in ${p.horizon_minutes}m${tag}` },
  ];
}

export function findZone(analysis: ApexAnalysis, id: string | null): ApexMapZone | null {
  const zm = analysis.zone_map;
  if (!zm || !id) return null;
  return [...zm.support, ...zm.resistance, ...zm.others].find((z) => z.zone_id === id) ?? null;
}

/** Minutes between the last bar's close and the analysis moment. */
export function dataAgeMinutes(analysis: ApexAnalysis): number | null {
  const last = analysis.data.last_bar;
  return last == null ? null : Math.max(0, Math.round((analysis.as_of - (last + 60)) / 60));
}

export const MODE_TONE: Record<string, PillTone> = {
  LIVE: "success",
  DELAYED: "info",
  HISTORICAL: "warning",
  REPLAY: "warning",
  SYNTHETIC: "warning",
};
