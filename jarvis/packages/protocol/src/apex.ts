/** JARVIS APEX — market structure, S/R zones, scenarios and trade candidates (research only). */

export type ApexMarketName = "XAUUSD" | "NQ" | "MNQ";

export interface ApexRate {
  /** Counted share; null when fewer than the minimum number of cases ("insufficient"). */
  p: number | null;
  ci: [number, number] | null;
  n: number;
  successes: number;
  days: number;
  status: "ok" | "insufficient";
}

export interface ApexTrackBrief {
  evaluation_id: string;
  /** Calibration of the stated rates in that period (APEX-3). */
  calibration?: { code: "CALIBRATED" | "MISCALIBRATED" | "INSUFFICIENT"; skill: number | null; touches: number } | null;
  period: [string, string];
  synthetic: boolean;
  data: string | null;
  verdict: { code: string; text: string } | null;
  filled: number | null;
  win_rate: number | null;
  expectancy_r: number | null;
  oos_filled: number | null;
  oos_expectancy_r: number | null;
  oos_ci: [number, number] | null;
}

export interface ApexMarket {
  market: ApexMarketName;
  label: string;
  unit: string;
  tick: number;
  point_value: number;
  sources: string[];
  fixture: boolean;
  session: [number, number];
  track_record: ApexTrackBrief | null;
}

export interface ApexFinding {
  level: "INFO" | "WARN" | "BLOCK";
  code: string;
  message: string;
  count: number | null;
}

export interface ApexSwing {
  kind: "high" | "low";
  price: number;
  t: number;
  confirmed: number;
  label: "" | "HH" | "LH" | "HL" | "LL";
}

export interface ApexStructure {
  minutes: number;
  trend: "up" | "down" | "range";
  last_high: ApexSwing | null;
  last_low: ApexSwing | null;
  swings: ApexSwing[];
  events: { type: "BOS" | "CHoCH"; direction: "up" | "down"; level: number; t: number; trend_before: string }[];
  atr: number | null;
  bars: number;
}

export interface ApexZone {
  id: string;
  low: number;
  high: number;
  mid: number;
  side: "support" | "resistance";
  inside: boolean;
  distance_atr: number;
  kinds: string[];
  groups: string[];
  reasons: string[];
  labels: string[];
  major_round: boolean;
  tests: number;
  score: number;
  rate: ApexRate | null;
}

export interface ApexScenario {
  id: string;
  zone: string;
  kind: "hold" | "break";
  side: "support" | "resistance";
  statement: string;
  rate: ApexRate;
  reference: ApexRate | null;
}

export interface ApexOutcome {
  state: "pending" | "open" | "win" | "loss" | "timeout" | "expired" | "missed";
  final: boolean;
  filled_at: number | null;
  exit_at: number | null;
  exit_price?: number | null;
  r: number | null;
  mfe_r?: number;
  mae_r?: number;
  checked?: number | null;
}

export interface ApexCandidate {
  id: string;
  template: string;
  direction: "long" | "short";
  zone: string;
  market: ApexMarketName;
  created_at: number;
  /** limit = zone_reaction (at the zone), stop = zone_rejection_5m (after its trigger). */
  entry: { type: "limit" | "stop"; price: number };
  stop: number;
  targets: { price: number; rr: number; zone: string }[];
  strategy_version?: string;
  trigger?: ApexTrigger;
  execution?: ApexExecution;
  risk_points: number;
  risk_usd: number;
  cost_usd: number;
  unit: string;
  valid_until: number;
  max_hold_minutes: number;
  trend_60: string;
  with_trend: boolean | null;
  rules: string[];
  zone_rate?: ApexRate | null;
  track_record?: ApexTrackBrief | null;
  synthetic?: boolean;
  replay?: boolean;
  source_id?: string;
  outcome?: ApexOutcome | null;
  /** Zone setups (sweep_reclaim, rejection_rotation, breakout_retest) and the gate. */
  setup?: ApexZoneSetupName;
  zone_ids?: string[];
  zone_rank?: number | null;
  zone_state?: string;
  zone_quality?: number;
  risk_ticks?: number;
  tick_value_usd?: number;
  stressed_costs_points?: number;
  break_even_probability?: number;
  expiry_utc?: number;
  trade_success_probability?: ApexTradeProbability;
  eligibility?: ApexEligibility;
  user_executable?: false;
}

