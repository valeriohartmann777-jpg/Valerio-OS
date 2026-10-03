import type { Mission } from "@jarvis/protocol";
import { motion } from "motion/react";

import { duration, missionNumber, missionStatusLabel } from "../../lib/format";
import { dispatch, useNow } from "../../store/store";
import { Pill, StatusDot } from "../ui/primitives";
import { MISSION_TONE } from "../ui/status";
import { MissionControls } from "./MissionControls";
import { StepList } from "./StepList";

export function ActiveMission({ mission }: { mission: Mission }) {
  const now = useNow(500);
  const tone = MISSION_TONE[mission.status];
  const live = mission.status === "active" || mission.status === "waiting_for_approval";
  return (
    <motion.section
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 6 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="w-full max-w-[560px] rounded-xl border border-hairline bg-surface/70 px-5 py-4"
      data-testid="active-mission"
    >
      <header className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="label">Mission {missionNumber(mission.number)}</span>
          <Pill tone={tone}>
            <StatusDot tone={tone} live={live} />
            {missionStatusLabel(mission.status)}
          </Pill>
        </div>
        <span className="font-mono text-xs text-fg-faint tabular">
          {duration(mission.created_at, mission.finished_at, now)}
        </span>
      </header>
      <h2 className="mt-2.5 text-[15px] font-medium text-fg">{mission.title}</h2>
      <div className="mt-3">
        <StepList steps={mission.steps} />
      </div>
      <footer className="mt-3 flex items-center justify-between gap-4 border-t border-hairline pt-3">
        <span className="truncate text-xs text-fg-faint">
          {mission.verification ? mission.verification.summary : `Goal: “${mission.goal}”`}
        </span>
        <div className="flex shrink-0 items-center gap-2">
          <MissionControls mission={mission} />
          <button
            type="button"
            className="text-xs text-fg-muted transition-colors hover:text-fg"
            onClick={() => dispatch({ type: "navigate", view: { name: "mission", id: mission.id } })}
          >
            Details →
          </button>
        </div>
      </footer>
    </motion.section>
  );
}
