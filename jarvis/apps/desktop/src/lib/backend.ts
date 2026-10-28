/**
 * Is the backend really there? Two independent sources:
 *
 * - the desktop app's supervisor (electron/backend.ts): did the Python process start, crash,
 *   why — available only inside the app;
 * - the backend's own `/health/ready`: database, schema, data folder and APEX answered a real
 *   request just now — polled while the event stream is connected.
 *
 * The header says "Backend online" only when the event stream is up AND /health/ready passed.
 */

import { useEffect, useSyncExternalStore } from "react";

import { BACKEND_URL } from "./config";

/** Mirror of electron/backend.ts → BackendStatus. */
export type SupervisorState =
  | "starting"
  | "installing"
  | "online"
  | "external"
  | "restarting"
  | "crashed"
  | "unavailable"
  | "stopped";

export type BackendAction = "retry" | "restart" | "repair";

export interface Diagnosis {
  code: string;
  title: string;
  detail: string;
  remedy: string;
  actions: BackendAction[];
}

export interface SupervisorStatus {
  state: SupervisorState;
  url: string;
  port: string;
  python: string | null;
  build: string;
  pid: number | null;
  attempts: number;
  lastExit: { code: number | null; signal: string | null; at: number; uptimeMs: number } | null;
  nextRetryAt: number | null;
  diagnosis: Diagnosis | null;
  log: string[];
  logFile: string | null;
}

export interface BackendBridge {
  status(): Promise<SupervisorStatus>;
  restart(): Promise<SupervisorStatus>;
  repair(): Promise<SupervisorStatus>;
  subscribe(listener: (status: SupervisorStatus) => void): () => void;
}

export const supervisor: BackendBridge | null =
  typeof window !== "undefined" ? (window.jarvis?.backend ?? null) : null;

export interface Readiness {
  status: "unknown" | "ready" | "degraded" | "unreachable";
  checks: { name: string; ok: boolean; detail: string }[];
  checkedAt: number | null;
}

// -- a tiny store shared by the header and the offline panel ---------------------------------

let readiness: Readiness = { status: "unknown", checks: [], checkedAt: null };
let supervised: SupervisorStatus | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export function useReadiness(): Readiness {
  return useSyncExternalStore(subscribe, () => readiness);
}

export function useSupervisor(): SupervisorStatus | null {
  return useSyncExternalStore(subscribe, () => supervised);
}

/** Ask /health/ready once (503 still carries the failing checks). */
export async function checkReadiness(): Promise<Readiness> {
  try {
    const response = await fetch(`${BACKEND_URL}/health/ready`, { signal: AbortSignal.timeout(5000) });
    const body = (await response.json()) as { status?: string; checks?: Readiness["checks"] };
    readiness = {
      status: body.status === "ready" ? "ready" : "degraded",
      checks: body.checks ?? [],
      checkedAt: Date.now(),
    };
  } catch {
    readiness = { status: "unreachable", checks: [], checkedAt: Date.now() };
  }
  emit();
  return readiness;
}

const READY_POLL_MS = 30_000;

/** While the event stream is online, /health/ready is asked now and every 30 s. */
export function useBackendWatch(connection: "connecting" | "online" | "offline"): void {
  useEffect(() => {
    if (connection !== "online") {
      readiness = { status: "unknown", checks: [], checkedAt: null };
      emit();
      return;
    }
    void checkReadiness();
    const timer = window.setInterval(() => void checkReadiness(), READY_POLL_MS);
    return () => window.clearInterval(timer);
  }, [connection]);

  useEffect(() => {
    if (!supervisor) return;
    const set = (status: SupervisorStatus) => {
      supervised = status;
      emit();
    };
    void supervisor.status().then(set);
    return supervisor.subscribe(set);
  }, []);
}

/** The one-line backend state for the header. */
export function headerState(
  connection: "connecting" | "online" | "offline",
  ready: Readiness,
  sup: SupervisorStatus | null,
): { text: string; tone: "success" | "warning" | "danger" } {
  if (connection === "online") {
    if (ready.status === "ready") return { text: "Backend online", tone: "success" };
    if (ready.status === "degraded") return { text: "Backend degraded", tone: "warning" };
    if (ready.status === "unreachable") return { text: "Backend unreachable", tone: "danger" };
    return { text: "Checking backend", tone: "warning" };
  }
  if (sup?.state === "installing") return { text: "Installing backend", tone: "warning" };
  if (sup && ["crashed", "unavailable", "restarting"].includes(sup.state)) return { text: "Backend offline", tone: "danger" };
  return connection === "offline"
    ? { text: "Backend offline", tone: "danger" }
    : { text: "Connecting", tone: "warning" };
}