/** P(TP before SL after the fill within the horizon) — only ever shown when validated. */
export interface ApexTradeProbability {
  event_definition: "TP_before_SL_after_fill_within_horizon";
  value: number | null;
  validated: boolean;
  calibrated: boolean;
  effective_sample_size: number;
  uncertainty_lower: number | null;
  uncertainty_upper: number | null;
  label: string | null;
  model_id: string | null;
}

export type ApexGateCheckName =
  | "APP_READY" | "DATA_FIT" | "SOURCE_CAPABILITY" | "ZONE_CONFIRMED" | "TRIGGER_CONFIRMED"
  | "EXECUTION_FEASIBLE" | "PROBABILITY" | "UNCERTAINTY_SAMPLE" | "NET_EV_STRESSED"
  | "MACRO_LIQUIDITY" | "UNIQUE_BUDGET";

export interface ApexGateCheck {
  check: ApexGateCheckName;
  passed: boolean;
  reason: string;
}

/** The eligibility gate's verdict on one plan (backend apex/eligibility.py). */
export interface ApexEligibility {
  checks: ApexGateCheck[];
  failed: ApexGateCheckName[];
  eligible: boolean;
  setup_state: ApexSetupState;
  blocked: boolean;
  net_expected_r: number | null;
  conviction: string | null;
  probability_label: string | null;
}

export type ApexSetupState =
  | "WATCH" | "TRIGGER_PENDING" | "TRIGGER_CONFIRMED" | "ENTRY_ARMED" | "RESEARCH_ONLY"
  | "NO_TRADE" | "BLOCKED";
export type ApexSetupStage =
  | "SCANNING" | "NEAR_ZONE" | "WATCHING_SETUP" | "TRIGGER_PENDING" | "TRIGGER_CONFIRMED"
  | "ENTRY_ARMED";
export type ApexZoneSetupName = "sweep_reclaim" | "rejection_rotation" | "breakout_retest";

/** A qualified paper alert (at most four per market and trading day; never an order). */
export interface ApexAlert {
  id: string;
  market: ApexMarketName;
  trading_day: string;
  signal_id: string;
  candidate_id: string;
  setup: string;
  direction: "long" | "short";
  zone_id: string;
  as_of: number;
  entry: { type: "limit" | "stop"; price: number };
  stop: number;
  targets: { price: number; rr: number; zone: string }[];
  trade_success_probability: ApexTradeProbability | null;
  net_expected_r: number | null;
  conviction: string | null;
  user_executable: false;
}

export interface ApexRejected {
  template: string;
  direction?: "long" | "short";
  zone?: string;
  reason: string;
}

/** What one data update did (written next to the cache, ``_checks.json``). */
export interface ApexUpdateReport {
  kind: "sync" | "today";
  at: number;
  side: "BID" | "ASK";
  range?: [string, string];
  recheck?: boolean;
  code?: string | null;
  asked?: number;
  with_data?: number;
  empty?: number;
  missing?: number;
  pending?: number;
  failed?: number;
  /** HTTP status (or error type) → count. */
  answers?: Record<string, number>;
  empty_run?: [string, string, number] | null;
  error: string | null;
  seconds?: number;
}

export interface ApexUpdate {
  requested: boolean;
  ok: boolean | null;
  error: string | null;
  report: ApexUpdateReport | null;
}

/** Measured ask − bid on the recent days (Dukascopy). */
export interface ApexSpread {
  measured: boolean;
  minutes: number;
  reason?: string;
  from?: number;
  to?: number;
  median?: number;
  p90?: number;
  session_median?: number;
  session_p90?: number;
  max?: number;
  negative?: number;
  cost_points: number;
  exceeds_costs?: boolean;
  days?: number;
  error?: string | null;
}

