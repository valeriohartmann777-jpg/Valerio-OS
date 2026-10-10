// ULTRON: JARVIS's development team (mirrors backend/jarvis/ultron).

export type UlMissionState =
  | "PLANNING"
  | "RUNNING"
  | "PAUSED"
  | "WAITING_APPROVAL"
  | "BLOCKED"
  | "COMPLETE"
  | "FAILED"
  | "CANCELLED";

export type UlTaskState =
  | "DRAFT"
  | "READY"
  | "RUNNING"
  | "VERIFYING"
  | "RETRYING"
  | "WAITING_APPROVAL"
  | "COMPLETE"
  | "BLOCKED"
  | "FAILED"
  | "CANCELLED";

export type UlProjectKind = "sandbox" | "jarvis";

export interface UlBlocker {
  kind: string;
  message?: string;
  questions?: string[];
  task?: string;
}

export interface UlCharter {
  title: string;
  objective: string;
  in_scope: string[];
  out_of_scope: string[];
  assumptions: string[];
  blocking_unknowns: string[];
  success_criteria: { criterion: string; check?: string[] | null }[];
  answers?: string[];
}

export interface UlCheckResult {
  command: string;
  exit_code: number | null;
  passed: boolean;
  timed_out: boolean;
  seconds: number;
}

export interface UlCriterionResult {
  criterion: string;
  met: boolean;
  evidence: string;
}

export interface UlTaskResult {
  summary?: string | null;
  criteria?: UlCriterionResult[];
  checks?: UlCheckResult[];
  commit?: string;
  integrated?: string;
  patch_artifact?: string;
  // reviews
  verdict?: string;
  findings?: { severity: string; issue: string; evidence: string }[];
  runtime_checks?: UlCheckResult[];
  runtime_checks_passed?: boolean;
  reviewed?: string[];
  accepted?: boolean;
  report_artifact?: string;
}

export interface UlTask {
  id: string;
  mission_id: string;
  key: string;
  title: string;
  owner: string;
  depends_on: string[];
  contract: {
    objective: string;
    deliverables: string[];
    done_when: string[];
    allowed_paths: string[];
    checks: string[][];
    depth: number;
    reviews: string[];
  };
  state: UlTaskState;
  attempts: number;
  max_attempts: number;
  feedback: string | null;
  result: UlTaskResult | null;
  cost_usd: number;
  started_at: string | null;
  finished_at: string | null;
  running: boolean;
}

export interface UlRun {
  id: string;
  mission_id: string;
  task_id: string | null;
  agent: string;
  attempt: number;
  state: string;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  tool_calls: number;
  summary: string | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface UlArtifact {
  id: string;
  mission_id: string;
  task_id: string | null;
  run_id: string | null;
  agent: string;
  kind: string;
  name: string;
  sha256: string;
  bytes: number;
  created_at: string;
}

export interface UlArtifactContent extends UlArtifact {
  content: string;
  intact: boolean;
}

export interface UlApproval {
  id: string;
  mission_id: string;
  task_id: string | null;
  category: string;
  title: string;
  effect: Record<string, unknown>;
  signature: string;
  state: "pending" | "approved" | "rejected" | "executed" | "failed";
  requested_by: string;
  requested_at: string;
  decided_at: string | null;
  result: string | null;
}

export interface UlReport {
  criteria: { criterion: string; status: "verified" | "reviewed" | "failed" | "not_checked"; evidence: string }[];
  final_checks: UlCheckResult[];
  tasks: { key: string; owner: string; attempts: number; summary: string | null }[];
  files: string[];
  branch: string;
  head_commit: string;
  spent_usd: number;
  isolation: string;
  model: string;
  delivery?: string;
  exported_to?: string;
}

export interface UlMissionRow {
  id: string;
  number: number;
  title: string;
  state: UlMissionState;
  project_kind: UlProjectKind;
  spent_usd: number;
  budget_usd: number;
  created_at: string;
  updated_at: string;
  blocker: UlBlocker | null;
  model_label: string | null;
  progress: { complete: number; total: number };
  owners: string[];
}

export interface UlMission extends Omit<UlMissionRow, "owners"> {
  goal: string;
  charter: UlCharter | null;
  plan_notes: string[] | null;
  revision: number;
  workspace: string;
  base_commit: string | null;
  head_commit: string | null;
  report: UlReport | null;
  finished_at: string | null;
  paused: boolean;
  tasks: UlTask[];
  runs: UlRun[];
  artifacts: UlArtifact[];
  approvals: UlApproval[];
}

export interface UlAgent {
  id: string;
  name: string;
  role: string;
  deliverables: string;
  tools: string[];
  writes: string;
  must_not: string;
  active: boolean;
  release: string;
  status: "working" | "idle" | "not_active";
  current: { task_id: string | null; mission_id: string; key: string; title: string; state: string }[];
  runs: Record<string, number>;
  spent_usd: number;
  model: string | null;
}

export interface UlEvent {
  id: number;
  at: string;
  mission_id: string | null;
  task_id: string | null;
  agent: string | null;
  kind: string;
  severity: "debug" | "info" | "important" | "warning" | "error";
  message: string;
  data: Record<string, unknown>;
}

export interface UlConfig {
  enabled: boolean;
  budget_usd_per_mission: number;
  max_parallel_workers: number;
  max_attempts: number;
  max_rounds_per_run: number;
  command_timeout_seconds: number;
  max_delegation_depth: number;
  export_dir: string;
  agents: Record<string, { model: string; effort: string | null; max_tokens: number }>;
}

export interface UlOverview {
  enabled: boolean;
  model_ready: boolean;
  model_label: string;
  scripted: boolean;
  isolation: { kind: "seatbelt" | "netns" | "none"; network_blocked: boolean; writes_confined: boolean; detail: string };
  workers: { busy: number; limit: number };
  spend: { today_usd: number; week_usd: number };
  counts: { active: number; complete: number; blocked: number; total: number };
  pending_approvals: UlApproval[];
  missions: UlMissionRow[];
  agents: UlAgent[];
  config: UlConfig;
  policy: { category: string; decision: "auto" | "approval" | "locked"; description: string }[];
  repo_available: boolean;
}

export interface UlKnowledge {
  specs: UlArtifact[];
  reports: UlArtifact[];
  reviews: UlArtifact[];
}
