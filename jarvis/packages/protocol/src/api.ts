/** HTTP + WebSocket API contract. Mirrors backend/jarvis/api/schemas.py. */

import type { JarvisEvent } from "./events";
import type { ApprovalPolicy, Snapshot, StateSnapshot } from "./models";

export const DEFAULT_BACKEND_URL = "http://127.0.0.1:8765";

export interface Health {
  status: "ok";
  version: string;
  uptime_seconds: number;
}

export interface SystemStatus {
  state: StateSnapshot;
  version: string;
  uptime_seconds: number;
  system_backend: string;
  simulated: boolean;
  clients: number;
  pending_approvals: number;
  active_missions: number;
}

export interface ChatRequest {
  text: string;
}

export interface ChatAccepted {
  accepted: true;
  trace_id: string;
}

export interface ApproveRequest {
  strong_confirmation?: boolean;
}

export interface RejectRequest {
  note?: string;
}

export interface LevelPolicy {
  level: number;
  label: string;
  policy: ApprovalPolicy;
}

export interface SettingsView {
  version: string;
  system_backend: string;
  simulated: boolean;
  personality_name: string;
  personality_traits: string[];
  permission_levels: LevelPolicy[];
  tool_overrides: Record<string, ApprovalPolicy>;
  disabled_categories: string[];
  approval_timeout_seconds: number;
  models: Record<string, string>;
  known_apps: string[];
  config_dir: string;
  database_path: string;
}

/** Messages on WS /events (server → client). */
export type ServerMessage =
  | { kind: "snapshot"; data: Snapshot }
  | { kind: "event"; data: JarvisEvent }
  | { kind: "pong" };

/** Messages on WS /events (client → server). */
export type ClientMessage = { kind: "ping" };