export interface ApexQualitySummary {
  bars: number;
  first?: number;
  last_close?: number;
  behind_minutes?: number;
  missing_share?: number | null;
  days?: number;
  largest_gaps?: { from: string; to: string; t: number; minutes: number }[];
  spikes?: { t: number; at: string; kind: "close" | "wick"; sigma: number }[];
  large_moves?: { t: number; at: string; move_pct: number; sigma: number }[];
}

/** JARVIS's written reading of an analysis — built only from its counted numbers. */
export interface ApexNarrative {
  headline: string;
  points: string[];
  caveats: string[];
  question: string;
}

export interface ApexAnalysis {
  id: string;
  engine: string;
  market: ApexMarketName;
  as_of: number;
  replay: boolean;
  blocked: boolean;
  data: {
    source: string;
    source_id?: string;
    label: string;
    synthetic: boolean;
    proxy: string | null;
    sha256: string;
    provenance: Record<string, unknown>;
    update?: ApexUpdate | null;
    spread?: ApexSpread | null;
  };
  quality: {
    status: "OK" | "WARN" | "BLOCK";
    stale: boolean;
    findings: ApexFinding[];
    summary: ApexQualitySummary;
  };
  narrative?: ApexNarrative;
  spec: { label: string; tick: number; point_value: number; unit: string; cost_points: number };
  price?: number | null;
  atr15?: number;
  structure: Record<string, ApexStructure>;
  zones: ApexZone[];
  scenarios: ApexScenario[];
  base_rates?: { definition: string; touches: number; skipped: Record<string, number> };
  candidates: ApexCandidate[];
  rejected: ApexRejected[];
  /** Each strategy's point-in-time decision (engine 1.2+). */
  strategies?: Partial<Record<ApexStrategyName, ApexDecision>>;
  /** One contract-checked signal per decision, stored immutably (engine 1.2+). */
  signals?: ApexSignal[];
  execution?: ApexExecution;
  validation_ids?: Partial<Record<ApexStrategyName, string>>;
  /** [time, open, high, low, close] of the last 15-minute bars. */
  chart: [number, number, number, number, number][];
}

export type ApexStrategyName = "zone_reaction" | "zone_rejection_5m" | ApexZoneSetupName;
export type ApexSignalStatus = "NO_TRADE" | "WAITING_FOR_TRIGGER" | "CANDIDATE" | "PAPER_VALIDATED" | "BLOCKED";
export type ApexValidationState = "UNVALIDATED" | "RESEARCH_ONLY" | "OOS_SUPPORTED" | "FORWARD_SUPPORTED" | "REJECTED";

/** How an order plan is filled on the data APEX has (backend apex/execution.py). */
export interface ApexExecution {
  assumption: "execution_assumption_limited" | "futures_trade_prices" | "synthetic_flat_costs" | "data_unavailable" | string;
  quotes: "bid" | "trades" | "synthetic";
  spread: number;
  spread_source: "measured" | "estimate" | "inside_costs";
  costs: number;
  total_cost: number;
  tradable: boolean;
  reason: string;
}

export interface ApexTrigger {
  bar_utc: number;
  closed_utc: number;
  open: number;
  high: number;
  low: number;
  close: number;
  rule: string;
}

export interface ApexSetup {
  zone: string | null;
  direction: "long" | "short";
  /** zone_rejection_5m */
  distance_atr?: number;
  /** zone setups: stage and state per direction, the zone looked at, why */
  stage?: ApexSetupStage;
  setup_state?: ApexSetupState;
  zone_rank?: number | null;
  zone_side?: "SUPPORT" | "RESISTANCE" | null;
  zone_bounds?: { low: number; high: number } | null;
  trigger?: ApexTrigger | null;
  reasons?: string[];
}

