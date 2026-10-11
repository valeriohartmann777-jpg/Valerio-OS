"""ULTRON HQ (JARVIS 2.0): robots show only what persisted work says (acceptance 10-15)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from jarvis.core.trace import TraceContext
from jarvis.runtime import Runtime
from jarvis.team import hq
from jarvis.team.hq import BY_ID, ROBOTS, project
from jarvis.util import utcnow

NOW = utcnow()


def work(
    agent: str, state: str, room: str = "workshop", minutes_ago: float | None = None
) -> dict[str, Any]:
    finished = (
        (NOW - timedelta(minutes=minutes_ago)).isoformat() if minutes_ago is not None else None
    )
    return hq._work(
        source="ultron",
        agent=agent,
        state=state,
        room=room,
        mission_id="m1",
        mission_title="Mission 001 · x",
        task_id=f"t-{agent}-{state}",
        title=f"{agent} {state}",
        finished_at=finished,
    )


def robot(agent_id: str, items: list[dict[str, Any]], *, ai: bool = True) -> dict[str, Any]:
    return project(BY_ID[agent_id], items, ai_ready=ai, ai_reason="No AI route", now=NOW)


def test_nine_original_robots_with_roles_colours_and_rooms() -> None:
    names = [r.name for r in ROBOTS]
    assert names == [
        "AXIOM",
        "FORGE",
        "ATLAS",
        "PRISM",
        "CIPHER",
        "SENTINEL",
        "OPERATOR",
        "ARCHIVE",
        "VECTOR",
    ]
    assert len({r.color for r in ROBOTS}) == 9
    assert all(r.home_room in hq.ROOM_IDS for r in ROBOTS)
    assert [r.id for r in hq.ROOMS] == ["homebase", "workshop", "quantlab", "research", "academy"]


def test_idle_robots_dock_at_the_homebase_and_nothing_is_invented() -> None:
    entry = robot("forge", [])
    assert (entry["state"], entry["room"], entry["work"]) == ("idle", "homebase", [])


def test_offline_is_honest_about_why() -> None:
    assert robot("forge", [], ai=False)["state"] == "offline"
    assert robot("forge", [], ai=False)["reason"] == "No AI route"
    assert robot("vector", [], ai=False)["state"] == "idle"  # VECTOR's work needs no model
    prism = robot("prism", [])
    assert prism["state"] == "offline" and "not built" in prism["reason"]


def test_working_robots_move_to_the_room_of_their_task() -> None:
    entry = robot("forge", [work("forge", "working")])
    assert (entry["state"], entry["room"]) == ("working", "workshop")
    sentinel = robot("sentinel", hq.from_ultron([_ul_row("sentinel", "VERIFYING")]))
    assert (sentinel["state"], sentinel["room"]) == ("working", "academy")


def test_the_most_urgent_state_wins() -> None:
    items = [
        work("forge", "failed", minutes_ago=1),
        work("forge", "working"),
        work("forge", "reserved"),
    ]
    assert robot("forge", items)["state"] == "working"
    items = [work("forge", "reserved"), work("forge", "waiting_approval")]
    entry = robot("forge", items)
    assert (entry["state"], entry["room"]) == ("waiting_approval", "workshop")


def test_finished_work_shows_briefly_then_the_robot_is_docked() -> None:
    done = robot("forge", [work("forge", "complete", minutes_ago=2)])
    assert (done["state"], done["room"]) == ("complete", "homebase")
    failed = robot("forge", [work("forge", "failed", minutes_ago=2)])
    assert (failed["state"], failed["room"]) == ("failed", "workshop")
    old = robot("forge", [work("forge", "complete", minutes_ago=60)])
    assert old["state"] == "idle"


def test_standby_tasks_wait_at_the_dock_with_their_mission() -> None:
    entry = robot("axiom", [work("axiom", "reserved")])
    assert (entry["state"], entry["room"]) == ("reserved", "homebase")
    assert entry["work"][0]["mission_title"] == "Mission 001 · x"


def test_research_mission_states_map_to_the_quantlab_room() -> None:
    rows = [
        _qm_row("vector", "WAITING", "WAITING_APPROVAL"),
        _qm_row("cipher", "RUNNING", "RUNNING"),
        _qm_row("atlas", "INTERRUPTED", "RUNNING"),
    ]
    items = hq.from_research(rows)
    assert (robot("vector", items)["state"], robot("vector", items)["room"]) == (
        "waiting_approval",
        "quantlab",
    )
    assert (robot("cipher", items)["state"], robot("cipher", items)["room"]) == (
        "working",
        "quantlab",
    )
    assert robot("atlas", items)["state"] == "blocked"


def test_operator_follows_the_desktop_agent() -> None:
    assert hq.from_desktop({"status": "idle"}) == []
    (item,) = hq.from_desktop(
        {"status": "active", "current_task": "Launch Notepad", "mission_id": "x"}
    )
    entry = robot("operator", [item])
    assert (entry["state"], entry["room"], entry["work"][0]["title"]) == (
        "working",
        "workshop",
        "Launch Notepad",
    )


async def test_snapshot_reads_real_tasks_from_the_database(runtime: Runtime) -> None:
    snap = await runtime.team_hq.snapshot()
    assert len(snap["robots"]) == 9 and len(snap["rooms"]) == 5
    homebase = next(r for r in snap["rooms"] if r["id"] == "homebase")
    assert len(homebase["robots"]) == 9  # nothing runs → everyone is docked
    states = {r["id"]: r["state"] for r in snap["robots"]}
    assert states["forge"] == "offline" and states["vector"] == "idle"  # no AI route in tests
    assert "No AI route" in snap["ai"]["reason"]

    now = utcnow().isoformat()
    await runtime.db.execute(
        "INSERT INTO ul_missions (id, number, title, goal, project_kind, state, budget_usd, "
        "workspace, created_at, updated_at) VALUES ('m1', 1, 'Slugify', 'g', 'new', 'RUNNING', 3, "
        "'w', ?, ?)",
        (now, now),
    )
    for key, owner, state in (
        ("spec", "axiom", "COMPLETE"),
        ("code", "forge", "RUNNING"),
        ("review", "sentinel", "DRAFT"),
    ):
        await runtime.db.execute(
            "INSERT INTO ul_tasks (id, mission_id, key, title, owner, depends_on, contract, state, "
            "max_attempts, finished_at) VALUES (?, 'm1', ?, ?, ?, '[]', ?, ?, 3, ?)",
            (
                f"t-{key}",
                key,
                f"Task {key}",
                owner,
                json.dumps({}),
                state,
                now if state == "COMPLETE" else None,
            ),
        )
    await runtime.agents.start("operator", "Launch Notepad", TraceContext.new())
    snap = await runtime.team_hq.snapshot()
    by_id = {r["id"]: r for r in snap["robots"]}
    assert (by_id["forge"]["state"], by_id["forge"]["room"]) == ("working", "workshop")
    assert by_id["forge"]["work"][0]["mission_title"] == "Mission 001 · Slugify"
    assert (by_id["axiom"]["state"], by_id["axiom"]["room"]) == ("complete", "homebase")
    assert by_id["sentinel"]["state"] == "reserved"
    assert (by_id["operator"]["state"], by_id["operator"]["room"]) == ("working", "workshop")
    workshop = next(r for r in snap["rooms"] if r["id"] == "workshop")
    assert set(workshop["robots"]) == {"forge", "operator"} and workshop["open_missions"] >= 1
    assert by_id["axiom"]["last_output"]["title"] == "Task spec"

    await runtime.db.execute("UPDATE ul_missions SET state = 'CANCELLED' WHERE id = 'm1'")
    await runtime.db.execute("UPDATE ul_tasks SET state = 'CANCELLED' WHERE state != 'COMPLETE'")
    snap = await runtime.team_hq.snapshot()
    assert {r["id"]: r["state"] for r in snap["robots"]}["forge"] == "offline"  # back, honestly


def _ul_row(owner: str, state: str) -> dict[str, Any]:
    return {
        "id": f"t-{owner}",
        "mission_id": "m1",
        "owner": owner,
        "state": state,
        "title": "Review",
        "finished_at": None,
        "mission_number": 1,
        "mission_title": "x",
    }


def _qm_row(agent: str, state: str, mission_state: str) -> dict[str, Any]:
    return {
        "id": f"q-{agent}",
        "mission_id": "q1",
        "agent": agent,
        "state": state,
        "objective": "Research",
        "finished_at": None,
        "mission_state": mission_state,
        "mission_title": "ORB idea",
        "error": None,
    }
