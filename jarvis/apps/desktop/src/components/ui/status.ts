import type { AgentStatus, JarvisEvent, MissionStatus, StepStatus } from "@jarvis/protocol";

import type { Tone } from "./primitives";

export const MISSION_TONE: Record<MissionStatus, Tone> = {
  pending: "faint",
  active: "accent",
  paused: "muted",
  waiting_for_approval: "warning",
  complete: "success",
  failed: "danger",
  stopped: "muted",
};

export const STEP_TONE: Record<StepStatus, Tone> = {
  waiting: "faint",
  active: "accent",
  waiting_for_approval: "warning",
  complete: "success",
  failed: "danger",
  rejected: "warning",
  skipped: "faint",
};

export const AGENT_TONE: Record<AgentStatus, Tone> = {
  idle: "faint",
  active: "accent",
  waiting: "warning",
  standby: "faint",
};

export const SEVERITY_TEXT: Record<JarvisEvent["severity"], string> = {
  debug: "text-fg-faint",
  info: "text-fg-muted",
  important: "text-fg",
  warning: "text-warning",
  error: "text-danger",
};
