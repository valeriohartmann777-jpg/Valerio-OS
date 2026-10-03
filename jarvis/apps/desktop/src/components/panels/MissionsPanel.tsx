import type { Mission } from "@jarvis/protocol";

import { missionNumber, missionStatusLabel } from "../../lib/format";
import { isTerminal } from "../../store/reducer";
import { dispatch, useJarvis } from "../../store/store";
import { Empty, Section, StatusDot, cx, textTone } from "../ui/primitives";
import { MISSION_TONE } from "../ui/status";

const VISIBLE = 7;

export function MissionsPanel() {
  const missions = useJarvis((s) => s.missions);
  const active = missions.filter((m) => !isTerminal(m));
  const done = missions.filter(isTerminal).slice(0, Math.max(0, VISIBLE - active.length));
  return (
    <Section
      title="Missions"
      aside={<span className="font-mono text-2xs text-fg-faint tabular">{active.length} active</span>}
    >
      {missions.length === 0 ? (
        <Empty>No missions yet.</Empty>
      ) : (
        <ul className="-mx-2 space-y-0.5" data-testid="missions-panel">
          {[...active, ...done].map((mission) => (
            <MissionRow key={mission.id} mission={mission} />
          ))}
        </ul>
      )}
    </Section>
  );
}

function MissionRow({ mission }: { mission: Mission }) {
  const tone = MISSION_TONE[mission.status];
  const live = !isTerminal(mission);
  return (
    <li>
      <button
        type="button"
        onClick={() => dispatch({ type: "navigate", view: { name: "mission", id: mission.id } })}
        className="grid w-full grid-cols-[30px_minmax(0,1fr)_auto] items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-white/[0.03]"
      >
        <span className="font-mono text-xs text-fg-faint tabular">{missionNumber(mission.number)}</span>
        <span className={cx("truncate text-[13px]", live ? "text-fg" : "text-fg-muted")}>{mission.title}</span>
        <span className={cx("flex items-center gap-1.5 text-2xs font-medium tracking-wide uppercase", textTone(tone))}>
          <StatusDot tone={tone} live={live} />
          {missionStatusLabel(mission.status, true)}
        </span>
      </button>
    </li>
  );
}
