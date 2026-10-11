import type { HqRobot, HqRoomId } from "@jarvis/protocol";

import { clock } from "../../../lib/format";
import { go } from "../../../lib/history";
import { Button } from "../../ui/primitives";
import { Panel, StatusPill } from "../../ui/shell";
import { RobotFigure } from "./Robot";
import { ROOM_LOOK, STATE_PILL } from "./World";

/** The robot profile: what it is, what it is really doing, what it may touch, how to task it. */
export function Inspector({ robot, onRoom }: { robot: HqRobot | null; onRoom: (room: HqRoomId) => void }) {
  if (!robot) {
    return (
      <Panel title="Agent inspector" testId="hq-inspector">
        <p className="text-[13px] text-fg-muted">Click a robot to see its role, its real task and how to give it work.</p>
      </Panel>
    );
  }
  const assign = robot.assign;
  return (
    <Panel title="Agent inspector" aside={<StatusPill tone={STATE_PILL[robot.state]} testId="hq-inspector-state">{robot.state_label}</StatusPill>} testId="hq-inspector">
      <div className="flex items-start gap-3" data-robot={robot.id}>
        <RobotFigure id={robot.id} color={robot.color} state={robot.state} size={52} />
        <div className="min-w-0">
          <p className="text-[18px] font-black tracking-[0.02em]" style={{ color: robot.color }} data-testid="hq-inspector-name">
            {robot.name}
          </p>
          <p className="text-[13px] font-semibold text-fg">{robot.specialization}</p>
          <p className="mt-1 text-[13px] leading-snug text-fg-muted">{robot.summary}</p>
        </div>
      </div>

      <dl className="mt-4 space-y-3 text-[13px]">
        <div>
          <dt className="text-[12px] font-bold tracking-[0.08em] text-fg-muted uppercase">Now</dt>
          <dd className="mt-1 text-fg" data-testid="hq-inspector-now">
            {robot.work.length > 0 ? (
              <ul className="space-y-1.5">
                {robot.work.map((w) => (
                  <li key={`${w.source}-${w.task_id}-${w.state}`}>
                    <span className="font-semibold">{w.title}</span>
                    <span className="block text-[12px] text-fg-muted">
                      {w.mission_title} · {ROOM_LOOK[w.room].no} {w.room} · {w.state.replace("_", " ")}
                      {w.finished_at ? ` · ${clock(w.finished_at)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : robot.reason ? (
              <span className="text-warning">{robot.reason}</span>
            ) : (
              <span className="text-fg-muted">Docked at the Homebase — no task.</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-[12px] font-bold tracking-[0.08em] text-fg-muted uppercase">Last verified output</dt>
          <dd className="mt-1 text-fg-muted" data-testid="hq-inspector-last">
            {robot.last_output ? (
              <>
                <span className="text-fg">{robot.last_output.title}</span> — {robot.last_output.mission_title}
                {robot.last_output.finished_at && ` · ${clock(robot.last_output.finished_at)}`}
              </>
            ) : (
              "None yet."
            )}
          </dd>
        </div>
        {(robot.writes || robot.must_not) && (
          <div>
            <dt className="text-[12px] font-bold tracking-[0.08em] text-fg-muted uppercase">Permissions</dt>
            <dd className="mt-1 text-fg-muted">
              {robot.writes && <span className="block">May write: {robot.writes}</span>}
              {robot.must_not && <span className="block">Never: {robot.must_not}</span>}
              {robot.tools.length > 0 && <span className="block">Tools: {robot.tools.join(", ")}</span>}
            </dd>
          </div>
        )}
        <div>
          <dt className="text-[12px] font-bold tracking-[0.08em] text-fg-muted uppercase">Spent</dt>
          <dd className="mt-1 text-fg-muted">${robot.spent_usd.toFixed(2)} on paid model calls (plan calls cost nothing extra)</dd>
        </div>
      </dl>

      <div className="mt-4 border-t border-hairline pt-3">
        <p className="text-[13px] text-fg-muted">{assign.how ?? assign.reason}</p>
        {assign.how && (
          <Button
            className="mt-2"
            variant="primary"
            disabled={!assign.possible}
            reason={assign.reason}
            onClick={() => (assign.room ? onRoom(assign.room) : go({ name: "home" }))}
            data-testid="hq-assign"
          >
            {assign.room === "workshop" ? "Go to the Workshop" : assign.room === "quantlab" ? "Go to the ULTRON QuantLab" : "Go to Home"}
          </Button>
        )}
      </div>
    </Panel>
  );
}
