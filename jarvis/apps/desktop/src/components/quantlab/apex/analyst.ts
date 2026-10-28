import type { ApexAnalysis, ApexDecision, ApexSignal, ApexSignalStatus, ApexStrategyName, ApexValidationState } from "@jarvis/protocol";

import type { PillTone } from "../../ui/shell";

/**
 * Pure helpers for the APEX Analyst Panel. Three states are kept apart on purpose:
 * the APP is online (backend + health), the DATA is fresh and passed the gate, and a SIGNAL is
 * validated — one never implies another.
 */

export const STRATEGIES: { id: ApexStrategyName; label: string; entry: string }[] = [
  { id: "zone_rejection_5m", label: "5-min rejection", entry: "stop entry after a confirmed 5-minute trigger" },
  { id: "zone_reaction", label: "Zone reaction", entry: "limit order resting at the zone" },
];

export const SIGNAL_TONE: Record<ApexSignalStatus, PillTone> = {
  PAPER_VALIDATED: "success",
  CANDIDATE: "info",
  WAITING_FOR_TRIGGER: "warning",
  NO_TRADE: "muted",
  BLOCKED: "danger",
};

export const VALIDATION_TONE: Record<ApexValidationState, PillTone> = {
  FORWARD_SUPPORTED: "success",
  OOS_SUPPORTED: "success",
  RESEARCH_ONLY: "warning",
  UNVALIDATED: "muted",
  REJECTED: "danger",
};

export const VALIDATION_TEXT: Record<ApexValidationState, string> = {
  UNVALIDATED: "Not validated — no validation run of this strategy version yet.",
  RESEARCH_ONLY: "Research only — too little evidence, or only generated test data.",
  OOS_SUPPORTED: "Supported out of sample — passed development and the untouched holdout.",
  FORWARD_SUPPORTED: "Supported forward — the paper journal confirmed it on new data.",
  REJECTED: "Rejected — failed the pre-registered criteria. Do not trade it.",
};

export interface Readiness {
  ready: boolean;
  tone: PillTone;
  text: string;
  reason: string;
}

/** DATA READY / BLOCKED for the analysis — the gate's verdict, the staleness and tradability. */
export function dataReadiness(analysis: ApexAnalysis): Readiness {
  const q = analysis.quality;
  const blocks = q.findings.filter((f) => f.level === "BLOCK");
  if (analysis.blocked || q.status === "BLOCK") {
    return { ready: false, tone: "danger", text: "Data blocked", reason: blocks.map((f) => `${f.code}: ${f.message}`).join(" ") || "Quality gate failed." };
  }
  if (analysis.execution && !analysis.execution.tradable) {
    return { ready: false, tone: "danger", text: "Data unavailable", reason: analysis.execution.reason };
  }
  if (q.stale) {
    const stale = q.findings.find((f) => f.code === "STALE");
    return { ready: false, tone: "danger", text: "Data stale", reason: stale?.message ?? "The newest bar is too old." };
  }
  if (analysis.data.synthetic) {
    return { ready: true, tone: "warning", text: "Test data", reason: "Generated test data — mechanics only, never a market statement." };
  }
  if (q.status === "WARN") {
    const warn = q.findings.find((f) => f.level === "WARN");
    return { ready: true, tone: "warning", text: "Data ready (warnings)", reason: warn ? `${warn.code}: ${warn.message}` : "" };
  }
  return { ready: true, tone: "success", text: "Data ready", reason: "Passed the quality gate; newest bar within the limit." };
}

export function decisionOf(analysis: ApexAnalysis, strategy: ApexStrategyName): ApexDecision | null {
  return analysis.strategies?.[strategy] ?? null;
}

/** The signals of one strategy in this analysis (one per order plan, or one without a plan). */
export function signalsOf(analysis: ApexAnalysis, strategy: ApexStrategyName): ApexSignal[] {
  return (analysis.signals ?? []).filter((s) => s.strategy_id === strategy);
}

export interface Chip {
  key: "app" | "data" | "signal";
  label: string;
  text: string;
  tone: PillTone;
}

export function statusChips(
  app: { text: string; tone: "success" | "warning" | "danger" },
  analysis: ApexAnalysis | null,
  signal: ApexSignal | null,
): Chip[] {
  const data = analysis ? dataReadiness(analysis) : null;
  const state = signal?.validation_state ?? null;
  return [
    { key: "app", label: "App", text: app.text, tone: app.tone },
    { key: "data", label: "Data", text: data ? data.text : "No analysis yet", tone: data ? data.tone : "muted" },
    {
      key: "signal",
      label: "Signal",
      text: signal ? `${signal.status.replace(/_/g, " ").toLowerCase()} · ${(state ?? "UNVALIDATED").replace(/_/g, " ").toLowerCase()}` : "—",
      tone: state && signal?.status !== "BLOCKED" ? VALIDATION_TONE[state] : signal ? SIGNAL_TONE[signal.status] : "muted",
    },
  ];
}

export function executionText(analysis: ApexAnalysis): string {
  const e = analysis.execution;
  if (!e) return `${analysis.spec.cost_points} points round-trip costs (flat).`;
  if (!e.tradable) return e.reason;
  if (e.quotes === "bid") {
    const how = e.spread_source === "measured" ? "measured from ask candles" : "an estimate — ask candles not measured";
    return `Bid prices only (execution assumption limited): buys modelled at bid + ${e.spread} (${how}), plus ${e.costs} slippage/commission per round trip.`;
  }
  if (e.quotes === "trades") return `Traded futures prices; ${e.costs} points per round trip deducted.`;
  return `Generated test data; ${e.costs} points per round trip deducted.`;
}
