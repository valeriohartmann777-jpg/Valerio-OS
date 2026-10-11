"""ULTRON HQ: where each robot is and what it really does (JARVIS 2.0, brief §7).

The HQ is a *projection*. Nothing here runs work or grants anything: a robot's state and room
are derived from persisted tasks — ULTRON software missions (``ul_tasks``), QuantLab research
missions (``qm_tasks``) and JARVIS's desktop agent registry (OPERATOR). No heartbeat is
invented; a robot without a task is docked at the Homebase.

States (most urgent first): working · waiting_approval · blocked · failed · reserved ·
complete · idle · offline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from jarvis.util import utcnow

RECENT = timedelta(minutes=15)  # how long a finished task still shows (✓ done / ✕ failed)

STATES = (
    "working",
    "waiting_approval",
    "blocked",
    "failed",
    "reserved",
    "complete",
    "idle",
    "offline",
)
STATE_LABEL = {
    "working": "Working",
    "waiting_approval": "Waiting for you",
    "blocked": "Blocked",
    "failed": "Failed",
    "reserved": "On standby",
    "complete": "Done · verified",
    "idle": "Docked",
    "offline": "Offline",
}


@dataclass(frozen=True)
class Room:
    id: str
    name: str
    description: str


ROOMS: tuple[Room, ...] = (
    Room("homebase", "Homebase", "The docking bay: robots rest here whenever they have no task."),
    Room(
        "workshop",
        "Workshop",
        "AXIOM, FORGE, PRISM and VECTOR build software: specs, code and tests in isolated "
        "git worktrees.",
    ),
    Room(
        "quantlab",
        "ULTRON QuantLab",
        "The team research lab. Same QuantLab engine and Data Vault as your Solo QuantLab, "
        "with its own workspace and only the access you grant.",
    ),
    Room(
        "research",
        "Research room",
        "ATLAS and ARCHIVE: sources, quoted claims, knowledge and lineage.",
    ),
    Room(
        "academy",
        "Academy · Test chamber",
        "SENTINEL verifies work independently; reviews, regressions and evaluations.",
    ),
)
ROOM_IDS = tuple(r.id for r in ROOMS)


@dataclass(frozen=True)
class Robot:
    id: str
    name: str
    specialization: str
    color: str
    home_room: str  # where its kind of work happens
    needs_ai: bool  # every ability needs Claude → offline without an AI route
    available: bool = True  # False: planned, not built yet
    summary: str = ""


ROBOTS: tuple[Robot, ...] = (
    Robot("axiom", "AXIOM", "Architecture", "#54cfff", "workshop", True,
          summary="Writes specs, interfaces and decision records for software missions."),
    Robot("forge", "FORGE", "Software development", "#ff9d68", "workshop", True,
          summary="Writes code and tests in its own git worktree; never pushes or merges."),
    Robot("atlas", "ATLAS", "Research", "#b29aff", "research", True,
          summary="Extracts quoted claims from sources in QuantLab research missions."),
    Robot("prism", "PRISM", "Product & UI design", "#ff82c7", "workshop", True, available=False,
          summary="Planned: design specs and UI code. Not built yet."),
    Robot("cipher", "CIPHER", "Quant, data & statistics", "#42d7b6", "quantlab", True,
          summary="Designs validation protocols and proposes strategy variants."),
    Robot("sentinel", "SENTINEL", "Verification, risk & security", "#ffdf59", "academy", False,
          summary="Reviews software independently and audits every QuantLab run."),
    Robot("operator", "OPERATOR", "Computer & browser automation", "#88bbff", "workshop", False,
          summary="JARVIS's desktop agent: opens and controls apps with your approval."),
    Robot("archive", "ARCHIVE", "Memory & knowledge", "#c5a6ff", "research", False,
          summary="Keeps lineage and the append-only trial ledger of research missions."),
    Robot("vector", "VECTOR", "Infrastructure & data", "#a7f0a4", "workshop", False,
          summary="Gets data quotes and builds verified datasets in research missions."),
)  # fmt: skip
ROBOT_IDS = tuple(r.id for r in ROBOTS)
BY_ID = {r.id: r for r in ROBOTS}

# ULTRON task states → HQ states (CANCELLED tasks are dropped).
_UL = {
    "RUNNING": "working",
    "VERIFYING": "working",
    "WAITING_APPROVAL": "waiting_approval",
    "BLOCKED": "blocked",
    "FAILED": "failed",
    "READY": "reserved",
    "DRAFT": "reserved",
    "RETRYING": "reserved",
    "COMPLETE": "complete",
}
# QuantLab research-mission task states.
_QM = {
    "RUNNING": "working",
    "BLOCKED": "blocked",
    "INTERRUPTED": "blocked",
    "FAILED": "failed",
    "COMPLETE": "complete",
    "VETOED": "complete",  # SENTINEL finished its audit — with a veto
}
_RANK = {state: i for i, state in enumerate(STATES)}


def _work(
    *,
    source: str,
    agent: str,
    state: str,
    room: str,
    mission_id: str,
    mission_title: str,
    task_id: str,
    title: str,
    finished_at: str | None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "agent": agent,
        "state": state,
        "room": room,
        "mission_id": mission_id,
        "mission_title": mission_title,
        "task_id": task_id,
        "title": title,
        "finished_at": finished_at,
        "detail": detail,
    }


def from_ultron(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        state = _UL.get(r["state"])
        if state is None:
            continue
        # SENTINEL's reviews happen in the test chamber; all other software work in the Workshop.
        room = "academy" if r["owner"] == "sentinel" else "workshop"
        out.append(
            _work(
                source="ultron",
                agent=r["owner"],
                state=state,
                room=room,
                mission_id=r["mission_id"],
                mission_title=f"Mission {int(r['mission_number']):03d} · {r['mission_title']}",
                task_id=r["id"],
                title=r["title"],
                finished_at=r["finished_at"],
            )
        )
    return out


def from_research(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        state = _QM.get(r["state"])
        if r["state"] == "WAITING":
            waiting_for_owner = r["mission_state"] in ("WAITING_APPROVAL", "WAITING_USER")
            state = "waiting_approval" if waiting_for_owner else "reserved"
        if state is None:
            continue
        out.append(
            _work(
                source="quantlab",
                agent=r["agent"],
                state=state,
                room="quantlab",
                mission_id=r["mission_id"],
                mission_title=f"QuantLab · {r['mission_title']}",
                task_id=r["id"],
                title=r["objective"],
                finished_at=r["finished_at"],
                detail=r.get("error"),
            )
        )
    return out


def from_desktop(agent: dict[str, Any] | None) -> list[dict[str, Any]]:
    """OPERATOR's live state from JARVIS's desktop agent registry (in memory, real)."""
    if not agent or agent.get("status") not in ("active", "waiting"):
        return []
    return [
        _work(
            source="desktop",
            agent="operator",
            state="working" if agent["status"] == "active" else "waiting_approval",
            room="workshop",
            mission_id=str(agent.get("mission_id") or ""),
            mission_title="Desktop task",
            task_id="",
            title=str(agent.get("current_task") or "Desktop action"),
            finished_at=None,
        )
    ]


def project(
    robot: Robot,
    work: list[dict[str, Any]],
    *,
    ai_ready: bool,
    ai_reason: str,
    now: datetime,
) -> dict[str, Any]:
    """One robot's state and room from its real work. Pure: tested on its own."""
    mine = []
    for w in work:
        if w["agent"] != robot.id:
            continue
        if w["state"] in ("complete", "failed") and w["finished_at"]:
            try:
                if now - datetime.fromisoformat(w["finished_at"]) > RECENT:
                    continue
            except ValueError:
                continue
        mine.append(w)
    mine.sort(key=lambda w: _RANK[w["state"]])
    if mine:
        top = mine[0]
        state = top["state"]
        room = (
            top["room"]
            if state in ("working", "waiting_approval", "blocked", "failed")
            else "homebase"
        )
        reason = top.get("detail")
    elif not robot.available:
        state, room, reason = "offline", "homebase", "Planned — not built yet."
    elif robot.needs_ai and not ai_ready:
        state, room, reason = "offline", "homebase", ai_reason
    else:
        state, room, reason = "idle", "homebase", None
    return {
        "id": robot.id,
        "name": robot.name,
        "specialization": robot.specialization,
        "summary": robot.summary,
        "color": robot.color,
        "home_room": robot.home_room,
        "available": robot.available,
        "state": state,
        "state_label": STATE_LABEL[state],
        "room": room,
        "reason": reason,
        "work": mine,
    }


