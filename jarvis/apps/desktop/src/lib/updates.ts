/**
 * In-app updates (Electron main process, see electron/updater.ts). Available
 * when JARVIS runs as the app or via `npm start`; absent in a plain browser.
 */

/** Mirror of electron/updater.ts → UpdateStatus. */
export type UpdateStatus =
  | { state: "off"; reason: string }
  | { state: "checking" }
  | { state: "current"; checkedAt: number }
  | { state: "available"; behind: number; latest: string; checkedAt: number }
  | { state: "updating"; step: string }
  | { state: "restarting" }
  | { state: "error"; message: string; detail?: string; canRetry: boolean };

export interface UpdatesBridge {
  status(): Promise<UpdateStatus>;
  check(): Promise<UpdateStatus>;
  apply(): Promise<UpdateStatus>;
  subscribe(listener: (status: UpdateStatus) => void): () => void;
}

export const updates: UpdatesBridge | null =
  typeof window !== "undefined" ? (window.jarvis?.updates ?? null) : null;

export function connectUpdates(onStatus: (status: UpdateStatus) => void): () => void {
  if (!updates) return () => {};
  void updates.status().then(onStatus);
  return updates.subscribe(onStatus);
}
