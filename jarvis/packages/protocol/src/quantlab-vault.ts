/** QuantLab Data Vault + Backtest Lab (local data first). */

export type BlReadinessStatus =
  | "READY"
  | "PARTIAL"
  | "WRONG_GRANULARITY"
  | "MISSING_MAPPINGS"
  | "INVALID"
  | "NOT_AVAILABLE"
  | "INCOMPATIBLE";

export interface BlCompat {
  minutes: number;
  label: string;
  compatible: boolean;
  issues: { code: string; message: string }[];
  notes: string[];
}

export interface BlReadiness {
  status: BlReadinessStatus;
  label: string;
  product: string;
  minutes: number;
  timeframe: string;
  message: string;
  runnable: boolean;
  action?: string;
  series?: {
    key: string;
    source: string;
    provider: string;
    symbol: string;
    schema: string;
    start: string;
    end: string;
    source_minutes: number;
    fixture: boolean;
    grade: string;
    authoritative: boolean;
  };
  range?: { start: string; end: string };
  compatibility?: BlCompat | null;
  missing?: { symbol: string; schema: string; start: string | null; end: string | null }[];
  holes?: { start: string; end: string }[];
  problems?: Record<string, unknown>[];
}

export interface BlStrategy {
  id: string;
  name: string;
  product: string;
  rule: string;
  session: string;
  versions: number;
  version_id: string;
  version_number: number;
  spec_sha256: string;
  state: "READY" | "DRAFT";
  summary: string[];
  last_verdict: string | null;
  last_run: Record<string, unknown> | null;
  created_at: string;
}

export interface BlMarket {
  symbol: string;
  name: string;
  asset_class: string;
  venue: string;
  exchange: string;
  dataset: string;
  family: string;
  pair: string;
  tick_size: number;
  tick_value: number;
  multiplier: number;
  point_value: number;
  currency: string;
  contract_months: string;
  sessions: string;
  continuous: string;
  not: string;
  spec_source: string;
  local: { source: string; symbol: string; start: string; end: string; schema: string; grade: string; fixture: boolean; sessions_with_bars: number }[];
}

export interface BlCostProfile {
  id: string;
  label: string;
  note: string;
  products: Record<string, { commission: number; fees: number; slippage_ticks: number }>;
}

export interface BlOptions {
  strategies: BlStrategy[];
  markets: BlMarket[];
  timeframes: { minutes: number; label: string }[];
  cost_profiles: BlCostProfile[];
  offline: boolean;
  network: string;
}

export interface BlAdvanced {
  start?: string | null;
  end?: string | null;
  cost_profile?: "intraday_conservative" | "intraday_stress" | null;
  kind?: "backtest" | "validation" | null;
  contracts?: number | null;
  account_capital?: number | null;
  direction?: "long" | "short" | "both" | null;
  commission_per_contract_side?: number | null;
  exchange_fees_per_contract_side?: number | null;
  slippage_ticks?: number | null;
  limit_fill?: "trade_through" | "touch" | null;
}

export interface BlLaunch {
  run: { id: string; status: string };
  readiness: BlReadiness;
  dataset: { id: string; provider: string; symbol: string; start: string; end: string; fixture: boolean; quality: string };
  network: string;
}

export interface QvFinding {
  level: string;
  code: string;
  message: string;
  count: number;
}

export interface QvSeries {
  key: string;
  provider: string;
  source: string;
  fixture: boolean;
  authoritative: boolean;
  dataset: string;
  schema: string;
  stype_in: string;
  symbol: string;
  root: string | null;
  source_minutes: number;
  start: string;
  end: string;
  days: number;
  sessions: number;
  sessions_with_bars: number;
  holes: { start: string; end: string }[];
  segments: { start: string; end: string; sessions: number }[];
  records: number;
  flagged_days: number;
  multi_contract_days: number;
  mapped: boolean;
  corrupt: Record<string, unknown>[];
  grade: string;
  imports: string[];
  jobs: string[];
  derived: string[];
}

export interface QvImport {
  id: string;
  created_at: string;
  status: "STAGED" | "COMMITTED" | "REJECTED" | "EXPIRED";
  format: string;
  filename: string;
  raw_sha256: string;
  bytes: number;
  preview: {
    format: string;
    columns: string[];
    sample: Record<string, unknown>[];
    mapping: Record<string, string | null>;
    timestamp_kind: string;
    contract_roots: string[];
    needs: string[];
    note: string;
    dbn?: { dataset: string; schema: string | null; stype_in: string | null; symbols: string[]; start: number; end: number | null };
  };
  mapping: Record<string, unknown> | null;
  provider: string | null;
  schema: string | null;
  symbol: string | null;
  root: string | null;
  source_minutes: number | null;
  rows_in: number;
  rows_ok: number;
  rows_rejected: number;
  days_added: number;
  days_kept: number;
  findings: QvFinding[];
  error: string | null;
  duplicate?: boolean;
}

export interface QvBackup {
  id: string;
  created_at: string;
  location: string;
  files: number;
  bytes: number;
  status: string;
  verified_at: string | null;
}

export interface QvAcquisition {
  id: string;
  created_at: string;
  updated_at: string;
  request_hash: string;
  request: { dataset: string; schema: string; stype_in: string; symbols: string[]; start: string; end: string; include_definitions: boolean; missing: Record<string, string>[] };
  items: { symbol: string; schema: string; start: string; end: string }[];
  status: "QUOTED" | "APPROVED" | "SUBMITTING" | "SUBMITTED" | "DOWNLOADING" | "INGESTING" | "COMPLETED" | "NOTHING_TO_BUY" | "ATTENTION" | "FAILED" | "EXPIRED" | "CANCELED";
  quote: {
    currency: string;
    cost_usd: number;
    billable_bytes?: number;
    records?: number;
    held_days_deducted?: number;
    held_days?: number;
    jobs?: number;
    source?: string;
    caveat?: string;
    warnings?: string[];
    quoted_at?: string;
    fixture_note?: string | null;
    message?: string;
  } | null;
  expires_at: string | null;
  approved_at: string | null;
  max_usd: number | null;
  job: { jobs?: { symbol: string; schema: string; start: string; end: string; estimated_usd: number; state: string; provider_job: string | null; files: { filename: string; bytes: number; verified: boolean }[] }[] };
  progress: number;
  error_code: string | null;
  error: string | null;
  fixture: boolean;
  approvals?: { id: number; at: string; quoted_usd: number; max_usd: number }[];
}

export interface QvOverview {
  offline: boolean;
  provider_requests: number;
  series: QvSeries[];
  markets: BlMarket[];
  storage: { parts: Record<string, { bytes: number; files: number }>; total_bytes: number; disk_free_bytes: number; disk_total_bytes: number; location: string };
  derived: { dataset_id: string; timeframe: string; bars: number; aggregator: string; created_at: string }[];
  imports: QvImport[];
  issues: { id: number; at: string; kind: string; subject: string; detail: Record<string, unknown> }[];
  last_audit: { checked: number; problems: Record<string, unknown>[]; repaired: Record<string, unknown>[]; ok: boolean; at: string } | null;
  backups: QvBackup[];
  backup_warning: string | null;
  provider: { databento: { status: string; fixture_label: string | null; key_needed_for_backtests: boolean } };
  alternatives: { criteria: string[]; providers: Record<string, string>[]; never: string };
  license: string;
}
