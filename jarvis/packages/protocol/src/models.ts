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

export type LearningPhase =
  | "off"
  | "preparing"
  | "running"
  | "waiting"
  | "budget"
  | "stalled"
  | "needs_brain"
  | "error";

export interface LearningStatus {
  state: LearningPhase;
  /** The user switched learning on (it resumes after restarts). */
  enabled: boolean;
  detail: string | null;
  /** Market data download, 0–1. */
  progress: number | null;
  model: string;
  budget_usd: number;
  spent_today_usd: number;
  /** Completed rounds since the last validated finding (or since it was started). */
  stall_rounds: number;
  stall_limit: number;
  next_round_at: string | null;
  round: number | null;
  web_search: boolean;
  /** What to research (the user's text, or the default from config/learning.yaml). */
  focus: string;
  counts: Partial<
    Record<
      | "tests"
      | "invalid"
      | "rejected"
      | "in_sample_passed"
      | "validated"
      | "confirmed"
      | "notes"
      | "rounds"
      | "studies",
      number
    >
  >;
  data: Record<string, { first: string; last: string; bars: number }>;
}

export interface LearningStats {
  trades: number;
  win_rate: number;
  avg_r: number;
  t_stat: number;
  total_r: number;
  profit_factor: number;
  max_drawdown_r: number;
  avg_points: number;
  trades_per_month: number;
  avg_minutes: number;
  long_trades: number;
  short_trades: number;
  exits: Record<string, number>;
  by_year: Record<string, { trades: number; avg_r: number }>;
}

export type LearningTestStatus = "invalid" | "rejected" | "oos_failed" | "validated";

export interface LearningTest {
  number: number;
  created_at: string;
  name: string;
  instrument: string | null;
  style: string | null;
  timeframe: string | null;
  status: LearningTestStatus;
  reason: string;
  spec: Record<string, unknown> & { hypothesis?: string };
  in_sample: LearningStats | null;
  out_of_sample: LearningStats | null;
  /** Never shown to the research model. */
  holdout: LearningStats | null;
  holdout_confirmed: boolean | null;
  t_required: number | null;
}

export interface LevelStudyResult {
  touches: number;
  held: number;
  broken: number;
  /** Broken already within the touching bar. */
  broken_on_touch: number;
  undecided: number;
  /** Share of decided touches that held. */
  held_rate: number | null;
  /** How often arbitrary prices touched the same way "held" (the control group). */
  expected_rate: number | null;
  controls: number;
  edge_z: number | null;
  by_touch: Record<"first" | "second" | "third_or_later", { decided: number; held_rate: number | null }>;
  by_year: Record<string, { touches: number; held_rate: number | null; expected_rate: number | null }>;
  avg_favourable_points: number | null;
  avg_adverse_points: number | null;
  error?: string;
}

export interface LearningStudy {
  number: number;
  created_at: string;
  name: string;
  instrument: string | null;
  ok: boolean;
  spec: { question?: string; level?: string; side?: "support" | "resistance"; timeframe?: string } & Record<
    string,
    unknown
  >;
  result: LevelStudyResult;
}

export interface LearningNote {
  number: number;
  created_at: string;
  updated_at: string;
  topic: string;
  text: string;
  sources: string[];
}

export interface LearningRound {
  number: number;
  day: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "completed" | "failed" | "interrupted";
  summary: string;
  next_focus: string;
  cost_usd: number;
  searches: number;
  error: string | null;
}

export interface BriefingStatus {
  enabled: boolean;
  /** HH:MM, this computer's local time. */
  time: string;
  /** 0 = Monday. */
  weekdays: number[];
  catch_up_until: string;
  next_at: string | null;
  /** Local date (YYYY-MM-DD) of the last briefing. */
  last_sent: string | null;
  last_error: string | null;
}

export type MemoryKind = "preference" | "fact" | "routine" | "correction";

export interface MemoryItem {
  number: number;
  kind: MemoryKind;
  text: string;
  created_at: string;
  updated_at: string;
  /** "conversation" (JARVIS stored it) or "dashboard" (added by hand). */
  source: string;
}

export interface Snapshot {
  version: string;
  build: string;
  brain: BrainStatus;
  voice: VoiceStatus;
  learning: LearningStatus;
  memories: MemoryItem[];
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
