import { useEffect, useState } from "react";
import { create } from "zustand";

import { type Action, type UIState, initialState, reduce } from "./reducer";

interface Store extends UIState {
  dispatch: (action: Action) => void;
}

export const useJarvis = create<Store>((set) => ({
  ...initialState,
  dispatch: (action) => set((state) => reduce(state, action)),
}));

export const dispatch = (action: Action): void => useJarvis.getState().dispatch(action);

/** Re-render on an interval (clock, fading messages). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
