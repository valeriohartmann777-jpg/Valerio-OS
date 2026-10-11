/**
 * Settings → Interface: motion, contrast and how detailed the ULTRON HQ world is drawn.
 * Per-viewer conveniences, so they live in local storage (wrapped: storage can be unavailable)
 * and apply as attributes on <html> (`data-motion`, `data-contrast`).
 */

import { useSyncExternalStore } from "react";

export interface InterfacePrefs {
  motion: "system" | "reduced";
  contrast: "standard" | "high";
  /** "simple" draws the HQ as a list of rooms without the scene. */
  world: "full" | "simple";
}

const KEY = "jarvis.interface";
const DEFAULTS: InterfacePrefs = { motion: "system", contrast: "standard", world: "full" };

function read(): InterfacePrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}") as Partial<InterfacePrefs>;
    return {
      motion: raw.motion === "reduced" ? "reduced" : "system",
      contrast: raw.contrast === "high" ? "high" : "standard",
      world: raw.world === "simple" ? "simple" : "full",
    };
  } catch {
    return DEFAULTS;
  }
}

let current = read();
const listeners = new Set<() => void>();

function apply(prefs: InterfacePrefs): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.motion = prefs.motion;
  root.dataset.contrast = prefs.contrast;
}
apply(current);

export function setInterfacePrefs(next: Partial<InterfacePrefs>): void {
  current = { ...current, ...next };
  try {
    localStorage.setItem(KEY, JSON.stringify(current));
  } catch {
    /* not remembered, still applied for this session */
  }
  apply(current);
  for (const listener of listeners) listener();
}

export function useInterfacePrefs(): InterfacePrefs {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
    () => DEFAULTS,
  );
}
