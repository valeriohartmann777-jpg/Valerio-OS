/** QuantLab Data Hub (Databento) and futures research — mirrors the /quantlab/* APIs. */

// Data Hub ----------------------------------------------------------------------------------

export type QhConnectionStatus = "NOT_CONNECTED" | "CONNECTED" | "REJECTED";

export interface QhKeystore {
  available: boolean;
  backend: string;
  reason: string | null;
  test_only: boolean;
}

export interface QhStatus {
  provider: "databento";
  status: QhConnectionStatus;
  key_hint: string | null;
  connected_at: string | null;
  verified_at: string | null;
  datasets: string[];
  error_code: string | null;
  error: string | null;
  keystore: QhKeystore;
  fixture: boolean;
  fixture_label: string | null;
  sdk_version: string | null;
  caps: QhCaps;
  approved_this_month_usd: number;
  license_note: string;
}

export interface QhCaps {
  per_request_usd: number;
  per_month_usd: number;
}

export interface QhProduct {
  root: string;
  name: string;
  engine: boolean;
}

export interface QhDatasetInfo {
  dataset: string;
  schemas: string[];
  ingestible: string[];
  available_start: string;
  available_end: string;
  recent_conditions: Record<string, number>;
  recent_flagged: { date: string; condition: string }[];
  fixture: boolean;
}

export interface QhCatalog {
  datasets: string[];
  products: QhProduct[];
  info: QhDatasetInfo | null;
}

export interface QhResolution {
  mappings: Record<string, { start: string; end: string; id: string }[]>;
  not_found: string[];
  partial: string[];
  warnings: string[];
}

export interface QhRequest {
  dataset: string;
  schema: string;
  stype_in: string;
  symbols: string[];
  start: string;
  end: string;
  include_definitions?: boolean;
}

export interface QhQuoteRange {
  start: string;
  end: string;
  cost_usd: number;
  billable_bytes: number;
  records: number;
}

export interface QhQuoteItem {
  dataset: string;
  schema: string;
  symbol: string;
  stype_in: string;
  ranges: QhQuoteRange[];
  cached_days: number;
  missing_days: number;
}

export type QhQuoteStatus = "OPEN" | "APPROVED" | "EXPIRED" | "REJECTED" | "SUPERSEDED";

export interface QhQuote {
  id: string;
  provider: string;
  created_at: string;
  expires_at: string;
  status: QhQuoteStatus;
  request: QhRequest;
  items: QhQuoteItem[];
  cost_usd: number;
  billable_bytes: number;
  records: number;
  cached_days: number;
  conditions: Record<string, string>;
  warnings: string[];
  fixture: boolean;
  job_id: string | null;
  note: string | null;
  caveat?: string;
  caps?: QhCaps;
  approved_this_month_usd?: number;
  within_caps?: boolean;
  signature_valid?: boolean;
}

export type QhJobStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELED" | "INTERRUPTED";

export interface QhChunk {
  idx: number;
  symbol: string;
  schema: string;
  start: string;
  end: string;
  status: string;
  bytes: number;
  records: number;
}

export interface QhJob {
  id: string;
  quote_id: string;
  status: QhJobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  approved_budget_usd: number;
  estimated_cost_usd: number;
  chunks_total: number;
  chunks_done: number;
  bytes: number;
  records: number;
  error_code: string | null;
  error: string | null;
  warnings: string[];
  fixture: boolean;
  chunks?: QhChunk[];
}

export interface QhCacheRow {
  cache_key: string;
  provider: string;
  dataset: string;
  schema: string;
  stype_in: string;
  symbol: string;
  first_day: string;
  last_day: string;
  days: number;
  records: number;
  flagged_days: number;
  fixture: boolean;
}

export interface QhFinding {
  level: "BLOCK" | "WARN" | "INFO";
  code: string;
  message: string;
  count: number;
  examples: string[];
}

export interface QhCapability {
  use: string;
  status: "FIT" | "LIMITED" | "NOT_SUPPORTED";
  why: string;
}

export interface QhQuality {
  status: "OK" | "WARN" | "BLOCK";
  findings: QhFinding[];
  summary: Record<string, number | string>;
  rolls?: { at: string; from_instrument_id: number; to_instrument_id: number; jump_points: number }[];
  capabilities?: QhCapability[];
}

export interface QhInstrument {
  instrument_id: number;
  raw_symbol?: string;
  bars: number;
  first_ts: number;
  last_ts: number;
}

