import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { Suspense, lazy, useEffect } from "react";

import { BackendBanner } from "./components/BackendBanner";
import { CommandBar } from "./components/command/CommandBar";
import { TopBar } from "./components/TopBar";
import { LoadingState, SafeErrorBoundary } from "./components/ui/shell";
import { useBackendWatch } from "./lib/backend";
import { connectHistory } from "./lib/history";
import { useInterfacePrefs } from "./lib/interface";
import { TABS, tabOf, toHash } from "./lib/routes";
import { connectEvents } from "./lib/socket";
import { connectUpdates } from "./lib/updates";
import { Home } from "./pages/Home";
import { MissionDetail } from "./pages/MissionDetail";
import { dispatch, useJarvis } from "./store/store";

// The heavy areas load on first visit, so the shell and Home appear fast.
const TrainingHub = lazy(() => import("./pages/TrainingHub").then((m) => ({ default: m.TrainingHub })));
const QuantLab = lazy(() => import("./pages/QuantLab").then((m) => ({ default: m.QuantLab })));
const Ultron = lazy(() => import("./pages/Ultron").then((m) => ({ default: m.Ultron })));
const Settings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Settings })));

export function App() {
  const view = useJarvis((s) => s.view);
  const connection = useJarvis((s) => s.connection);
  const prefs = useInterfacePrefs();
  useBackendWatch(connection);
  const tab = tabOf(view);

  useEffect(() => connectEvents(dispatch), []);
  useEffect(() => connectUpdates((status) => dispatch({ type: "updates", status })), []);
  useEffect(() => connectHistory(), []);

  const area = TABS.find((t) => t.id === tab)?.color ?? "var(--color-home)";
  // One mount per page (sub-sections switch inside the page and keep its state).
  const key = view.name === "mission" ? `mission-${view.id}` : tab;
  return (
    <MotionConfig reducedMotion={prefs.motion === "reduced" ? "always" : "user"}>
      <div className="flex h-full flex-col" style={{ "--area": area } as React.CSSProperties} data-tab={tab} data-route={toHash(view)}>
        <TopBar />
        <BackendBanner />
        <AnimatePresence mode="wait" initial={false}>
          <motion.main
            key={key}
            className="min-h-0 flex-1"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
          >
            <SafeErrorBoundary label={TABS.find((t) => t.id === tab)?.label ?? "This page"}>
              <Suspense fallback={<div className="p-8"><LoadingState /></div>}>
                {view.name === "home" && <Home />}
                {view.name === "mission" && <MissionDetail id={view.id} />}
                {view.name === "training" && <TrainingHub />}
                {view.name === "quantlab" && <QuantLab />}
                {view.name === "ultron" && <Ultron />}
                {view.name === "settings" && <Settings />}
              </Suspense>
            </SafeErrorBoundary>
          </motion.main>
        </AnimatePresence>
        <CommandBar />
      </div>
    </MotionConfig>
  );
}
