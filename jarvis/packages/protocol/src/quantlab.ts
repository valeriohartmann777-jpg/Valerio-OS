// QuantLab: research workbench contract (mirrors backend/jarvis/quantlab).
// Research only — there is no order, broker or live-trading endpoint.

export type QlVerdict = "INVALID" | "FAILED" | "INCONCLUSIVE" | "PROMISING_RESEARCH_CANDIDATE";
export type QlCheckResult = "PASS" | "WARN" | "FAIL" | "NOT_RUN" | "N_A";
export type QlDataStatus = "ACCEPTED" | "WARNING" | "REJECTED" | "UNSUPPORTED";
export type QlRunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type QlBarInterval = "1d" | "1h" | "5m" | "1m";

export interface QlStrategySpec {
  schema_version: "0.1";
  name: string;
  hypothesis: string;
  instrument: {
    asset_class: string;
    symbol: string;
    exchange: string;
    currency: string;
    timezone: string;
    corporate_action_policy?: "data_adjusted" | "not_applicable_test_fixture";
  };
  timeframe: {
    bar_interval: QlBarInterval;
    timestamps_are_bar: "open";
    signal_timestamp: "bar_close";
    session_calendar?: string;
  };
  signal: {
    type: "sma_crossover";
    fast_window: number;
    slow_window: number;
    price_column: "close";
    cross: "strict_cross";
  };
  execution: {
    entry_order: "market";
    exit_order: "market";
    fill_timing: "next_bar_open";
    slippage_bps: number;
    end_of_data: "mark_open_position_no_forced_sale";
  };
  position: { direction: "long_only"; size_units: number; initial_cash: number; leverage: 1 };
  costs: { fee_fixed_per_order: number; fee_variable_bps: number; currency: string };
  analysis: { chronological_oos_fraction: number; allow_parameter_search_on_oos: false };
}

export interface QlSpecCheck {
  valid: boolean;
  code: string | null;
  message: string | null;
  errors: { field: string; problem: string }[];
  warnings: string[];
  summary: string | null;
  spec_sha256: string | null;
}

export interface QlStrategyVersion {
  id: string;
  number: number;
  spec: QlStrategySpec;
  spec_sha256: string;
  created_at: string;
  summary: string;
  warnings: string[];
}

export interface QlStrategy {
  id: string;
  name: string;
  hypothesis: string;
  created_at: string;
  versions: QlStrategyVersion[];
}

export interface QlFinding {
  code: string;
  severity: "block" | "warn" | "info";
  message: string;
  count: number;
  examples: string[];
}

export interface QlPassport {
  schema_version: string;
  dataset_id: string;
  source_type: string;
  dataset_kind: string;
  synthetic: boolean;
  label: string | null;
  provider: string;
  license: string;
  original_filename: string;
  original_sha256: string;
  normalized_sha256: string | null;
  snapshot_file_sha256?: string;
  instrument: { asset_class: string; symbol: string; exchange: string; currency: string };
  venue: string;
  currency: string;
  asset_class: string;
  timezone_original: string;
  normalized_timezone: string;
  timestamp_semantics: string;
  frequency: QlBarInterval | null;
  frequency_source: string | null;
  coverage_start_utc: string | null;
  coverage_end_utc: string | null;
  row_count: number;
  rows_in_file: number;
  missing_periods: { gaps: number; estimated_missing_bars: number };
  duplicates: number;
  sort_fixes: number;
  invalid_ohlc: number;
  outliers: number;
  zero_volume: number;
  corporate_actions_or_roll_policy: string;
  column_mapping: Record<string, string>;
  normalizations: string[];
  quality_status: QlDataStatus;
  quality_findings: QlFinding[];
  limitations: string[];
}

export interface QlDataset {
  id: string;
  created_at: string;
  status: QlDataStatus;
  synthetic: boolean;
  label: string | null;
  usable: boolean;
  symbol: string;
  frequency: QlBarInterval | null;
  filename: string;
  original_sha256: string;
  normalized_sha256: string | null;
  snapshot_sha256: string | null;
  rows: number;
  passport: QlPassport;
  preview: {
    header: string[];
    raw_rows: string[][];
    normalized_rows: { ts: string; open: number; high: number; low: number; close: number; volume: number | null }[];
  };
}

export interface QlSegmentMetrics {
  bars: number;
  start_utc: string;
  end_utc: string;
  start_equity: number;
  end_equity: number;
  net_pnl: number;
  return_pct: number | null;
  return_denominator: string;
  fees: number;
  slippage_cost: number;
  gross_pnl: number;
  max_drawdown_abs: number;
  max_drawdown_pct: number;
  max_drawdown_at_utc: string | null;
  trades_entered: number;
  trades_closed: number;
  trades_open: number;
  trades_carried_in: number;
  win_rate: number | null;
  avg_net_per_closed_trade: number | null;
  profit_factor: number | null;
  largest_win: number | null;
  largest_loss: number | null;
  exposure: number | null;
  benchmark_price_return: number | null;
  benchmark_note: string;
}

export interface QlFullMetrics extends QlSegmentMetrics {
  initial_cash: number;
  final_cash: number;
  final_position: number;
  final_equity: number;
  realized_net_pnl: number;
  realized_gross_pnl: number;
  unrealized_gross_pnl: number;
  open_position_entry_fees: number;
  mark_price: number | null;
  orders_filled: number;
  orders_rejected: number;
  orders_not_executable: number;
  signals: number;
  sharpe: { status: string; reason: string };
}

