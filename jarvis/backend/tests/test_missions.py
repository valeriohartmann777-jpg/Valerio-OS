from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from jarvis.core.state import JarvisState
from jarvis.events.types import EventType
from jarvis.missions.models import Mission, MissionStatus, StepStatus
from jarvis.runtime import Runtime
from tests.conftest import Recorder, make_settings


async def eventually(predicate: Callable[[], object]) -> None:
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110 - polling a plain condition in tests
            await asyncio.sleep(0.01)


async def last_mission(rt: Runtime) -> Mission:
    missions = await rt.missions.list(limit=1)
    return missions[0]


async def test_open_notepad_full_vertical_slice(runtime: Runtime, recorder: Recorder) -> None:
    """The first demo: command → route → mission → operator → tool → sentinel → reply."""
    await runtime.core.handle("Open Notepad.")
    await runtime.core.wait_idle()

    mission = await last_mission(runtime)
    assert mission.status is MissionStatus.COMPLETE
    assert mission.title == "Open Notepad"
    assert [s.status for s in mission.steps] == [StepStatus.COMPLETE, StepStatus.COMPLETE]
    assert mission.verification is not None and mission.verification.verified
    assert mission.result == "Notepad is open."
    assert mission.tools_used == ["open_application"]

    chain = [
        t
        for t in recorder.types()
        if t
        not in {
            EventType.JARVIS_STATE_CHANGED,
            EventType.MISSION_UPDATED,
            EventType.AGENT_COMPLETED,
            EventType.CONTEXT_UPDATED,
        }
    ]
    assert chain == [
        EventType.COMMAND_RECEIVED,
        EventType.INTENT_CLASSIFIED,
        EventType.MISSION_CREATED,
        EventType.AGENT_STARTED,  # operator
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.AGENT_STARTED,  # sentinel
        EventType.VERIFICATION_COMPLETED,
        EventType.JARVIS_MESSAGE,
    ]
    states = [e.payload["state"] for e in recorder.of(EventType.JARVIS_STATE_CHANGED)]
    assert states[:6] == [
        JarvisState.UNDERSTANDING,
        JarvisState.PLANNING,
        JarvisState.DELEGATING,
        JarvisState.EXECUTING,
        JarvisState.VERIFYING,
        JarvisState.COMPLETE,
    ]
    await eventually(lambda: runtime.state.state is JarvisState.DORMANT)
    message = recorder.of(EventType.JARVIS_MESSAGE)[0]
    assert message.payload["text"] == "Notepad is open."
    assert message.trace_id == mission.trace_id


async def test_compound_mission(runtime: Runtime) -> None:
    await runtime.core.handle("open notepad and calculator")
    await runtime.core.wait_idle()
    mission = await last_mission(runtime)
    assert mission.status is MissionStatus.COMPLETE
    assert len(mission.steps) == 4
    assert mission.result == "Notepad and Calculator are open."


async def test_failed_action_skips_verification(runtime: Runtime, recorder: Recorder) -> None:
    await runtime.core.handle("open blender")
    await runtime.core.wait_idle()
    mission = await last_mission(runtime)
    assert mission.status is MissionStatus.FAILED
    assert [s.status for s in mission.steps] == [StepStatus.FAILED, StepStatus.SKIPPED]
    message = recorder.of(EventType.JARVIS_MESSAGE)[-1]
    assert message.payload["text"] == "I couldn't open Blender."
    assert message.payload["error"]["suggestion"]
    assert "detail" not in message.payload["error"]


async def test_approval_flow_approve(runtime: Runtime, recorder: Recorder) -> None:
    await runtime.core.handle("open powershell")
    await eventually(lambda: runtime.permissions.pending())
    mission = await last_mission(runtime)
    assert mission.status is MissionStatus.WAITING_FOR_APPROVAL
    assert runtime.state.state is JarvisState.WAITING_FOR_APPROVAL
    assert runtime.agents.state("operator").status == "waiting"

    [request] = runtime.permissions.pending()
    assert request.action.level == 2 and request.reason == "open powershell"
    assert "arbitrary commands" in " ".join(request.action.effects)
    await runtime.permissions.approve(request.id)
    await runtime.core.wait_idle()
    mission = await last_mission(runtime)
    assert mission.status is MissionStatus.COMPLETE
    assert mission.approvals == [request.id]
    assert (await runtime.audit.recent(1))[0].approval == "approved"


async def test_approval_flow_reject_stops_mission(runtime: Runtime, recorder: Recorder) -> None:
    await runtime.core.handle("open regedit")
    await eventually(lambda: runtime.permissions.pending())
    await runtime.permissions.reject(runtime.permissions.pending()[0].id)
    await runtime.core.wait_idle()
    mission = await last_mission(runtime)
    assert mission.status is MissionStatus.STOPPED
    assert mission.steps[0].status is StepStatus.REJECTED
    assert mission.result == "Understood. I won't open Registry Editor."


async def test_pause_resume(runtime: Runtime) -> None:
    await runtime.core.handle("open notepad and calculator")
    mission = await last_mission(runtime)
    await runtime.missions.pause(mission.id)
    await eventually(lambda: runtime.state.state is JarvisState.PAUSED)
    paused = await runtime.missions.get(mission.id)
    assert paused is not None and paused.status is MissionStatus.PAUSED
    await runtime.missions.resume(mission.id)
    await runtime.core.wait_idle()
    assert (await last_mission(runtime)).status is MissionStatus.COMPLETE


async def test_stop_while_waiting_for_approval(runtime: Runtime) -> None:
    await runtime.core.handle("open powershell and notepad")
    await eventually(lambda: runtime.permissions.pending())
    mission = await last_mission(runtime)
    await runtime.missions.stop(mission.id)
    await runtime.core.wait_idle()
    mission = await last_mission(runtime)
    assert mission.status is MissionStatus.STOPPED
    assert runtime.permissions.pending() == []
    assert all(s.status in (StepStatus.REJECTED, StepStatus.SKIPPED) for s in mission.steps)
    assert mission.result == "Stopped. 0 of 2 steps were completed."


async def test_missions_persist_and_interrupted_ones_fail_on_restart(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    first = Runtime(settings)
    await first.start()
    await first.core.handle("open notepad")
    await first.core.wait_idle()
    stuck = Mission(title="Stuck", goal="x", status=MissionStatus.ACTIVE, number=99)
    await first.mission_repository.save(stuck)
    await first.stop()

    second = Runtime(settings)
    await second.start()
    try:
        missions = await second.missions.list()
        assert [m.number for m in missions] == [99, 1]
        assert missions[0].status is MissionStatus.FAILED
        assert missions[1].status is MissionStatus.COMPLETE
        assert (await second.events.recent())[0].type is EventType.SYSTEM_ONLINE
    finally:
        await second.stop()


async def test_queries_and_conversation_do_not_create_missions(
    runtime: Runtime, recorder: Recorder
) -> None:
    for text in ("what's the active window", "system status", "hello", "write me a poem"):
        await runtime.core.handle(text)
    await runtime.core.wait_idle()
    assert await runtime.missions.list() == []
    replies = [e.payload["text"] for e in recorder.of(EventType.JARVIS_MESSAGE)]
    assert replies[0] == "You're in Jarvis — “JARVIS”."
    assert replies[1].startswith("CPU at ")
    assert replies[2] == "Online. Everything is nominal."
    assert replies[3] == "I can't reason about that yet."
    offline = recorder.of(EventType.JARVIS_MESSAGE)[3].payload["error"]
    assert "ANTHROPIC_API_KEY" in offline["message"]
