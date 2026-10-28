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
  entry: { type: "limit"; price: number };
  stop: number;
  targets: { price: number; rr: number; zone: string }[];
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
}

export interface ApexRejected {
  template: string;
  direction?: "long" | "short";
  zone?: string;
  reason: string;
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
  };
  quality: {
    status: "OK" | "WARN" | "BLOCK";
    stale: boolean;
    findings: ApexFinding[];
    summary: Record<string, number>;
  };
  spec: { label: string; tick: number; point_value: number; unit: string; cost_points: number };
  price?: number | null;
  atr15?: number;
  structure: Record<string, ApexStructure>;
  zones: ApexZone[];
  scenarios: ApexScenario[];
  base_rates?: { definition: string; touches: number; skipped: Record<string, number> };
  candidates: ApexCandidate[];
  rejected: ApexRejected[];
  /** [time, open, high, low, close] of the last 15-minute bars. */
  chart: [number, number, number, number, number][];
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