export interface QlSplit {
  cutoff_index: number;
  train_bars: number;
  oos_bars: number;
  train_start_utc: string;
  train_end_utc: string;
  oos_start_utc: string;
  oos_end_utc: string;
}

export interface QlCheck {
  id: string;
  gate: "A" | "B" | "C" | "D" | "E";
  title: string;
  method_version: string;
  result: QlCheckResult;
  assumptions: string[];
  metric: Record<string, unknown>;
  observations: string;
  evidence_artifact: string | null;
}

export interface QlAssessment {
  headline: string;
  observed: string[];
  limitations: string[];
  cannot_conclude: string[];
  next_test: string;
}

export interface QlSummary {
  name: string;
  symbol: string;
  currency: string;
  synthetic: boolean;
  verdict: QlVerdict;
  meaning: string;
  reason: string;
  headline: string;
  metrics: { full: QlFullMetrics; train: QlSegmentMetrics; oos: QlSegmentMetrics; split: QlSplit };
  missing_gates: string[];
  assessment: QlAssessment;
  checks: QlCheck[];
  audit: { id: string; passed: boolean; detail: string; checked: number }[];
}

export interface QlArtifact {
  kind: string;
  relative_path: string;
  sha256: string;
  bytes: number;
}

export interface QlExperiment {
  id: string;
  status: QlRunStatus;
  stage: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  attempts: number;
  error_code: string | null;
  error: string | null;
  verdict: QlVerdict | null;
  results_sha256: string | null;
  manifest: Record<string, unknown> & {
    strategy_id: string;
    strategy_version_id: string;
    strategy_version: number;
    strategy_spec_sha256: string;
    dataset_id: string;
    dataset_synthetic: boolean;
    engine_name: string;
    engine_version: string;
    code_revision: string;
  };
  manifest_sha256: string;
  strategy_name: string | null;
  strategy_version: number | null;
  dataset: QlDataset | null;
  summary: QlSummary | null;
  artifacts: QlArtifact[];
}

export interface QlExperimentRow {
  id: string;
  status: QlRunStatus;
  stage: string | null;
  created_at: string;
  finished_at: string | null;
  verdict: QlVerdict | null;
  error_code: string | null;
  error: string | null;
  strategy_version_id: string;
  strategy_version: number;
  dataset_id: string;
  synthetic: boolean;
  name: string | null;
  symbol: string | null;
  headline: string | null;
  net_pnl: number | null;
  oos_net_pnl: number | null;
  max_drawdown_pct: number | null;
  currency: string | null;
}

export interface QlOverview {
  scope: string;
  live_trading: false;
  engine: { name: string; version: string };
  counts: { strategies: number; datasets: number; experiments: number; running: number };
  latest: QlExperiment | null;
  experiments: QlExperimentRow[];
  fixtures_available: boolean;
  template: QlStrategySpec;
}

export interface QlOrder {
  id: string;
  side: "BUY" | "SELL";
  quantity: number;
  status: "FILLED" | "REJECTED" | "NOT_EXECUTABLE";
  reason: string | null;
  signal_index: number;
  signal_bar_start_utc: string;
  signal_available_at_utc: string;
  fill_index: number | null;
  fill_time_utc: string | null;
  reference_open: number | null;
  fill_price: number | null;
  notional: number | null;
  fee: number | null;
  slippage_cost: number | null;
  cash_before: number | null;
  cash_after: number | null;
  position_after: number | null;
}

export interface QlTrade {
  id: string;
  status: "CLOSED" | "OPEN";
  quantity: number;
  entry_order: string;
  entry_index: number;
  entry_time_utc: string;
  entry_price: number;
  entry_fee: number;
  exit_order: string | null;
  exit_index: number | null;
  exit_time_utc: string | null;
  exit_price: number | null;
  exit_price_is_mark: boolean;
  exit_fee: number;
  gross_pnl: number;
  fees: number;
  net_pnl: number;
  return_pct: number;
  bars_held: number;
}

export interface QlSignal {
  index: number;
  bar_start_utc: string;
  available_at_utc: string;
  side: "BUY" | "SELL";
  disposition: string;
  sma_fast: number | null;
  sma_slow: number | null;
}

export interface QlLedger {
  trades: QlTrade[];
  orders: QlOrder[];
  signals: QlSignal[];
}

export interface QlEquityPoint {
  ts: string;
  equity: number;
  drawdown: number | null;
  segment: "train" | "oos";
}

export interface QlEquity {
  total_points: number;
  points: QlEquityPoint[];
  oos_start: string | null;
}

export interface QlReproduction {
  experiment_id: string;
  recorded_results_sha256: string;
  rerun_results_sha256: string;
  identical: boolean;
  same_code_revision: boolean;
}

export interface QlImportRequest {
  filename: string;
  content_base64: string;
  symbol: string;
  exchange: string;
  currency: string;
  asset_class?: string;
  timezone?: string | null;
  frequency?: QlBarInterval | null;
  provider?: string;
  license?: string;
  adjustment?: "adjusted" | "unadjusted" | "unknown";
  columns?: Record<string, string>;
}
