import type {
  JarvisState,
  LearningPhase,
  MissionStatus,
  StepStatus,
  TrainingPhase,
  TrainingVerdict,
} from "@jarvis/protocol";

export function clock(iso: string | number | Date): string {
  const date = new Date(iso);
  return date.toLocaleTimeString("en-GB", { hour12: false });
}

export function missionNumber(n: number): string {
  return String(n).padStart(3, "0");
}

export function duration(fromIso: string, toIso: string | null, now = Date.now()): string {
  const ms = Math.max(0, (toIso ? Date.parse(toIso) : now) - Date.parse(fromIso));
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

export function uptime(seconds: number): string {
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function percent(value: number): string {
  return `${Math.round(value)}%`;
}

const STATE_LABELS: Record<JarvisState, string> = {
  DORMANT: "Online",
  LISTENING: "Listening",
  UNDERSTANDING: "Understanding",
  PLANNING: "Planning",
  THINKING: "Thinking",
  DELEGATING: "Delegating",
  EXECUTING: "Executing",
  VERIFYING: "Verifying",
  WAITING_FOR_APPROVAL: "Awaiting approval",
  SPEAKING: "Speaking",
  COMPLETE: "Complete",
  FAILED: "Failed",
  PAUSED: "Paused",
};

export function stateLabel(state: JarvisState): string {
  return STATE_LABELS[state];
}

export function missionStatusLabel(status: MissionStatus, short = false): string {
  if (status === "waiting_for_approval") return short ? "Approval" : "Awaiting approval";
  return capitalize(status);
}

export function stepStatusLabel(status: StepStatus): string {
  return status === "waiting_for_approval" ? "Approval" : capitalize(status);
}

export function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** claude-sonnet-5-5 → Claude Sonnet 5.5 */
export function modelLabel(id: string): string {
  const parts = id.split("-");
  if (parts.length >= 3 && parts[0] === "claude") {
    const name = parts[1] ?? "";
    const version = parts.slice(2).filter((p) => /^\d{1,3}$/.test(p)).join(".");
    return `Claude ${name.charAt(0).toUpperCase()}${name.slice(1)} ${version}`.trim();
  }
  return id;
}

export const LEVEL_LABELS = ["Read", "Safe action", "Modification", "External effect", "High risk"];

const LEARNING_LABELS: Record<LearningPhase, string> = {
  off: "Off",
  preparing: "Preparing",
  running: "Researching",
  waiting: "Between rounds",
  budget: "Budget used up",
  stalled: "Stopped itself",
  needs_brain: "Needs Claude",
  error: "Problem",
};

export function learningLabel(state: LearningPhase): string {
  return LEARNING_LABELS[state];
}

export function usd(value: number): string {
  return `$${value.toFixed(2)}`;
}

export function signed(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}`;
}

export interface HoldoutVerdict {
  label: string;
  tone: "success" | "warning" | "danger";
  explanation: string;
}

/** What the holdout (data the research model never saw) says about a validated finding. */
export function holdoutVerdict(
  holdout: { avg_r: number; t_stat: number } | null,
  confirmed: boolean | null,
): HoldoutVerdict | null {
  if (!holdout || confirmed === null) return null;
  const t = holdout.t_stat.toFixed(1);
  if (confirmed) {
    return {
      label: `Significant on unseen data · t ${t}`,
      tone: "success",
      explanation: "Also significant on the holdout — data JARVIS never saw (t ≥ 1.6). A status, nothing to click.",
    };
  }
  if (holdout.avg_r > 0) {
    return {
      label: `Positive on unseen data, not significant · t ${t}`,
      tone: "warning",
      explanation:
        "In the plus on the holdout, but not significantly (t < 1.6) — a strategy without an edge does that half the time. Not confirmed.",
    };
  }
  return {
    label: `Failed on unseen data · t ${t}`,
    tone: "danger",
    explanation: "Lost money on the holdout — data JARVIS never saw. Probably no real edge.",
  };
}

const TRAINING_LABELS: Record<TrainingPhase, string> = {
  off: "Off",
  waiting: "Up to date",
  preparing: "Getting data",
  training: "Training",
  error: "Problem",
};

export function trainingLabel(state: TrainingPhase): string {
  return TRAINING_LABELS[state];
}

/** 0.0123 → "+1.2%" (a share as signed percent). */
export function signedPercent(share: number, digits = 1): string {
  return `${signed(share * 100, digits)}%`;
}

export interface ModelVerdict {
  label: string;
  tone: "success" | "warning" | "muted" | "faint";
  explanation: string;
}

/** What months the model never saw say about JARVIS's trained model. */
export function trainingVerdict(verdict: TrainingVerdict): ModelVerdict {
  switch (verdict) {
    case "confirmed":
      return {
        label: "Confirmed on unseen months",
        tone: "success",
        explanation:
          "Predicts significantly better than the baseline on months it never trained on. JARVIS uses its odds.",
      };
    case "unconfirmed":
      return {
        label: "Not confirmed on unseen months",
        tone: "warning",
        explanation:
          "Looked better when it was chosen, but not significantly better on months it never saw. JARVIS only gives the baseline's odds.",
      };
    case "no_edge":
      return {
        label: "No edge over the baseline",
        tone: "muted",
        explanation:
          "Not better than the baseline (level kind + how the touching candle closed). Honest result: the extra context doesn't predict more.",
      };
    default:
      return {
        label: "Too little data",
        tone: "faint",
        explanation: "Not enough level touches yet to judge the model.",
      };
  }
}
