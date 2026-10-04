/**
 * Minimal bridge. The renderer gets configuration and the update controls —
 * never Node APIs.
 */

import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";

const backendArg = process.argv.find((arg) => arg.startsWith("--jarvis-backend="));

contextBridge.exposeInMainWorld("jarvis", {
  backendUrl: backendArg ? backendArg.slice("--jarvis-backend=".length) : undefined,
  platform: process.platform,
  updates: {
    status: () => ipcRenderer.invoke("jarvis:update:status"),
    check: () => ipcRenderer.invoke("jarvis:update:check"),
    apply: () => ipcRenderer.invoke("jarvis:update:apply"),
    subscribe: (listener: (status: unknown) => void) => {
      const handler = (_event: IpcRendererEvent, status: unknown) => listener(status);
      ipcRenderer.on("jarvis:update:status", handler);
      return () => {
        ipcRenderer.removeListener("jarvis:update:status", handler);
      };
    },
  },
});
