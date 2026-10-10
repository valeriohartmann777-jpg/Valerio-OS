import { AnimatePresence, motion } from "motion/react";
import { useEffect } from "react";

import { TopBar } from "./components/TopBar";
import { CommandBar } from "./components/command/CommandBar";
import { connectEvents } from "./lib/socket";
import { connectUpdates } from "./lib/updates";
import { Home } from "./pages/Home";
import { Bots } from "./pages/Bots";
import { Learning } from "./pages/Learning";
import { MissionDetail } from "./pages/MissionDetail";
import { QuantLab } from "./pages/QuantLab";
import { Ultron } from "./pages/Ultron";
import { Settings } from "./pages/Settings";
import { dispatch, useJarvis } from "./store/store";

export function App() {
  const view = useJarvis((s) => s.view);

  useEffect(() => connectEvents(dispatch), []);
  useEffect(() => connectUpdates((status) => dispatch({ type: "updates", status })), []);

  const key = view.name === "mission" ? `mission-${view.id}` : view.name;
  return (
    <div className="flex h-full flex-col bg-canvas">
      <TopBar />
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={key}
          className="min-h-0 flex-1"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
        >
          {view.name === "home" && <Home />}
          {view.name === "mission" && <MissionDetail id={view.id} />}
          {view.name === "learning" && <Learning />}
          {view.name === "bots" && <Bots />}
          {view.name === "quantlab" && <QuantLab />}
          {view.name === "ultron" && <Ultron />}
          {view.name === "settings" && <Settings />}
        </motion.div>
      </AnimatePresence>
      <CommandBar />
    </div>
  );
}
