/**
 * Domain models as they cross the wire (JSON). Mirrors the Pydantic models in
 * backend/jarvis/{missions,agents,permissions,tools,core}.
 */

import type { JarvisEvent, JarvisState } from "./events";

export interface ToolError {
  code: string;
  message: string;
  suggestion?: string | null;
  detail?: string | null;
}

export type VerificationStatus = "verified" | "failed" | "unverifiable";

export interface Verification {
  status: VerificationStatus;
  method: string;
  summary: string;
  evidence: string[];
}

export interface ToolResult {
  success: boolean;
  tool: string;
  action: string;
  target: string | null;
  summary: string;
  data: Record<string, unknown>;
  observations: string[];
  observed_result: string | null;
  error: ToolError | null;
  simulated: boolean;
  duration_ms: number;
}

export type MissionStatus =
  | "pending"
  | "active"
  | "paused"
  | "waiting_for_approval"
  | "complete"
  | "failed"
  | "stopped";

export type StepStatus =
  | "waiting"
  | "active"
  | "waiting_for_approval"
  | "complete"
  | "failed"
  | "rejected"
  | "skipped";

export interface MissionStep {
  id: string;
  index: number;
  title: string;
  kind: "action" | "verify";
  agent: string;
  tool: string | null;
  args: Record<string, unknown>;
  depends_on: number | null;
  status: StepStatus;
  summary: string | null;
  error: ToolError | null;
  result: ToolResult | null;
  verification: Verification | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Mission {
  id: string;
  number: number;
  title: string;
  goal: string;
  status: MissionStatus;
  priority: "low" | "normal" | "high";
  current_step: number | null;
  steps: MissionStep[];
  agents: string[];
  tools_used: string[];
  approvals: string[];
  result: string | null;
  errors: ToolError[];
  verification: Verification | null;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export type AgentStatus = "idle" | "active" | "waiting" | "standby";

export interface AgentState {
  id: string;
  name: string;
  role: string;
  description: string;
  available: boolean;
  available_in_phase: number | null;
  tools: string[];
  max_permission_level: number;
  status: AgentStatus;
  current_task: string | null;
  detail: string | null;
  mission_id: string | null;
  last_outcome: "success" | "failure" | null;
  last_summary: string | null;
  updated_at: string;
}

export type PermissionLevel = 0 | 1 | 2 | 3 | 4;
export type ApprovalPolicy = "auto" | "confirm" | "strong_confirm" | "deny";

export interface ActionDescriptor {
  title: string;
  target: string | null;
  summary: string;
  effects: string[];
  level: PermissionLevel;
  category: string | null;
  reversible: boolean;
}

export interface PermissionRequest {
  id: string;
  tool: string;
  action: ActionDescriptor;
  reason: string;
  policy: ApprovalPolicy;
  status: "pending" | "approved" | "rejected" | "expired";
  agent: string | null;
  trace_id: string | null;
  mission_id: string | null;
  created_at: string;
  expires_at: string | null;
  decided_at: string | null;
  decision_note: string | null;
}

export interface EnvironmentContext {
  hostname: string;
  platform: string;
  system_backend: string;
  simulated: boolean;
  active_app: string | null;
  active_window: string | null;
  cpu_percent: number;
  memory_percent: number;
  memory_used_gb: number;
  memory_total_gb: number;
  uptime_seconds: number;
  updated_at: string;
}

export interface StateSnapshot {
  state: JarvisState;
  detail: string;
}

export interface BrainStatus {
  available: boolean;
  fast_model: string | null;
  reasoning_model: string | null;
  /** Why the reasoning model is offline (e.g. no API key). */
  reason: string | null;
  /** Last four characters of the API key in use. */
  key_hint: string | null;
}

export type VoicePhase = "off" | "unavailable" | "ready" | "listening" | "transcribing" | "speaking";

export interface VoiceStatus {
  state: VoicePhase;
  /** An ElevenLabs key is set. */
  configured: boolean;
  /** The user wants “Hey JARVIS” … */
  wake_word: boolean;
  /** … and it is listening right now. */
  wake_word_active: boolean;
  /** Speak replies to typed commands too. */
  speak_replies: boolean;
  voice_name: string;
  key_hint: string | null;
  /** Why voice is off/unavailable, or a wake-word problem. */
  reason: string | null;
}

export interface Snapshot {
  version: string;
  build: string;
  brain: BrainStatus;
  voice: VoiceStatus;
  state: StateSnapshot;
  system_backend: string;
  simulated: boolean;
  context: EnvironmentContext | null;
  agents: AgentState[];
  missions: Mission[];
  approvals: PermissionRequest[];
  activity: JarvisEvent[];
}

export interface JarvisMessagePayload {
  text: string;
  success: boolean;
  error: Omit<ToolError, "detail"> | null;
}