export interface ApexDecision {
  strategy: ApexStrategyName;
  version: string;
  status: "NO_TRADE" | "WAITING_FOR_TRIGGER" | "CANDIDATE" | "BLOCKED";
  direction: "long" | "short" | "none";
  setups: ApexSetup[];
  trigger: ApexTrigger | null;
  candidates: ApexCandidate[];
  rejected: ApexRejected[];
  reasons: string[];
  /** Zone setups only: the furthest stage of either direction, and what a person sees. */
  stage?: ApexSetupStage;
  setup_state?: ApexSetupState;
}

/** The binding signal contract (backend apex/signal.py — invariants enforced there). */
export interface ApexSignal {
  signal_id: string;
  symbol: ApexMarketName;
  market_data_source: string;
  as_of_utc: string;
  last_closed_bar_utc: string | null;
  timeframe: string;
  strategy_id: ApexStrategyName;
  strategy_version: string;
  status: ApexSignalStatus;
  direction: "LONG" | "SHORT" | "NONE";
  market_structure: "BULLISH" | "BEARISH" | "RANGE" | "UNKNOWN";
  zones: { id: string; side: "support" | "resistance"; low: number; high: number; confirmed_at_utc: string | null; source: string }[];
  entry_type: "LIMIT" | "STOP" | "MARKET" | "NONE";
  entry: number | null;
  stop_loss: number | null;
  take_profit_1: number | null;
  take_profit_2: number | null;
  risk_reward_after_costs: number | null;
  probability_estimate: number | null;
  probability_interval: [number, number] | null;
  calibration_sample_size: number | null;
  validation_state: ApexValidationState;
  invalidation_rule: string;
  reasons: string[];
  data_quality: "PASS" | "WARN" | "FAIL";
  execution_assumption: string;
  setup: { setups: ApexSetup[] } | null;
  trigger: ApexTrigger | null;
  evidence_ids: string[];
  candidate_id: string | null;
  created_at_utc: string;
  setup_state: ApexSetupState | null;
  stage: string | null;
  eligible: boolean;
  eligibility: ApexGateCheck[];
  net_expected_r: number | null;
  trade_success_probability: ApexTradeProbability | null;
  conviction: string | null;
  mode: string | null;
  zone_ids: string[];
  instrument_id: string | null;
  risk_usd: number | null;
  expiry_utc: string | null;
  user_executable: false;
  synthetic?: boolean;
  replay?: boolean;
  analysis_id?: string;
  outcome?: ApexOutcome | null;
}

export interface ApexValidationStats {
  candidates: number;
  filled: number;
  trade_days: number;
  states: Record<string, number>;
  mean_r: number | null;
  ci: [number, number] | null;
  win_rate: number | null;
  profit_factor: number | null;
  total_r: number;
  max_drawdown_r: number;
}

export interface ApexCriterion {
  code: "EVIDENCE" | "DEV_CI" | "BASELINE" | "COST_STRESS" | "NEIGHBOURS" | "FOLDS" | string;
  required: string;
  measured: string;
  passed: boolean;
}

export interface ApexValidationResult {
  protocol_version: string;
  strategy: ApexStrategyName;
  version: string;
  engine: string;
  market: ApexMarketName;
  data: { source_id: string; label: string; synthetic: boolean; sha256: string };
  execution: ApexExecution;
  parameters: Record<string, number>;
  split: { dev: [string, string]; holdout: [string, string]; folds: [string, string][] };
  development: {
    decisions: number;
    embargo_minutes: number;
    stats: ApexValidationStats;
    folds: (ApexValidationStats & { fold: number; start: number; end: number; decisions: number })[];
    cost_stress: ApexValidationStats & { factor: number };
    neighbours: (ApexValidationStats & { change: Record<string, number> })[];
    baseline: { runs: number; p_value: number | null; mean_r: number | null; p95_r?: number };
    trials: { before: number; this_run: number; total: number; alpha: number; alpha_corrected: number };
    criteria: ApexCriterion[];
    enough: boolean;
    passed: boolean;
  };
  holdout: { decisions: number; stats: ApexValidationStats; enough: boolean; passed: boolean; opened_by: string } | null;
  verdict: { validation_state: ApexValidationState; code: string; text: string };
}

