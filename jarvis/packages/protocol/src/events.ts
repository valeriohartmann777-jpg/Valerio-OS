/**
 * Event contract between the JARVIS backend and its clients.
 * Mirror of backend/jarvis/events/types.py — kept in sync by
 * backend/tests/test_protocol_sync.py.
 */

export const EVENT_TYPES = [
  "system.online",
  "jarvis.state.changed",
  "jarvis.message",
  "jarvis.reasoning",
  "command.received",
  "intent.classified",
  "mission.created",
  "mission.updated",
  "agent.started",
  "agent.updated",
  "agent.completed",
  "agent.failed",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "verification.completed",
  "permission.requested",
  "permission.approved",
  "permission.rejected",
  "permission.expired",
  "model.completed",
  "brain.changed",
  "voice.changed",
  "learning.changed",
  "memory.changed",
  "context.updated",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

export const SEVERITIES = ["debug", "info", "important", "warning", "error"] as const;

export type Severity = (typeof SEVERITIES)[number];

export const JARVIS_STATES = [
  "DORMANT",
  "LISTENING",
  "UNDERSTANDING",
  "PLANNING",
  "THINKING",
  "DELEGATING",
  "EXECUTING",
  "VERIFYING",
  "WAITING_FOR_APPROVAL",
  "SPEAKING",
  "COMPLETE",
  "FAILED",
  "PAUSED",
] as const;

export type JarvisState = (typeof JARVIS_STATES)[number];

export interface JarvisEvent<P = Record<string, unknown>> {
  id: string;
  type: EventType;
  timestamp: string;
  severity: Severity;
  source: string;
  message: string;
  trace_id: string | null;
  mission_id: string | null;
  payload: P;
}