export interface QhDataset {
  id: string;
  created_at: string;
  provider: string;
  dataset: string;
  schema: string;
  stype_in: string;
  symbol: string;
  start: string;
  end: string;
  records: number;
  snapshot_sha256: string;
  fixture: boolean;
  quality: QhQuality;
  manifest: {
    time_convention: string;
    prices: string;
    instruments: QhInstrument[];
    mapping: { start: string; end: string; instrument_ids: number[] }[];
    conditions: Record<string, string>;
    acquisition: { job_id: string; quote_id: string; estimated_cost_usd: number; approved_at: string }[];
    license: string;
    transform_version: string;
    calendar: { library: string; version: string };
    sdk_version: string | null;
    snapshot: { path: string; sha256: string };
    definitions: { days_cached: number; records: number };
  } & Record<string, unknown>;
}

export interface QhAudit {
  id: number;
  at: string;
  action: string;
  detail: Record<string, unknown>;
}

// Research ----------------------------------------------------------------------------------

export type QrVerdict =
  | "INVALID_DATA_OR_METHOD"
  | "INSUFFICIENT_EVIDENCE"
  | "REJECTED_HYPOTHESIS"
  | "PROMISING_RESEARCH_CANDIDATE"
  | "FORWARD_VALIDATION_REQUIRED"
  | "ROBUST_UNDER_TESTED_ASSUMPTIONS";

export type QrTestStatus = "PASSED" | "WARNING" | "FAILED" | "INCONCLUSIVE" | "NOT_APPLICABLE" | "NOT_RUN";
export type QrFitness = "FIT" | "FIT_WITH_LIMITATIONS" | "INSUFFICIENT" | "INVALID";
export type QrRunStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELED" | "INTERRUPTED";

/** FuturesSpec 1.0 — kept as JSON; the backend is the validator. */
export type QrSpec = Record<string, unknown> & {
  schema_version: "1.0";
  kind: "futures_intraday";
  name: string;
  hypothesis: string;
};

export interface QrAssumption {
  field: string;
  state: "confirmed" | "assumed" | "unknown";
  note: string;
}

export interface QrCheck {
  valid: boolean;
  errors: { field: string; problem: string }[];
  description?: string[];
  sha256?: string;
  state?: "DRAFT" | "READY";
  unresolved?: QrAssumption[];
  assumptions?: QrAssumption[];
  warnings?: string[];
}

export interface QrTemplate {
  kind: string;
  title: string;
  spec: QrSpec;
}

export interface QrVersion {
  id: string;
  number: number;
  parent_id: string | null;
  origin: "user" | "ai" | "template" | "variant";
  note: string | null;
  created_at: string;
  spec: QrSpec;
  spec_sha256: string;
  description: string[];
  state: "DRAFT" | "READY";
  assumptions: QrAssumption[];
}

export interface QrRunRow {
  id: string;
  strategy_id: string;
  version_id: string;
  version_number: number;
  dataset_id: string;
  kind: "backtest" | "validation";
  include_holdout: boolean;
  status: QrRunStatus;
  stage: string | null;
  progress: number;
  verdict: QrVerdict | null;
  fixture: boolean;
  name: string;
  symbol: string;
  net_pnl: number | null;
  oos_net: number | null;
  trades: number | null;
  error_code: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  results_sha256: string | null;
}

export interface QrStrategyRow {
  id: string;
  name: string;
  hypothesis: string;
  product: string;
  created_at: string;
  versions: number;
  latest_version_id: string | null;
  latest_run: QrRunRow | null;
}

export interface QrTrial {
  id: number;
  run_id: string;
  version_id: string;
  params: Record<string, number>;
  segment: string;
  trades: number;
  net: number;
  sharpe_daily: number | null;
  created_at: string;
}

export interface QrNote {
  id: number;
  run_id: string | null;
  kind: "note" | "decision";
  text: string;
  created_at: string;
}

export interface QrStrategy {
  id: string;
  name: string;
  hypothesis: string;
  product: string;
  created_at: string;
  versions: QrVersion[];
  runs: QrRunRow[];
  trials: { variants: number; recent: QrTrial[] };
  holdouts: { id: number; first_day: string; last_day: string; run_id: string; at: string }[];
  notes: QrNote[];
}

export type QrMetrics = Record<string, number | null> & {
  trades: number;
  net_pnl: number | null;
  notes?: Record<string, string>;
  exit_reasons?: Record<string, number>;
  session_status?: Record<string, number>;
};

export interface QrTest {
  id: string;
  name: string;
  status: QrTestStatus;
  assumptions: string[];
  requirements: string;
  metric: Record<string, unknown>;
  interpretation: string;
  evidence: string | null;
}

export interface QrVerdictInfo {
  verdict: QrVerdict;
  reasons: string[];
  next_steps: string[];
  never: string;
  would_be?: QrVerdict;
}

export interface QrAuditCheck {
  id: string;
  title: string;
  result: "PASS" | "FAIL";
  checked: number;
  failures: number;
  examples: string[];
}

