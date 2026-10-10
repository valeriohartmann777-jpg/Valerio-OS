/** QuantLab Idea-to-Edge: sources, claims, blueprints, research missions, evolution. */

export type QsKind = "text" | "text_file" | "pdf" | "video" | "audio" | "image" | "link";
export type QsStatus =
  | "RECEIVED"
  | "QUEUED"
  | "EXTRACTING"
  | "EXTRACTED"
  | "PARTIAL"
  | "RESOLVING"
  | "NEEDS_UPLOAD"
  | "REJECTED"
  | "FAILED"
  | "CANCELED";
export type QsCapability =
  | "METADATA_ONLY"
  | "EMBED_ONLY"
  | "TRANSCRIPT_AVAILABLE"
  | "VIDEO_ACCESS_AUTHORIZED"
  | "UNAVAILABLE"
  | "REQUIRES_UPLOAD";

export interface QsSegment {
  id: string;
  seq: number;
  modality: "speech" | "onscreen" | "text" | "pdf_page" | "pdf_page_image" | "image_text" | "metadata";
  start_ms: number | null;
  end_ms: number | null;
  page: number | null;
  char_start: number | null;
  char_end: number | null;
  text: string;
  confidence: number | null;
  quality: "ok" | "low" | "unintelligible";
  provider: string;
  frame_id: string | null;
  flags: string[];
}

export interface QsFrame {
  id: string;
  t_ms: number | null;
  sha256: string;
  width: number | null;
  height: number | null;
  scene_score: number | null;
  text: string | null;
}

export type QsClaimKind = "RULE" | "PERFORMANCE_CLAIM" | "CONTEXT" | "MARKETING" | "INSTRUCTION_TO_AI" | "OTHER";

export interface QsClaim {
  id: string;
  kind: QsClaimKind;
  content: string;
  quote: string | null;
  quote_verified: boolean;
  segment_ids: string[];
  field_mapping: string[];
  extraction_confidence: "high" | "medium" | "low";
  claim_status: "defined" | "partially_defined" | "undefined" | "unsupported";
  unresolved: string[];
  trading_truth: "NOT_TESTED";
  origin: "model" | "rule";
}

export type QbClass =
  | "EXPLICIT_SOURCE"
  | "USER_SPECIFIED"
  | "INFERRED_NONCRITICAL"
  | "MISSING_BLOCKING"
  | "DEFAULT_RESEARCH_ASSUMPTION";
export type QbStatus = "DRAFT" | "NEEDS_DEFINITION" | "READY_FOR_DATA" | "READY_TO_TEST" | "INVALID";

export interface QbProvenance {
  class: QbClass;
  claim_ids: string[];
  segment_ids: string[];
  note_ids: string[];
  note: string;
  term?: string;
}

export interface QbAlternative {
  id: string;
  label: string;
  definition: string;
  patch: Record<string, unknown>;
  supported: boolean;
}

export interface QbAmbiguity {
  id: string;
  name: string;
  why: string;
  material: boolean;
  default: string | null;
  alternatives: QbAlternative[];
  segment_ids: string[];
  chosen: string | null;
  basis: "source" | "user" | "default" | "open";
}

export interface QbQuestion {
  kind: "term" | "field" | "note";
  term?: string;
  field?: string;
  question: string;
  default?: string | null;
  material?: boolean;
  options?: { id: string; label: string; definition?: string }[];
  input?: "number";
  current?: unknown;
}

export interface QbBlueprint {
  id: string;
  source_id: string | null;
  number: number;
  parent_id: string | null;
  status: QbStatus;
  spec: Record<string, unknown>;
  spec_sha256: string | null;
  provenance: Record<string, QbProvenance>;
  ambiguities: QbAmbiguity[];
  questions: QbQuestion[];
  unsupported: { feature: string; reason: string; segment_ids: string[]; origin: string }[];
  summary: string | null;
  origin: "model" | "owner";
  strategy_id: string | null;
  version_id: string | null;
  created_at: string;
  /** Derived on read from the spec: plain-language rules and a worked example with made-up prices. */
  what_it_does?: string[];
  illustration?: string[];
}

export interface QsAudit {
  id: number;
  source_id: string | null;
  action: string;
  detail: Record<string, unknown>;
  at: string;
}

export interface QsSourceRow {
  id: string;
  kind: QsKind;
  title: string;
  status: QsStatus;
  stage: string | null;
  capability: QsCapability | null;
  fallback: QsCapability | null;
  origin_url: string | null;
  filename: string | null;
  mime: string | null;
  size_bytes: number | null;
  duration_ms: number | null;
  language: string | null;
  meta: {
    coverage?: Record<string, Record<string, unknown>>;
    warnings?: string[];
    notes?: string[];
    metadata?: Record<string, unknown>;
    provider?: string;
    error?: string | null;
  };
  media_available: boolean;
  media_expires_at: string | null;
  media_deleted_at: string | null;
  linked_source_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  duplicate?: boolean;
}

export interface QsSource extends QsSourceRow {
  segments: QsSegment[];
  frames: QsFrame[];
  notes: { id: string; text: string; created_at: string }[];
  claims: QsClaim[];
  blueprints: QbBlueprint[];
  extractions: Record<string, unknown>[];
  audit: QsAudit[];
  linked?: QsSourceRow | null;
}

export type QmState =
  | "RUNNING"
  | "WAITING_USER"
  | "WAITING_APPROVAL"
  | "PAUSED"
  | "BLOCKED"
  | "COMPLETE"
  | "FAILED"
  | "CANCELED";
