/**
 * Pure state machine: snapshot + live events → dashboard state.
 * No I/O here; everything is unit-tested in reducer.test.ts.
 */

import type {
  AgentState,
  BrainStatus,
  EnvironmentContext,
  JarvisEvent,
  JarvisMessagePayload,
  JarvisState,
  LearningStatus,
  Mission,
  PermissionRequest,
  Snapshot,
  VoiceStatus,
} from "@jarvis/protocol";

import type { UpdateStatus } from "../lib/updates";

export type View =
  | { name: "home" }
  | { name: "mission"; id: string }
  | { name: "learning" }
  | { name: "settings" };
export type Connection = "connecting" | "online" | "offline";

export interface LastMessage extends JarvisMessagePayload {
  at: number;
  traceId: string | null;
  missionId: string | null;
}

export interface UIState {
  connection: Connection;
  version: string | null;
  build: string | null;
  systemBackend: string | null;
  simulated: boolean;
  brain: BrainStatus | null;
  voice: VoiceStatus | null;
  learning: LearningStatus | null;
  jarvis: { state: JarvisState; detail: string; since: number };
  context: EnvironmentContext | null;
  agents: AgentState[];
  missions: Mission[];
  approvals: PermissionRequest[];
  activity: JarvisEvent[];
  lastMessage: LastMessage | null;
  view: View;
  showDebug: boolean;
  /** In-app update state from the Electron main process (null outside Electron). */
  updates: UpdateStatus | null;
  /** The chat: commands and JARVIS's replies, oldest first (persisted by the backend). */
  conversation: JarvisEvent[];
  /** There is nothing older to load. */
  conversationComplete: boolean;
}

export type Action =
  | { type: "connection"; status: Connection }
  | { type: "snapshot"; snapshot: Snapshot; now: number }
  | { type: "event"; event: JarvisEvent; now: number }
  | { type: "navigate"; view: View }
  | { type: "toggleDebug" }
  | { type: "updates"; status: UpdateStatus }
  | { type: "conversation"; events: JarvisEvent[]; complete: boolean; older?: boolean };

export const MAX_ACTIVITY = 400;
export const MAX_MISSIONS = 50;
const TERMINAL = new Set(["complete", "failed", "stopped"]);
/** How long a finished mission stays in the "active" slot on the home screen. */
export const RECENT_MISSION_MS = 15_000;

export const initialState: UIState = {
  connection: "connecting",
  version: null,
  build: null,
  systemBackend: null,
  simulated: false,
  brain: null,
  voice: null,
  learning: null,
  jarvis: { state: "DORMANT", detail: "", since: 0 },
  context: null,
  agents: [],
  missions: [],
  approvals: [],
  activity: [],
  lastMessage: null,
  view: { name: "home" },
  showDebug: false,
  updates: null,
  conversation: [],
  conversationComplete: false,
};

export const CHAT_EVENTS = new Set(["command.received", "jarvis.message"]);
export const MAX_CONVERSATION = 1000;

export function reduce(state: UIState, action: Action): UIState {
  switch (action.type) {
    case "connection":
      return { ...state, connection: action.status };
    case "navigate":
      return { ...state, view: action.view };
    case "toggleDebug":
      return { ...state, showDebug: !state.showDebug };
    case "updates":
      return { ...state, updates: action.status };
    case "conversation":
      return {
        ...state,
        conversation: mergeConversation(state.conversation, action.events, Boolean(action.older)),
        // Paging back only ever learns that the start was reached; a fresh load says so itself.
        conversationComplete: action.older ? action.complete || state.conversationComplete : action.complete,
      };
    case "snapshot":
      return applySnapshot(state, action.snapshot, action.now);
    case "event":
      return applyEvent(state, action.event, action.now);
  }
}

function applySnapshot(state: UIState, snapshot: Snapshot, now: number): UIState {
  const activity = snapshot.activity.filter((e) => e.type !== "context.updated").slice(-MAX_ACTIVITY);
  const lastMessageEvent = [...activity].reverse().find((e) => e.type === "jarvis.message");
  return {
    ...state,
    connection: "online",
    version: snapshot.version,
    build: snapshot.build,
    systemBackend: snapshot.system_backend,
    simulated: snapshot.simulated,
    brain: snapshot.brain,
    voice: snapshot.voice,
    learning: snapshot.learning ?? null,
    jarvis: { state: snapshot.state.state, detail: snapshot.state.detail, since: now },
    context: snapshot.context,
    agents: snapshot.agents,
    missions: sortMissions(snapshot.missions),
    approvals: snapshot.approvals,
    activity,
    lastMessage: lastMessageEvent ? toLastMessage(lastMessageEvent) : null,
  };
}

