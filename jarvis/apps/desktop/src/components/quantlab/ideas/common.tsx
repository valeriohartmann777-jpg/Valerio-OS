import type { QbClass, QbStatus, QmState, QmVerdict, QsClaimKind, QsStatus } from "@jarvis/protocol";

import { Badge } from "../ui";

/** Idea-to-Edge status pieces: every state is an icon + a word, never colour alone. */

type Tone = "accent" | "positive" | "warning" | "danger" | "muted";

const SOURCE: Record<QsStatus, { tone: Tone; icon: string; label: string }> = {
  RECEIVED: { tone: "muted", icon: "○", label: "Received" },
  QUEUED: { tone: "muted", icon: "○", label: "Queued" },
  EXTRACTING: { tone: "accent", icon: "◌", label: "Reading" },
  EXTRACTED: { tone: "positive", icon: "✓", label: "Read" },
  PARTIAL: { tone: "warning", icon: "◐", label: "Partly read" },
  RESOLVING: { tone: "accent", icon: "◌", label: "Checking link" },
  NEEDS_UPLOAD: { tone: "warning", icon: "↥", label: "Needs the file" },
  REJECTED: { tone: "danger", icon: "⊘", label: "Rejected" },
  FAILED: { tone: "danger", icon: "✕", label: "Failed" },
  CANCELED: { tone: "muted", icon: "■", label: "Canceled" },
};

export function SourceStatus({ status }: { status: QsStatus }) {
  const s = SOURCE[status];
  return (
    <Badge tone={s.tone} icon={s.icon} testId="qs-status">
      {s.label}
    </Badge>
  );
}

const MISSION: Record<QmState, { tone: Tone; icon: string; label: string }> = {
  RUNNING: { tone: "accent", icon: "◌", label: "Running" },
  WAITING_USER: { tone: "warning", icon: "?", label: "Needs you" },
  WAITING_APPROVAL: { tone: "warning", icon: "$", label: "Needs approval" },
  PAUSED: { tone: "muted", icon: "‖", label: "Paused" },
  BLOCKED: { tone: "danger", icon: "⊘", label: "Blocked" },
  COMPLETE: { tone: "positive", icon: "✓", label: "Complete" },
  FAILED: { tone: "danger", icon: "✕", label: "Failed" },
  CANCELED: { tone: "muted", icon: "■", label: "Canceled" },
};

export function MissionState({ state }: { state: QmState }) {
  const s = MISSION[state];
  return (
    <Badge tone={s.tone} icon={s.icon} testId="qm-state">
      {s.label}
    </Badge>
  );
}

const VERDICT: Record<QmVerdict, { tone: Tone; icon: string; label: string }> = {
  SOURCE_UNAVAILABLE: { tone: "muted", icon: "⊘", label: "Source unavailable" },
  RULES_UNCLEAR: { tone: "warning", icon: "?", label: "Rules unclear" },
  DATA_INSUFFICIENT: { tone: "warning", icon: "◐", label: "Data insufficient" },
  SIMULATION_INVALID: { tone: "danger", icon: "⊘", label: "Simulation invalid" },
  INSUFFICIENT_EVIDENCE: { tone: "warning", icon: "◐", label: "Insufficient evidence" },
  REJECTED_UNDER_TESTED_ASSUMPTIONS: { tone: "danger", icon: "✕", label: "Rejected under tested assumptions" },
  PROMISING_RESEARCH_CANDIDATE: { tone: "positive", icon: "◆", label: "Promising research candidate" },
  ROBUST_UNDER_TESTED_ASSUMPTIONS: { tone: "positive", icon: "✓", label: "Robust under tested assumptions" },
  FORWARD_VALIDATION_REQUIRED: { tone: "accent", icon: "▲", label: "Forward validation required" },
};

export function MissionVerdict({ verdict }: { verdict: QmVerdict | null }) {
  if (!verdict) return <Badge tone="muted">No verdict yet</Badge>;
  const v = VERDICT[verdict];
  return (
    <Badge tone={v.tone} icon={v.icon} testId="qm-verdict">
      {v.label}
    </Badge>
  );
}

export const CLASS_LABEL: Record<QbClass, { tone: Tone; icon: string; label: string; hint: string }> = {
  EXPLICIT_SOURCE: { tone: "positive", icon: "❝", label: "Source", hint: "Stated in the source; the quote is checked word for word." },
  USER_SPECIFIED: { tone: "accent", icon: "✎", label: "You", hint: "You defined it (answer or note)." },
  INFERRED_NONCRITICAL: { tone: "muted", icon: "~", label: "Inferred", hint: "Inferred; never used for an entry, exit or risk rule." },
  DEFAULT_RESEARCH_ASSUMPTION: { tone: "warning", icon: "≈", label: "Research default", hint: "A declared test default. The source did not say this." },
  MISSING_BLOCKING: { tone: "danger", icon: "?", label: "Missing", hint: "Must be defined before anything is tested." },
};

export function ClassBadge({ cls }: { cls: QbClass }) {
  const c = CLASS_LABEL[cls];
  return (
    <Badge tone={c.tone} icon={c.icon} title={c.hint}>
      {c.label}
    </Badge>
  );
}

const BLUEPRINT: Record<QbStatus, { tone: Tone; icon: string; label: string }> = {
  DRAFT: { tone: "muted", icon: "○", label: "Draft" },
  NEEDS_DEFINITION: { tone: "warning", icon: "?", label: "Needs definition" },
  READY_FOR_DATA: { tone: "accent", icon: "▸", label: "Ready for data" },
  READY_TO_TEST: { tone: "positive", icon: "✓", label: "Ready to test" },
  INVALID: { tone: "danger", icon: "⊘", label: "Invalid" },
};

export function BlueprintStatus({ status }: { status: QbStatus }) {
  const b = BLUEPRINT[status];
  return (
    <Badge tone={b.tone} icon={b.icon} testId="qb-status">
      {b.label}
    </Badge>
  );
}

export const CLAIM_KIND: Record<QsClaimKind, { tone: Tone; label: string }> = {
  RULE: { tone: "accent", label: "Rule" },
  PERFORMANCE_CLAIM: { tone: "warning", label: "Performance claim" },
  CONTEXT: { tone: "muted", label: "Context" },
  MARKETING: { tone: "muted", label: "Marketing" },
  INSTRUCTION_TO_AI: { tone: "danger", label: "Instruction to AI" },
  OTHER: { tone: "muted", label: "Other" },
};

/** Milliseconds → m:ss.s (source timecodes). */
export function tc(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  const s = ms / 1000;
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

export const STAGE_LABEL: Record<string, string> = {
  register: "Register source",
  claims: "Extract claims",
  blueprint: "Blueprint",
  boundary: "Source boundary",
  freeze: "Freeze version",
  protocol: "Test protocol",
  data: "Data",
  baseline: "Baseline backtest",
  audit: "SENTINEL audit",
  verdict: "Verdict",
  diagnose: "Diagnose",
  propose: "Propose variants",
  test: "Test variants",
  compare: "Compare",
  holdout: "Holdout (once)",
};

export const OPEN_STATES: QmState[] = ["RUNNING", "WAITING_USER", "WAITING_APPROVAL", "PAUSED", "BLOCKED"];