export type QmVerdict =
  | "SOURCE_UNAVAILABLE"
  | "RULES_UNCLEAR"
  | "DATA_INSUFFICIENT"
  | "SIMULATION_INVALID"
  | "INSUFFICIENT_EVIDENCE"
  | "REJECTED_UNDER_TESTED_ASSUMPTIONS"
  | "PROMISING_RESEARCH_CANDIDATE"
  | "ROBUST_UNDER_TESTED_ASSUMPTIONS"
  | "FORWARD_VALIDATION_REQUIRED";

export interface QmWaiting {
  kind: "questions" | "connect_data" | "data_purchase" | "lock_candidate";
  blueprint_id?: string;
  questions?: QbQuestion[];
  quote_id?: string;
  cost_usd?: number;
  mission_cap_usd?: number;
  within_mission_budget?: boolean;
  fixture?: boolean;
  request?: Record<string, unknown>;
  candidates?: QmComparisonRow[];
  note?: string;
}

export interface QmTask {
  id: string;
  mission_id: string;
  seq: number;
  agent: string;
  kind: string;
  objective: string;
  inputs_hash: string;
  allowed_tools: string[];
  budget: Record<string, unknown>;
  state: string;
  model: string | null;
  actions: { at: string; text: string; [key: string]: unknown }[];
  artifacts: Record<string, unknown>[];
  results: Record<string, unknown> | null;
  evidence_refs: string[];
  limitations: string[];
  cost_usd: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface QmEvent {
  id: number;
  mission_id: string;
  task_id: string | null;
  agent: string | null;
  kind: string;
  message: string;
  data: Record<string, unknown> | null;
  at: string;
}

export interface QmTrialMetrics {
  oos_trades: number | null;
  oos_net: number | null;
  oos_win_rate: number | null;
  oos_profit_factor: number | null;
  is_net: number | null;
  is_trades: number | null;
  max_drawdown: number | null;
  dsr: number | null;
  family_trials: number | null;
  verdict: string | null;
  would_be: string | null;
  tests: Record<string, string>;
  grid?: number;
}

export interface QmTrial {
  id: string;
  mission_id: string;
  family_id: string;
  iteration: number;
  version_id: string | null;
  parent_version_id: string | null;
  spec_sha256: string | null;
  title: string;
  changes: { path: string; value: unknown }[];
  mechanism: string | null;
  prediction: string | null;
  complexity: number;
  run_id: string | null;
  outcome: string;
  metrics: QmTrialMetrics | null;
  audit: { passed: boolean | null; sha256: string | null; second_engine: number | null } | null;
  post_holdout: boolean;
  created_at: string;
}

export interface QmComparisonRow extends QmTrialMetrics {
  id: string;
  title: string;
  version_id: string;
  iteration: number;
  complexity: number;
  pareto: boolean;
}

export interface QmCheck {
  id: string;
  title: string;
  result: "PASS" | "WARN" | "FAIL";
  detail: string;
}

export interface QmReport {
  kind: string;
  checks: QmCheck[];
  passed: boolean;
  sha256: string;
  limitation?: string;
  second_engine?: { compared: number; second_engine_trades: number; agree: number; mismatch_count: number; mismatches: string[] };
}

export interface QmMissionRow {
  id: string;
  kind: "idea" | "evolution";
  source_id: string | null;
  parent_id: string | null;
  title: string;
  state: QmState;
  stage: string | null;
  verdict: QmVerdict | null;
  spent_usd: number;
  created_at: string;
  updated_at: string;
  waiting: QmWaiting | null;
}

export interface QmMission extends QmMissionRow {
  budget: Record<string, number>;
  budget_sha256: string;
  refs: {
    blueprint_id?: string;
    strategy_id?: string;
    version_id?: string;
    spec_sha256?: string;
    dataset_id?: string;
    run_id?: string;
    quote_id?: string | null;
    job_id?: string | null;
    protocol?: Record<string, unknown> & { sha256: string; months: number; min_trades_oos: number };
    boundary?: QmReport;
    audit?: QmReport;
    verdict?: {
      label: QmVerdict;
      reasons: string[];
      note: { text: string; next_step: string; checked: boolean } | null;
      fixture?: boolean;
    };
    comparison?: { pareto: string[]; rows: QmComparisonRow[]; notes: string[] };
    diagnosis?: { findings: string[] } & Record<string, unknown>;
    holdout?: { version_id: string; run_id: string; independent?: boolean; status?: string };
    family_id?: string;
    claims?: { extraction_id: string; count: number; kinds: Record<string, number>; summary: string };
    [key: string]: unknown;
  };
  error: string | null;
  tasks: QmTask[];
  events: QmEvent[];
  trials: QmTrial[];
  stages: string[];
  model_label: string;
  blueprint: QbBlueprint | null;
}

export interface QmAgent {
  id: string;
  name: string;
  role: string;
  engine: string;
  active: boolean;
  status: "working" | "idle" | "not_used";
  current: { task_id: string; mission_id: string; kind: string; objective: string }[];
  tasks: Record<string, number>;
  spent_usd: number;
}

export interface QmDossier {
  markdown: string;
  json: Record<string, unknown>;
  verdict: QmVerdict | null;
}

export interface QsSpeechModel {
  default: string;
  multilingual: string;
  installed: boolean;
  job: { status: string; error?: string };
}
