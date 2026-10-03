import type { JarvisState, MissionStatus, StepStatus } from "@jarvis/protocol";

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
