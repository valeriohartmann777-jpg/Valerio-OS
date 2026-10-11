import type { HqRobot, HqRoom, HqRoomId, HqSnapshot } from "@jarvis/protocol";

import { Icon, type IconName } from "../../ui/icons";
import { cx } from "../../ui/primitives";
import { StatusPill, type PillTone } from "../../ui/shell";
import { RobotFigure } from "./Robot";

/**
 * The HQ world: five connected rooms with the robots where the backend says they are.
 * Positions inside a room are scene layout only — the room itself comes from real work,
 * and standing in a room grants nothing (brief §10.3).
 */

export const ROOM_LOOK: Record<HqRoomId, { no: string; icon: IconName; color: string; tint: string; tagline: string }> = {
  homebase: { no: "01", icon: "moon", color: "#b29aff", tint: "from-[#3a2d63] to-[#1f2147]", tagline: "Docking · rest zone" },
  workshop: { no: "02", icon: "cog", color: "#54cfff", tint: "from-[#18405a] to-[#16263f]", tagline: "Code · systems" },
  quantlab: { no: "03", icon: "candles", color: "#42d7b6", tint: "from-[#164a45] to-[#14283a]", tagline: "Shared trading lab" },
  research: { no: "04", icon: "search", color: "#b29aff", tint: "from-[#33325f] to-[#1b2343]", tagline: "Knowledge · sources" },
  academy: { no: "05", icon: "target", color: "#ff9d68", tint: "from-[#5a3238] to-[#2a2236]", tagline: "Tests · reviews" },
};

export const STATE_PILL: Record<HqRobot["state"], PillTone> = {
  working: "info",
  waiting_approval: "warning",
  blocked: "warning",
  failed: "danger",
  reserved: "muted",
  complete: "success",
  idle: "muted",
  offline: "muted",
};

export function World({
  hq,
  room,
  robot,
  onRoom,
  onRobot,
}: {
  hq: HqSnapshot;
  room: HqRoomId;
  robot: string | null;
  onRoom: (room: HqRoomId) => void;
  onRobot: (id: string) => void;
}) {
  const byId = new Map(hq.robots.map((r) => [r.id, r]));
  const roomOf = (id: HqRoomId) => hq.rooms.find((r) => r.id === id)!;
  return (
    <div className="grid h-full min-h-[380px] grid-cols-1 gap-3 md:grid-cols-[1.15fr_1fr_1fr] md:grid-rows-2" data-testid="hq-world">
      {(["homebase", "workshop", "quantlab", "research", "academy"] as HqRoomId[]).map((id) => (
        <RoomTile
          key={id}
          room={roomOf(id)}
          robots={roomOf(id).robots.map((r) => byId.get(r)!).filter(Boolean)}
          selected={room === id}
          selectedRobot={robot}
          onSelect={() => onRoom(id)}
          onRobot={onRobot}
          className={id === "homebase" ? "md:row-span-2" : undefined}
        />
      ))}
    </div>
  );
}

