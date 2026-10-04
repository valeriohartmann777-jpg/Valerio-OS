import type { AgentState, JarvisEvent, Mission, PermissionRequest, Snapshot } from "@jarvis/protocol";
import { describe, expect, it } from "vitest";

import {
  MAX_ACTIVITY,
  RECENT_MISSION_MS,
  type UIState,
  busyAgent,
  featuredMission,
  initialState,
  reduce,
  visibleActivity,
} from "./reducer";

let counter = 0;
function event(type: JarvisEvent["type"], payload: Record<string, unknown> = {}, extra: Partial<JarvisEvent> = {}) {
  counter += 1;
  return {
    id: `e${counter}`,
    type,
    timestamp: new Date(1_700_000_000_000 + counter * 1000).toISOString(),
    severity: "info",
    source: "jarvis",
    message: type,
    trace_id: "t1",
    mission_id: null,
    payload,
    ...extra,
  } satisfies JarvisEvent;
}

function mission(overrides: Partial<Mission> = {}): Mission {
  return {
    id: "m1",
    number: 1,
    title: "Open Notepad",
    goal: "open notepad",
    status: "active",
    priority: "normal",
    current_step: 0,
    steps: [],
    agents: ["operator", "sentinel"],
    tools_used: [],
    approvals: [],
    result: null,
    errors: [],
    verification: null,
    trace_id: "t1",
    created_at: new Date(0).toISOString(),
    updated_at: new Date(0).toISOString(),
    finished_at: null,
    ...overrides,
  };
}

function agent(overrides: Partial<AgentState> = {}): AgentState {
  return {
    id: "operator",
    name: "Operator",
    role: "Computer interaction",
    description: "",
    available: true,
    available_in_phase: null,
    tools: [],
    max_permission_level: 4,
    status: "idle",
    current_task: null,
    detail: null,
    mission_id: null,
    last_outcome: null,
    last_summary: null,
    updated_at: new Date(0).toISOString(),
    ...overrides,
  };
}

function snapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    version: "0.1.0",
    build: "070a0b5c0ffee",
    brain: {
      available: true,
      fast_model: "claude-sonnet-5-5",
      reasoning_model: "claude-opus-5-5",
      reason: null,
      key_hint: "AbCd",
    },
    voice: {
      state: "ready",
      configured: true,
      wake_word: true,
      wake_word_active: true,
      speak_replies: false,
      voice_name: "Daniel",
      key_hint: "WXYZ",
      reason: null,
    },
    learning: {
      state: "off",
      enabled: false,
      detail: null,
      progress: null,
      model: "claude-opus-5-5",
      budget_usd: 3,
      spent_today_usd: 0,
      stall_rounds: 0,
      stall_limit: 20,
      next_round_at: null,
      round: null,
      web_search: true,
      focus: "Support and resistance on NQ and XAUUSD.",
      counts: {},
      data: {},
    },
    state: { state: "DORMANT", detail: "" },
    system_backend: "simulated",
    simulated: true,
    context: null,
    agents: [agent()],
    missions: [],
    approvals: [],
    activity: [],
    ...overrides,
  };
}

const apply = (state: UIState, e: JarvisEvent, now = 0) => reduce(state, { type: "event", event: e, now });

