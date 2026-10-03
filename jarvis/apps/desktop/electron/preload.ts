/**
 * Minimal, read-only bridge. The renderer gets configuration, never Node APIs.
 */

import { contextBridge } from "electron";

const backendArg = process.argv.find((arg) => arg.startsWith("--jarvis-backend="));

contextBridge.exposeInMainWorld("jarvis", {
  backendUrl: backendArg ? backendArg.slice("--jarvis-backend=".length) : undefined,
  platform: process.platform,
});
