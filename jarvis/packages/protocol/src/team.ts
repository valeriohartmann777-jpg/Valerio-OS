/** Team ULTRON HQ (JARVIS 2.0): GET /team/hq — a projection of real work, nothing simulated. */

export type HqState =
  | "working"
  | "waiting_approval"
  | "blocked"
  | "failed"
  | "reserved"
  | "complete"
  | "idle"
  | "offline";

export type HqRoomId = "homebase" | "workshop" | "quantlab" | "research" | "academy";

export interface HqWork {
  source: "ultron" | "quantlab" | "desktop" | "team";
  agent: string;
  state: HqState;
  room: HqRoomId;
  mission_id: string;
  mission_title: string;
  task_id: string;
  title: string;
  finished_at: string | null;
  detail: string | null;
}

export interface HqRobot {
  id: string;
  name: string;
  specialization: string;
  summary: string;
  color: string;
  home_room: HqRoomId;
  available: boolean;
  state: HqState;
  state_label: string;
  room: HqRoomId;
  reason: string | null;
  work: HqWork[];
  last_output: { title: string; mission_title: string; finished_at: string | null; source: string } | null;
  spent_usd: number;
  tools: string[];
  writes: string | null;
  must_not: string | null;
  assign: { possible: boolean; how: string | null; room?: HqRoomId | null; reason: string | null };
}

export interface HqRoom {
  id: HqRoomId;
  name: string;
  description: string;
  robots: string[];
  open_missions: number;
}

export interface HqSnapshot {
  generated_at: string;
  robots: HqRobot[];
  rooms: HqRoom[];
  ai: { ready: boolean; route: string; reason: string };
  jarvis: { state: "planning" | "idle"; planning: string[]; label: string };
  evidence: string;
}

/** ULTRON QuantLab team workspaces (JARVIS 2.0): /team/quantlab/projects. */
export type TmGrant = "read_spec" | "run_backtests" | "propose_versions";

export interface TmTask {
  id: string;
  project_id: string;
  agent: "sentinel" | "cipher";
  kind: "challenge" | "improve";
  state: "QUEUED" | "RUNNING" | "COMPLETE" | "FAILED";
  request: { run_id: string; version_id: string };
  result: Record<string, unknown> | null;
  mission_id: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface TmAudit {
  id: number;
  project_id: string;
  at: string;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
}

export interface TmProject {
  id: string;
  name: string;
  solo_strategy_id: string;
  solo_version_id: string;
  team_strategy_id: string;
  grants: Record<TmGrant, boolean>;
  grant_labels: Record<TmGrant, string>;
  state: "ACTIVE" | "CLOSED";
  created_at: string;
  updated_at: string;
  team_strategy: {
    id: string;
    name: string;
    versions: { id: string; number: number; origin: string; note: string | null; created_at: string; spec_sha256: string; parent_id: string | null; state: string }[];
  };
  runs: import("./quantlab-research").QrRunRow[];
  tasks: TmTask[];
  missions: { id: string; state: string; stage: string | null; title: string; trials: import("./quantlab-ideas").QmTrial[] }[];
  audit: TmAudit[];
}

/** Owner feedback on replies (Training Hub): GET/POST /feedback. */
export interface FeedbackItem {
  id: number;
  at: string;
  event_id: string;
  trace_id: string | null;
  rating: 1 | -1;
  note: string | null;
  reply: string;
  command: string | null;
  intent: string | null;
  route: string | null;
}

export interface FeedbackOverview {
  items: FeedbackItem[];
  by_route: { route: string; up: number; down: number }[];
  up: number;
  down: number;
  ratings: Record<string, 1 | -1>;
}

/** Training Hub → Evaluations: what JARVIS must understand from a sentence (local router). */
export interface EvalExpect {
  kind?: "query" | "action" | "conversation" | "unsupported";
  tool?: string | null;
  topic?: string | null;
  targets?: string[];
}

export interface EvalCase {
  id: string;
  text: string;
  expect: EvalExpect;
  source: "curated" | "pinned";
  created_at?: string;
}

export interface EvalResult {
  id: string;
  text: string;
  source: "curated" | "pinned";
  expect: EvalExpect;
  got: Required<EvalExpect>;
  passed: boolean;
  wrong: string[];
}

export interface EvalOverview {
  cases: EvalCase[];
  pinned: EvalCase[];
  pinned_events: string[];
  last_run: { at: string; total: number; passed: number; results: EvalResult[] } | null;
}
