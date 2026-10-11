/**
 * Keeps the address (hash) and the store's view in step: navigation pushes a history entry,
 * back/forward and reload restore the view. Home is the launch page when there is no hash.
 */

import { fromHash, sameView, toHash, withMemory } from "./routes";
import { dispatch, useJarvis } from "../store/store";

export function connectHistory(): () => void {
  if (typeof window === "undefined") return () => {};
  // Launch / reload: the address decides (a reload keeps the page).
  if (window.location.hash) dispatch({ type: "navigate", view: fromHash(window.location.hash) });
  else window.history.replaceState(null, "", toHash(useJarvis.getState().view));

  const onPop = () => {
    const next = fromHash(window.location.hash);
    if (!sameView(next, useJarvis.getState().view)) dispatch({ type: "navigate", view: next });
  };
  window.addEventListener("popstate", onPop);
  window.addEventListener("hashchange", onPop);

  const unsubscribe = useJarvis.subscribe((state, previous) => {
    if (state.view === previous.view) return;
    const hash = toHash(state.view);
    if (hash !== window.location.hash) window.history.pushState(null, "", hash);
  });
  return () => {
    window.removeEventListener("popstate", onPop);
    window.removeEventListener("hashchange", onPop);
    unsubscribe();
  };
}

/** Navigate like a click on a tab: a bare tab reopens its last sub-section. */
export function go(view: Parameters<typeof withMemory>[0]): void {
  dispatch({ type: "navigate", view: withMemory(view) });
}
