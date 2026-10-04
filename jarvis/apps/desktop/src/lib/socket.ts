import type { JarvisEvent, ServerMessage } from "@jarvis/protocol";

import type { Action } from "../store/reducer";
import { api } from "./api";
import { EVENTS_URL } from "./config";

export const CONVERSATION_PAGE = 100;

/** The chat history is loaded on every (re)connect; live events extend it. */
async function loadConversation(dispatch: (action: Action) => void): Promise<void> {
  try {
    const events = await api.conversation(CONVERSATION_PAGE);
    dispatch({ type: "conversation", events, complete: events.length < CONVERSATION_PAGE });
  } catch {
    /* the live stream still works; the history loads on the next connect */
  }
}

const PING_MS = 15_000;
const MAX_BACKOFF_MS = 5_000;

/** Keeps a live event stream open, reconnecting with backoff. Returns a disposer. */
export function connectEvents(dispatch: (action: Action) => void): () => void {
  let socket: WebSocket | null = null;
  let attempt = 0;
  let wasOnline = false;
  let disposed = false;
  let retryTimer: number | undefined;
  let pingTimer: number | undefined;

  const open = () => {
    socket = new WebSocket(EVENTS_URL);

    socket.onopen = () => {
      attempt = 0;
      pingTimer = window.setInterval(() => socket?.send(JSON.stringify({ kind: "ping" })), PING_MS);
    };

    socket.onmessage = (message: MessageEvent<string>) => {
      const data = JSON.parse(message.data) as ServerMessage;
      if (data.kind === "snapshot") {
        wasOnline = true;
        dispatch({ type: "snapshot", snapshot: data.data, now: Date.now() });
        void loadConversation(dispatch);
      } else if (data.kind === "event") {
        dispatch({ type: "event", event: data.data, now: Date.now() });
        notifyBriefing(data.data);
      }
    };

    socket.onerror = () => socket?.close();

    socket.onclose = () => {
      window.clearInterval(pingTimer);
      if (disposed) return;
      dispatch({ type: "connection", status: wasOnline ? "offline" : "connecting" });
      attempt += 1;
      retryTimer = window.setTimeout(open, Math.min(MAX_BACKOFF_MS, 400 * 2 ** attempt));
    };
  };

  dispatch({ type: "connection", status: "connecting" });
  open();

  return () => {
    disposed = true;
    window.clearTimeout(retryTimer);
    window.clearInterval(pingTimer);
    socket?.close();
  };
}

/** A system notification when the morning briefing arrives while JARVIS is in the background. */
function notifyBriefing(event: JarvisEvent): void {
  const payload = event.payload as { kind?: string };
  if (event.type !== "jarvis.message" || payload.kind !== "briefing") return;
  if (!document.hidden || typeof Notification === "undefined") return;
  try {
    new Notification("JARVIS", { body: "Your morning briefing for NQ and gold is ready.", silent: false });
  } catch {
    /* notifications unavailable */
  }
}
