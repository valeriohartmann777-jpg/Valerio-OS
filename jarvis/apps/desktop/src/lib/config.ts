import { DEFAULT_BACKEND_URL } from "@jarvis/protocol";

import type { UpdatesBridge } from "./updates";

export interface LoginItem {
  supported: boolean;
  enabled: boolean;
  /** macOS 13+: registered, but still to be allowed in System Settings → Login Items. */
  needsApproval: boolean;
}

export interface BackgroundInfo {
  /** Closing the window keeps JARVIS running (menu bar / tray). */
  keepsRunning: boolean;
  login: LoginItem;
}

interface AppBridge {
  background: () => Promise<BackgroundInfo>;
  setStartAtLogin: (enabled: boolean) => Promise<LoginItem>;
}

interface Bridge {
  backendUrl?: string;
  platform?: string;
  updates?: UpdatesBridge;
  app?: AppBridge;
}

declare global {
  interface Window {
    jarvis?: Bridge;
  }
}

const bridge: Bridge = typeof window !== "undefined" ? (window.jarvis ?? {}) : {};

export const BACKEND_URL: string =
  bridge.backendUrl ?? import.meta.env.VITE_JARVIS_BACKEND_URL ?? DEFAULT_BACKEND_URL;

export const EVENTS_URL = `${BACKEND_URL.replace(/^http/, "ws")}/events`;

/** Native window controls are drawn over the top-right corner on Windows. */
export const HAS_TITLEBAR_OVERLAY = bridge.platform === "win32";

/** macOS draws the traffic-light buttons over the top-left corner. */
export const HAS_TRAFFIC_LIGHTS = bridge.platform === "darwin";

export const IS_ELECTRON = bridge.platform !== undefined;

/** Background and start-at-login controls (desktop app only). */
export const APP_BRIDGE: AppBridge | null = bridge.app ?? null;
