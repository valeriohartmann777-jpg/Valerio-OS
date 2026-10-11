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
