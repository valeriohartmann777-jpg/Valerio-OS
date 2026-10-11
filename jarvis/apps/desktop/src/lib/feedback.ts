/**
 * Owner feedback on JARVIS's replies (Training Hub evidence). One shared, lazily loaded view
 * of the backend's ratings so every reply knows whether it was rated.
 */

import type { FeedbackOverview } from "@jarvis/protocol";
import { useSyncExternalStore } from "react";

import { api } from "./api";

let current: FeedbackOverview | null = null;
let loading = false;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

export async function loadFeedback(): Promise<void> {
  if (loading) return;
  loading = true;
  try {
    current = await api.feedback();
    notify();
  } catch {
    /* no feedback service reachable: replies just show no rating */
  } finally {
    loading = false;
  }
}

export async function rate(eventId: string, rating: 1 | -1, note?: string): Promise<void> {
  current = await api.rateReply(eventId, rating, note);
  notify();
}

export function useFeedback(): FeedbackOverview | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      if (!current) void loadFeedback();
      return () => listeners.delete(listener);
    },
    () => current,
    () => null,
  );
}