export interface QrContract {
  instrument_id: number;
  raw_symbol: string;
  root: string;
  tick_size: string;
  tick_value: string;
  multiplier: string;
  currency: string;
  provenance: "definition" | "assumed";
}

export interface QrSummary {
  name: string;
  product: string;
  fixture: boolean;
  description: string[];
  metrics: QrMetrics;
  segments: { insample: QrMetrics; oos: QrMetrics; holdout?: QrMetrics };
  optimistic: { net_pnl: number; oos_net: number | null };
  split: {
    insample: { sessions: number; first: string | null; last: string | null };
    oos: { sessions: number; first: string | null; last: string | null };
    holdout: { sessions: number; first: string | null; last: string | null };
    embargo_sessions: string[];
  };
  fitness: { status: QrFitness; reasons: { level: string; code: string; message: string }[] };
  audit: { conservative: QrAuditCheck[]; optimistic: QrAuditCheck[] };
  contracts: QrContract[];
  contract_findings: QhFinding[];
  session_status: Record<string, number>;
  tests: QrTest[];
  verdict: QrVerdictInfo | null;
  risk: {
    max_notional: number | null;
    avg_notional: number | null;
    max_leverage: number | null;
    margin_required: number | null;
    margin_ok: boolean | null;
    fee_per_side: number;
    slippage_ticks: number;
  };
  dataset: { id: string; symbol: string; start: string; end: string; quality: string; license?: string };
  limitations: string[];
  assumptions: QrAssumption[];
}

export interface QrRun extends Omit<QrRunRow, "name" | "symbol" | "net_pnl" | "oos_net" | "trades" | "version_number"> {
  manifest: Record<string, unknown> & {
    version_number: number;
    spec: QrSpec;
    dataset: { id: string; symbol: string; start: string; end: string; fixture: boolean; snapshot_sha256: string };
    engine: { name: string; version: string; code_revision: string };
  };
  manifest_sha256: string;
  summary: QrSummary | null;
}

export interface QrTrade {
  number: number;
  session: string;
  segment: "IS" | "OOS" | "HOLDOUT" | "EMBARGO";
  direction: "LONG" | "SHORT";
  contracts: number;
  instrument_id: number;
  raw_symbol: string;
  entry_time: string;
  entry_price: number;
  exit_time: string;
  exit_price: number;
  exit_reason: string;
  stop_price: number | null;
  target_price: number | null;
  gross: number;
  fees: number;
  net: number;
  slippage_cost: number;
  mae_ticks: number;
  mfe_ticks: number;
  mae_usd: number;
  mfe_usd: number;
  r_multiple: number | null;
  bars_held: number;
  signal_known_at: string;
  ambiguous: boolean;
}

export interface QrTrades {
  total: number;
  offset: number;
  rows: QrTrade[];
}

export interface QrBar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface QrTradeDetail {
  trade: QrTrade;
  bars: QrBar[];
  fills: {
    order_id: number;
    bar_time: string;
    known_at: string;
    side: "BUY" | "SELL";
    price: number;
    reference_price: number;
    slippage_ticks: number;
    fee: number;
    reason: string;
    ambiguous: boolean;
  }[];
  session: { session: string; status: string; raw_symbol: string; early_close: boolean } | null;
  range: { high: number; low: number; known_at: string } | null;
  costs: { commission: number; exchange_fees: number; slippage: number; total: number };
  story: string[];
  timezone: string;
}

export interface QrChart {
  equity: { t: string; equity: number; drawdown: number }[];
  candles: { session: string; o: number; h: number; l: number; c: number }[];
  markers: { session: string; number: number; direction: "LONG" | "SHORT"; entry: number; exit: number; net: number; segment: string }[];
  split: QrSummary["split"] | null;
  capital: number;
}

export interface QrDraft {
  spec: QrSpec;
  summary: string;
  questions: { field: string; question: string }[];
  unsupported: string[];
  description: string[];
  assumptions: QrAssumption[];
  state: "DRAFT" | "READY";
  model: string;
  note: string;
}

export interface QrCompare {
  runs: {
    id: string;
    name: string;
    kind: string;
    version: number;
    dataset: string;
    verdict: QrVerdict | null;
    net: number | null;
    oos_net: number | null;
    oos_trades: number | null;
    oos_sharpe: number | null;
    max_drawdown: number | null;
    ambiguous_trades: number | null;
    fixture: boolean;
  }[];
  spec_differences: { field: string; values: unknown[] }[];
}

export interface QrOverview {
  strategies: number;
  runs: QrRunRow[];
  active: string[];
  hub: {
    status: QhConnectionStatus;
    fixture: boolean;
    datasets: number;
    cache: QhCacheRow[];
    jobs_running: number;
    approved_this_month_usd: number;
  };
}