export interface ApexValidation {
  id: string;
  market: ApexMarketName;
  strategy: ApexStrategyName;
  version: string;
  protocol_version: string;
  source_id: string;
  state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  progress: number;
  synthetic: boolean;
  data_sha256: string;
  result: ApexValidationResult | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface ApexTrial {
  id: number;
  family: string;
  strategy: ApexStrategyName;
  version: string;
  kind: "registered" | "neighbour";
  params: Record<string, number>;
  validation_id: string;
  summary: { filled: number; mean_r: number | null };
  synthetic: boolean;
  created_at: string;
}

export interface ApexGapReason {
  reason: "day_without_bars" | "long_gap" | "short_gap";
  text: string;
  minutes: number;
  days?: number;
  count?: number;
  examples?: (string | { from: string; minutes: number })[];
}

/** APEX → Data fitness (backend apex/fitness.py). */
export interface ApexFitness {
  market: ApexMarketName;
  as_of: number;
  source: string;
  source_id: string;
  label: string;
  synthetic: boolean;
  symbol: string;
  contract: string;
  quote_side: "bid" | "trades" | "synthetic";
  execution: ApexExecution;
  execution_text: string;
  granularity: string;
  timeframes: string;
  status: "OK" | "WARN" | "BLOCK" | "DATA_UNAVAILABLE";
  bars: number;
  first_bar?: number;
  last_closed_bar?: number;
  last_closed_text?: string;
  span_days?: number;
  coverage: { expected_bars: number; present_bars: number; share: number | null } | null;
  gaps: { calendar: string; missing_minutes: number; by_reason: ApexGapReason[]; outside_calendar_bars: number } | null;
  integrity?: { duplicates_or_disorder: number; misaligned: number };
  spikes: { t: number; at: string; kind: string; sigma: number }[];
  large_moves?: { t: number; at: string; move_pct: number; sigma: number }[];
  rolls?: string[];
  hints: string[];
  cache_days?: Record<string, number>;
}

export interface ApexSummary {
  candidates: number;
  filled: number;
  states: Record<"win" | "loss" | "timeout" | "expired" | "missed", number>;
  fill_rate: number | null;
  win_rate: number | null;
  win_ci: [number, number] | null;
  expectancy_r: { mean: number | null; se?: number | null; t?: number | null; ci?: [number, number] | null; n: number; clusters?: number };
  profit_factor: number | null;
  total_r: number;
  max_drawdown_r: number;
  status: "ok" | "insufficient";
}

export interface ApexJournal {
  candidates: ApexCandidate[];
  stats: { real: ApexSummary; synthetic: ApexSummary };
}

export interface ApexTrackRecord {
  engine: string;
  template: string;
  strategy_version?: string;
  execution?: ApexExecution;
  market: ApexMarketName;
  start: string;
  end: string;
  oos_from: string | null;
  decisions: number;
  skipped: Record<string, number>;
  data: { source: string; label: string; synthetic: boolean; proxy: string | null; sha256: string };
  all: ApexSummary;
  in_sample: ApexSummary;
  out_of_sample: ApexSummary;
  by_direction: Record<"long" | "short", ApexSummary>;
  by_trend: Record<"with_trend" | "counter_trend", ApexSummary>;
  verdict: { code: "INSUFFICIENT" | "NO_EDGE" | "INCONCLUSIVE" | "POSITIVE_OOS"; text: string };
  settings: Record<string, number>;
  calibration?: ApexCalibration;
}

/** Stated hold rates vs. what later touches did (APEX-3). */
export interface ApexCalibration {
  touches: number;
  skipped: number;
  brier: number | null;
  brier_reference: number | null;
  /** 1 - Brier / Brier of a side-only reference: > 0 means the zone kinds add information. */
  skill: number | null;
  ece: number | null;
  bins: { range: [number, number]; n: number; stated: number; observed: number; ci: [number, number] | null; inside: boolean }[];
  by_side: Record<string, { touches: number; stated: number | null; observed: number | null }>;
  verdict: { code: "CALIBRATED" | "MISCALIBRATED" | "INSUFFICIENT"; text: string };
}

export interface ApexEvaluation {
  id: string;
  market: ApexMarketName;
  template: string;
  start: string;
  end: string;
  state: "RUNNING" | "COMPLETED" | "FAILED";
  progress: number;
  synthetic: boolean;
  result: ApexTrackRecord | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

/** An analysis' bars at another timeframe — only what was known at its moment. */
export interface ApexChartData {
  analysis_id: string;
  market: ApexMarketName;
  minutes: number;
  as_of: number;
  label: string;
  synthetic: boolean;
  prices: string;
  /** false when the cache changed (healed) since the analysis. */
  same_data: boolean;
  /** The last bar was still forming at the moment. */
  forming: boolean;
  /** [time, open, high, low, close, volume] */
  bars: [number, number, number, number, number, number][];
}

export type ApexTimeframe = 1 | 5 | 15 | 60 | 240;

export interface ApexCause {
  code: string;
  level: "OK" | "INFO" | "WARN" | "BLOCK";
  text: string;
  actions: ("repair" | "vault")[];
}

export interface ApexDayRange {
  from: string;
  to: string;
  days: number;
  states: Record<string, number>;
}

export interface ApexSourceStatus {
  id: string;
  kind: "dukascopy" | "vault" | "fixture";
  label?: string;
  prices?: string;
  proxy?: string | null;
  state: "ok" | "warn" | "error" | "none";
  message?: string;
  action?: string | null;
  readiness?: string;
  cache?: string;
  coverage?: { first: string | null; last: string | null; bars?: number } | null;
  newest_bar?: number | null;
  behind_minutes?: number | null;
  days?: Record<string, number>;
  suspicious?: ApexDayRange[];
  last_update?: ApexUpdateReport | null;
  last_sync?: ApexUpdateReport | null;
  updates?: ApexUpdateReport[];
  spread?: ApexSpread | null;
  bars?: number;
}

export interface ApexRepair {
  state: "RUNNING" | "COMPLETED" | "FAILED";
  started?: number;
  finished?: number | null;
  steps: {
    source: string;
    side?: string;
    range?: [string, string];
    ok: boolean | null;
    error?: string | null;
    skipped?: string;
    report?: ApexUpdateReport | null;
  }[];
  message: string | null;
  fixture?: boolean;
}

export interface ApexDataStatus {
  market: ApexMarketName;
  checked_at: number;
  fixture: boolean;
  span: [string, string];
  market_open: boolean;
  sources: ApexSourceStatus[];
  active?: string | null;
  causes: ApexCause[];
  repair: ApexRepair | null;
}

/** SENTINEL's independent re-check of a stored analysis. */
export interface ApexAuditCheck {
  code: string;
  passed: boolean;
  detail: string;
}

export interface ApexAudit {
  id: string;
  analysis_id: string;
  market: ApexMarketName;
  state: "COMPLETED" | "FAILED";
  passed: boolean | null;
  result: { checks: ApexAuditCheck[]; recomputed_at: number; data_source: string } | null;
  created_at: string;
  finished_at: string | null;
}

export interface ApexForwardEvidence {
  strategy: ApexStrategyName;
  version: string;
  synthetic: boolean;
  plans: number;
  filled: number;
  trade_days: number;
  mean_r: number | null;
  ci: [number, number] | null;
  needed: number;
  passed: boolean;
}

/** The paper forward log (APEX → Validation): opt-in live analyses on a schedule. */
export interface ApexForward {
  enabled: boolean;
  interval_minutes: number;
  markets: Record<string, { state: "ok" | "error" | "outside_session"; at: number; analysis_id?: string; error?: string } | null>;
  evidence: Record<string, Partial<Record<ApexStrategyName, ApexForwardEvidence>>>;
}