function applyEvent(state: UIState, event: JarvisEvent, now: number): UIState {
  const next: UIState = { ...state, activity: appendActivity(state.activity, event) };
  if (CHAT_EVENTS.has(event.type)) next.conversation = mergeConversation(state.conversation, [event]);
  const payload = event.payload as Record<string, unknown>;
  switch (event.type) {
    case "jarvis.state.changed":
      next.jarvis = {
        state: payload.state as JarvisState,
        detail: (payload.detail as string | undefined) ?? "",
        since: now,
      };
      break;
    case "jarvis.message":
      next.lastMessage = toLastMessage(event);
      break;
    case "mission.created":
    case "mission.updated":
      next.missions = upsertMission(state.missions, payload.mission as Mission);
      break;
    case "agent.started":
    case "agent.updated":
    case "agent.completed":
    case "agent.failed":
      next.agents = upsertById(state.agents, payload.agent as AgentState);
      break;
    case "permission.requested": {
      const request = payload.request as PermissionRequest;
      if (!state.approvals.some((a) => a.id === request.id)) {
        next.approvals = [...state.approvals, request];
      }
      break;
    }
    case "permission.approved":
    case "permission.rejected":
    case "permission.expired": {
      const request = payload.request as PermissionRequest;
      next.approvals = state.approvals.filter((a) => a.id !== request.id);
      break;
    }
    case "context.updated":
      next.context = payload.context as EnvironmentContext;
      break;
    case "brain.changed":
      next.brain = payload.brain as BrainStatus;
      break;
    case "voice.changed":
      next.voice = payload.voice as VoiceStatus;
      break;
    case "learning.changed":
      next.learning = payload.learning as LearningStatus;
      break;
    case "system.online":
      next.version = (payload.version as string | undefined) ?? state.version;
      next.systemBackend = (payload.system_backend as string | undefined) ?? state.systemBackend;
      break;
    default:
      break;
  }
  return next;
}

/** Merge by id, keep chronological order; drop the oldest beyond the cap (newest when paging back). */
export function mergeConversation(existing: JarvisEvent[], incoming: JarvisEvent[], older = false): JarvisEvent[] {
  const byId = new Map(existing.map((e) => [e.id, e]));
  for (const event of incoming) byId.set(event.id, event);
  const merged = [...byId.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  if (merged.length <= MAX_CONVERSATION) return merged;
  return older ? merged.slice(0, MAX_CONVERSATION) : merged.slice(-MAX_CONVERSATION);
}

function appendActivity(activity: JarvisEvent[], event: JarvisEvent): JarvisEvent[] {
  if (event.type === "context.updated") return activity;
  if (activity.slice(-50).some((e) => e.id === event.id)) return activity;
  const next = [...activity, event];
  return next.length > MAX_ACTIVITY ? next.slice(-MAX_ACTIVITY) : next;
}

function toLastMessage(event: JarvisEvent): LastMessage {
  const payload = event.payload as unknown as JarvisMessagePayload;
  return {
    text: payload.text,
    success: payload.success,
    error: payload.error ?? null,
    at: Date.parse(event.timestamp),
    traceId: event.trace_id,
    missionId: event.mission_id,
  };
}

function upsertById<T extends { id: string }>(items: T[], item: T): T[] {
  const index = items.findIndex((existing) => existing.id === item.id);
  if (index === -1) return [...items, item];
  const next = items.slice();
  next[index] = item;
  return next;
}

function upsertMission(missions: Mission[], mission: Mission): Mission[] {
  return sortMissions(upsertById(missions, mission)).slice(0, MAX_MISSIONS);
}

function sortMissions(missions: Mission[]): Mission[] {
  return missions.slice().sort((a, b) => b.number - a.number);
}

// Selectors ------------------------------------------------------------------

export function isTerminal(mission: Mission): boolean {
  return TERMINAL.has(mission.status);
}

/** The mission the home screen should feature: running, or just finished. */
export function featuredMission(state: UIState, now: number): Mission | null {
  const running = state.missions.find((m) => !isTerminal(m));
  if (running) return running;
  const latest = state.missions[0];
  if (latest?.finished_at && now - Date.parse(latest.finished_at) < RECENT_MISSION_MS) return latest;
  return null;
}

export function visibleActivity(state: UIState): JarvisEvent[] {
  if (state.showDebug) return state.activity;
  return state.activity.filter((e) => e.severity !== "debug");
}

export function busyAgent(state: UIState): AgentState | null {
  return state.agents.find((a) => a.status === "active" || a.status === "waiting") ?? null;
}