describe("reducer", () => {
  it("applies a snapshot and goes online", () => {
    const message = event("jarvis.message", { text: "Notepad is open.", success: true, error: null });
    const state = reduce(initialState, {
      type: "snapshot",
      snapshot: snapshot({ activity: [message], missions: [mission({ number: 1 }), mission({ id: "m2", number: 2 })] }),
      now: 5,
    });
    expect(state.connection).toBe("online");
    expect(state.simulated).toBe(true);
    expect(state.missions.map((m) => m.number)).toEqual([2, 1]);
    expect(state.lastMessage?.text).toBe("Notepad is open.");
    expect(state.brain?.fast_model).toBe("claude-sonnet-5-5");
  });

  it("follows the open-notepad event chain", () => {
    let state = reduce(initialState, { type: "snapshot", snapshot: snapshot(), now: 0 });
    state = apply(state, event("jarvis.state.changed", { state: "EXECUTING", detail: "Launch Notepad" }), 10);
    state = apply(state, event("mission.created", { mission: mission() }));
    state = apply(state, event("agent.started", { agent: agent({ status: "active", current_task: "Launch Notepad" }) }));
    expect(state.jarvis).toEqual({ state: "EXECUTING", detail: "Launch Notepad", since: 10 });
    expect(busyAgent(state)?.current_task).toBe("Launch Notepad");
    expect(featuredMission(state, 0)?.id).toBe("m1");

    const done = mission({ status: "complete", finished_at: new Date(1000).toISOString() });
    state = apply(state, event("mission.updated", { mission: done }));
    state = apply(state, event("agent.completed", { agent: agent() }));
    state = apply(state, event("jarvis.message", { text: "Notepad is open.", success: true, error: null }));
    expect(state.missions).toHaveLength(1);
    expect(state.missions[0]?.status).toBe("complete");
    expect(busyAgent(state)).toBeNull();
    expect(state.lastMessage?.text).toBe("Notepad is open.");
    expect(featuredMission(state, 1000 + RECENT_MISSION_MS - 1)?.id).toBe("m1");
    expect(featuredMission(state, 1000 + RECENT_MISSION_MS + 1)).toBeNull();
  });

  it("tracks pending approvals", () => {
    const request = { id: "r1" } as PermissionRequest;
    let state = apply(initialState, event("permission.requested", { request }));
    state = apply(state, event("permission.requested", { request }));
    expect(state.approvals).toHaveLength(1);
    state = apply(state, event("permission.approved", { request }));
    expect(state.approvals).toHaveLength(0);
  });

  it("keeps telemetry out of the activity stream and dedupes events", () => {
    const e = event("tool.started");
    let state = apply(initialState, e);
    state = apply(state, e);
    state = apply(state, event("context.updated", { context: { cpu_percent: 3 } }));
    expect(state.activity.map((a) => a.id)).toEqual([e.id]);
    expect(state.context).toEqual({ cpu_percent: 3 });
  });

  it("hides debug events unless enabled and bounds history", () => {
    let state = initialState;
    for (let i = 0; i < MAX_ACTIVITY + 10; i += 1) {
      state = apply(state, event("tool.started", {}, { severity: i % 2 ? "debug" : "info" }));
    }
    expect(state.activity).toHaveLength(MAX_ACTIVITY);
    expect(visibleActivity(state).every((e) => e.severity !== "debug")).toBe(true);
    state = reduce(state, { type: "toggleDebug" });
    expect(visibleActivity(state)).toHaveLength(MAX_ACTIVITY);
  });

  it("switches the brain on when a key is connected", () => {
    const offline = { available: false, fast_model: null, reasoning_model: null, reason: "No API key yet.", key_hint: null };
    let state = reduce(initialState, { type: "snapshot", snapshot: snapshot({ brain: offline }), now: 0 });
    expect(state.brain?.available).toBe(false);
    const online = { ...snapshot().brain };
    state = apply(state, event("brain.changed", { brain: online }, { severity: "important" }));
    expect(state.brain).toEqual(online);
    expect(visibleActivity(state).at(-1)?.type).toBe("brain.changed");
  });

  it("follows the voice state", () => {
    let state = reduce(initialState, { type: "snapshot", snapshot: snapshot(), now: 0 });
    expect(state.voice?.state).toBe("ready");
    const listening = { ...snapshot().voice, state: "listening" as const };
    state = apply(state, event("voice.changed", { voice: listening }, { severity: "debug" }));
    expect(state.voice?.state).toBe("listening");
  });

  it("follows the learning state", () => {
    let state = reduce(initialState, { type: "snapshot", snapshot: snapshot(), now: 0 });
    expect(state.learning?.state).toBe("off");
    const running = { ...snapshot().learning, state: "running" as const, enabled: true, round: 3 };
    state = apply(state, event("learning.changed", { learning: running }, { severity: "info" }));
    expect(state.learning?.round).toBe(3);
    expect(visibleActivity(state).at(-1)?.type).toBe("learning.changed");
  });

  it("keeps the conversation in order, merges history with live messages", () => {
    const said = event("command.received", { text: "hello", source: "text" });
    const answer = event("jarvis.message", { text: "Online.", success: true, error: null });
    let state = apply(initialState, said);
    state = apply(state, event("tool.started", {}));
    expect(state.conversation.map((e) => e.id)).toEqual([said.id]);
    // History arrives after a live message: no duplicates, chronological order.
    const older = event("command.received", { text: "earlier", source: "voice" }, { timestamp: "2020-01-01T00:00:00Z" });
    state = reduce(state, { type: "conversation", events: [older, said, answer], complete: true });
    expect(state.conversation.map((e) => e.id)).toEqual([older.id, said.id, answer.id]);
    expect(state.conversationComplete).toBe(true);
  });

  it("navigates and tracks connection", () => {
    let state = reduce(initialState, { type: "navigate", view: { name: "mission", id: "m1" } });
    state = reduce(state, { type: "connection", status: "offline" });
    expect(state.view).toEqual({ name: "mission", id: "m1" });
    expect(state.connection).toBe("offline");
  });
});
