import type { ServerMessage } from "@jarvis/protocol";

import type { Action } from "../store/reducer";
import { EVENTS_URL } from "./config";

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
      } else if (data.kind === "event") {
        dispatch({ type: "event", event: data.data, now: Date.now() });
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