function RoomTile({
  room,
  robots,
  selected,
  selectedRobot,
  onSelect,
  onRobot,
  className,
}: {
  room: HqRoom;
  robots: HqRobot[];
  selected: boolean;
  selectedRobot: string | null;
  onSelect: () => void;
  onRobot: (id: string) => void;
  className?: string;
}) {
  const look = ROOM_LOOK[room.id];
  const home = room.id === "homebase";
  return (
    <div
      className={cx(
        "relative flex min-h-[170px] flex-col overflow-hidden rounded-2xl border-2 border-b-[6px] bg-gradient-to-br p-3.5 transition-transform",
        look.tint,
        selected ? "border-ultron shadow-[0_0_0_2px_rgb(255_180_93/0.35)]" : "border-[#4a6488] hover:-translate-y-0.5",
        className,
      )}
      style={{ borderTopColor: selected ? undefined : look.color }}
      onClick={onSelect}
      data-testid={`hq-room-${room.id}`}
      data-selected={selected ? "yes" : "no"}
    >
      {/* floor grid: a quiet hint of depth, not a pattern everywhere */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-1/2 opacity-25"
        style={{
          backgroundImage:
            "linear-gradient(rgb(255 255 255 / 0.18) 1px, transparent 1px), linear-gradient(90deg, rgb(255 255 255 / 0.18) 1px, transparent 1px)",
          backgroundSize: "26px 26px",
          transform: "perspective(240px) rotateX(58deg)",
          transformOrigin: "bottom",
        }}
      />
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        aria-pressed={selected}
        className="relative z-10 self-start text-left"
        data-testid={`hq-room-open-${room.id}`}
      >
        <span className="block text-[14px] font-black tracking-[0.04em] text-fg uppercase">
          {look.no} / {room.name}
        </span>
        <span className="block text-[12px] text-fg-muted">
          {look.tagline}
          {room.open_missions > 0 && ` · ${room.open_missions} open mission${room.open_missions === 1 ? "" : "s"}`}
        </span>
      </button>
      <Icon name={look.icon} size={30} className="pointer-events-none absolute top-3.5 right-3.5 opacity-40" style={{ color: look.color }} />
      <div className={cx("relative z-10 mt-auto flex flex-wrap items-end gap-x-2 gap-y-1 pt-3", home && "pr-10")}>
        {robots.length === 0 && !home && <span className="text-[12px] text-fg-muted">Empty — no robot works here right now.</span>}
        {robots.map((robot) => (
          <RobotButton key={robot.id} robot={robot} selected={selectedRobot === robot.id} onClick={() => onRobot(robot.id)} docked={home} />
        ))}
      </div>
    </div>
  );
}

export function RobotButton({ robot, selected, onClick, docked, size = 52 }: { robot: HqRobot; selected: boolean; onClick: () => void; docked?: boolean; size?: number }) {
  const task = robot.work[0];
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      aria-label={`${robot.name} — ${robot.specialization} — ${robot.state_label}${task ? `: ${task.title}` : ""}`}
      title={`${robot.name} · ${robot.state_label}${task ? ` — ${task.title}` : robot.reason ? ` — ${robot.reason}` : ""}`}
      data-testid={`hq-robot-${robot.id}`}
      data-state={robot.state}
      data-room={robot.room}
      className={cx(
        "flex flex-col items-center rounded-xl px-1 pt-1 pb-0.5 transition-colors",
        selected ? "bg-white/15 ring-2 ring-ultron" : "hover:bg-white/10",
      )}
    >
      <span className="relative">
        <RobotFigure id={robot.id} color={robot.color} state={robot.state} size={size} />
        {docked && <span aria-hidden className="absolute -bottom-0.5 left-1/2 h-1.5 w-[70%] -translate-x-1/2 rounded-[50%] bg-[#7f6bd6]/50 blur-[1px]" />}
      </span>
      <span className="mt-0.5 text-[11px] font-black tracking-[0.04em] text-fg [text-shadow:0_1px_2px_#000]">{robot.name}</span>
    </button>
  );
}

/** The crew bar: every robot with its name, role and real state — the accessible list view. */
export function Crew({ robots, selected, onRobot }: { robots: HqRobot[]; selected: string | null; onRobot: (id: string) => void }) {
  return (
    <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5" data-testid="hq-crew">
      {robots.map((robot) => (
        <li key={robot.id}>
          <button
            type="button"
            onClick={() => onRobot(robot.id)}
            aria-pressed={selected === robot.id}
            data-testid={`hq-crew-${robot.id}`}
            data-state={robot.state}
            className={cx(
              "flex w-full items-center gap-2.5 rounded-xl border bg-surface-2/50 p-2 text-left transition-colors",
              selected === robot.id ? "border-ultron" : "border-hairline-strong hover:border-control",
            )}
          >
            <RobotFigure id={robot.id} color={robot.color} state={robot.state} size={34} />
            <span className="min-w-0">
              <span className="block text-[13px] font-black tracking-[0.03em] text-fg">{robot.name}</span>
              <span className="block truncate text-[12px] text-fg-muted">{robot.specialization}</span>
              <span className="mt-0.5 block">
                <StatusPill tone={STATE_PILL[robot.state]}>{robot.state_label}</StatusPill>
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