@dataclass
class HQSources:
    ultron_tasks: Callable[[str], Awaitable[list[dict[str, Any]]]]
    research_tasks: Callable[[str], Awaitable[list[dict[str, Any]]]]
    ultron_last: Callable[[], Awaitable[dict[str, dict[str, Any]]]]
    research_last: Callable[[], Awaitable[dict[str, dict[str, Any]]]]
    ultron_agents: Callable[[], Awaitable[list[dict[str, Any]]]]
    desktop_operator: Callable[[], dict[str, Any] | None]
    ai: Callable[[], tuple[bool, str, str]]  # (ready, route, reason)
    jarvis_planning: Callable[[], list[str]]


class TeamHQ:
    def __init__(self, sources: HQSources) -> None:
        self._src = sources

    async def snapshot(self) -> dict[str, Any]:
        now = utcnow()
        since = (now - RECENT).isoformat()
        ultron_rows = await self._src.ultron_tasks(since)
        research_rows = await self._src.research_tasks(since)
        work = (
            from_ultron(ultron_rows)
            + from_research(research_rows)
            + from_desktop(self._src.desktop_operator())
        )
        ai_ready, route, ai_reason = self._src.ai()
        last = await self._last_outputs()
        profiles = {a["id"]: a for a in await self._src.ultron_agents()}
        robots = []
        for robot in ROBOTS:
            entry = project(robot, work, ai_ready=ai_ready, ai_reason=ai_reason, now=now)
            profile = profiles.get(robot.id, {})
            entry["last_output"] = last.get(robot.id)
            entry["spent_usd"] = profile.get("spent_usd", 0.0)
            entry["tools"] = profile.get("tools", [])
            entry["writes"] = profile.get("writes")
            entry["must_not"] = profile.get("must_not")
            entry["assign"] = _assign(robot, ai_ready, ai_reason)
            robots.append(entry)
        missions: dict[str, set[str]] = {room: set() for room in ROOM_IDS}
        for w in work:
            if w["state"] not in ("complete",) and w["mission_id"]:
                missions[w["room"]].add(w["mission_id"])
        rooms = [
            {
                "id": room.id,
                "name": room.name,
                "description": room.description,
                "robots": [r["id"] for r in robots if r["room"] == room.id],
                "open_missions": len(missions[room.id]),
            }
            for room in ROOMS
        ]
        planning = self._src.jarvis_planning()
        return {
            "generated_at": now.isoformat(),
            "robots": robots,
            "rooms": rooms,
            "ai": {"ready": ai_ready, "route": route, "reason": ai_reason},
            "jarvis": {
                "state": "planning" if planning else "idle",
                "planning": planning,
                "label": "Planning a mission" if planning else "Orchestrator · ready",
            },
            "evidence": "States come from persisted tasks and JARVIS's desktop agent; "
            "no heartbeat is simulated.",
        }

    async def _last_outputs(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for agent, row in (await self._src.ultron_last()).items():
            out[agent] = {
                "title": row["title"],
                "mission_title": row["mission_title"],
                "finished_at": row["finished_at"],
                "source": "ultron",
            }
        for agent, row in (await self._src.research_last()).items():
            mine = out.get(agent)
            if mine is None or (row["finished_at"] or "") > (mine["finished_at"] or ""):
                out[agent] = {
                    "title": row["objective"],
                    "mission_title": f"QuantLab · {row['mission_title']}",
                    "finished_at": row["finished_at"],
                    "source": "quantlab",
                }
        return out


def _assign(robot: Robot, ai_ready: bool, ai_reason: str) -> dict[str, Any]:
    """How a task reaches this robot — honestly, including when it can't."""
    if not robot.available:
        return {"possible": False, "how": None, "reason": "PRISM is planned and not built yet."}
    if robot.id in ("axiom", "forge", "sentinel"):
        how = "Start a Workshop mission: JARVIS plans it and assigns the robots it needs."
        return {
            "possible": ai_ready,
            "how": how,
            "room": "workshop",
            "reason": None if ai_ready else ai_reason,
        }
    if robot.id in ("cipher", "atlas"):
        how = "Share a strategy into the ULTRON QuantLab and ask for an analysis there."
        return {
            "possible": ai_ready,
            "how": how,
            "room": "quantlab",
            "reason": None if ai_ready else ai_reason,
        }
    if robot.id == "operator":
        return {
            "possible": True,
            "how": "Tell JARVIS on Home, e.g. “Open Notepad”.",
            "room": None,
            "reason": None,
        }
    return {
        "possible": False,
        "how": None,
        "reason": f"{robot.name} works inside research missions automatically; "
        "it takes no direct tasks.",
    }
