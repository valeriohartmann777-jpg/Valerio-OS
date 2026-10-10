/** AI routing & billing (Settings → AI & Billing): Claude plan → Claude API → local / pause. */

export type AiRoute = "plan" | "api" | "local" | "paused";

export interface AiProviderBase {
  provider: "plan" | "api" | "local";
  state: string;
  detail: string;
  checked_at: string | null;
  until: string | null;
  label: string;
}

export interface AiPlanStatus extends AiProviderBase {
  enabled: boolean;
  confirmed_at: string | null;
  login_method: string | null;
  version: string | null;
  usage: string;
  custom_binary: string | null;
}

export interface AiPaidSettings {
  enabled: boolean;
  monthly_budget_usd: number;
  per_mission_cap_usd: number;
  warn_at: number[];
  max_concurrent: number;
  stop_at_budget: boolean;
  approved_at: string | null;
}

export interface AiApiStatus extends AiProviderBase {
  key_hint: string | null;
  key_source: "keystore" | "env_file" | "environment" | null;
  keystore: { available: boolean; backend: string; reason: string | null; test_only: boolean };
  paid: AiPaidSettings;
  month: string;
  month_spent_usd: number;
  budget_left_usd: number | null;
  in_flight: number;
}

export interface AiLocalStatus extends AiProviderBase {
  enabled: boolean;
  base_url: string;
  model: string;
}

export interface AiUsageRow {
  provider: string;
  calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  cost_usd: number | null;
  list_cost_usd: number | null;
  failures: number;
}

export interface AiStatus {
  strategy: "subscription_first" | "plan_only" | "api_only";
  profile: "economy" | "balanced" | "deep";
  order: string[];
  current: { route: AiRoute; label: string; reason: string };
  providers: { plan: AiPlanStatus; api: AiApiStatus; local: AiLocalStatus };
  currency: { display: "USD" | "CHF"; usd_to_chf: number | null };
  usage: AiUsageRow[];
  by_consumer: { consumer: string; provider: string; calls: number; cost_usd: number | null }[];
  credits_note: string;
  keystore_test_only: boolean;
}

export interface AiEvent {
  id: number;
  at: string;
  kind: string;
  provider: string | null;
  message: string;
  data: Record<string, unknown> | null;
}

export interface AiCall {
  id: number;
  at: string;
  provider: string;
  model: string;
  consumer: string;
  agent: string | null;
  mission_id: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  billed: number;
  list_cost_usd: number | null;
  outcome: string;
  failure: string | null;
  latency_ms: number | null;
}

export interface AiUsage {
  calls: AiCall[];
  events: AiEvent[];
  approvals: { id: number; kind: string; detail: Record<string, unknown>; at: string }[];
}
