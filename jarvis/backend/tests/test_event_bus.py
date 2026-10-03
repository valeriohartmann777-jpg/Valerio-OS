from __future__ import annotations

import asyncio

from jarvis.events.bus import EventBus, matches
from jarvis.events.types import Event, EventType, Severity


def test_pattern_matching() -> None:
    assert matches("*", "mission.created")
    assert matches("mission.created", "mission.created")
    assert matches("mission.*", "mission.updated")
    assert not matches("mission.*", "agent.started")
    assert not matches("mission", "mission.created")


async def test_handlers_receive_matching_events_in_order() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def on_mission(event: Event) -> None:
        seen.append(f"mission:{event.message}")

    async def on_all(event: Event) -> None:
        seen.append(f"all:{event.message}")

    bus.subscribe("mission.*", on_mission)
    bus.subscribe("*", on_all)
    await bus.emit(EventType.MISSION_CREATED, message="a")
    await bus.emit(EventType.TOOL_STARTED, message="b")
    assert seen == ["mission:a", "all:a", "all:b"]


async def test_failing_handler_does_not_break_others() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def broken(event: Event) -> None:
        raise RuntimeError("boom")

    async def healthy(event: Event) -> None:
        seen.append(event.message)

    bus.subscribe("*", broken)
    bus.subscribe("*", healthy)
    await bus.emit(EventType.JARVIS_MESSAGE, message="still delivered")
    assert seen == ["still delivered"]


async def test_unsubscribe() -> None:
    bus = EventBus()
    seen: list[Event] = []

    async def handler(event: Event) -> None:
        seen.append(event)

    unsubscribe = bus.subscribe("*", handler)
    await bus.emit(EventType.JARVIS_MESSAGE)
    unsubscribe()
    await bus.emit(EventType.JARVIS_MESSAGE)
    assert len(seen) == 1


async def test_stream_delivers_and_drops_oldest_when_full() -> None:
    bus = EventBus()
    stream = bus.open_stream(maxsize=2)
    for message in ("1", "2", "3"):
        await bus.emit(EventType.JARVIS_MESSAGE, message=message)
    assert stream.dropped == 1
    first = await asyncio.wait_for(stream.get(), 1)
    second = await asyncio.wait_for(stream.get(), 1)
    assert [first.message, second.message] == ["2", "3"]
    stream.close()
    assert bus.stream_count == 0


async def test_history_filters_by_severity_and_type() -> None:
    bus = EventBus(history_size=3)
    await bus.emit(EventType.JARVIS_STATE_CHANGED, severity=Severity.DEBUG)
    await bus.emit(EventType.CONTEXT_UPDATED, severity=Severity.INFO)
    await bus.emit(EventType.JARVIS_MESSAGE, severity=Severity.IMPORTANT)
    await bus.emit(EventType.TOOL_FAILED, severity=Severity.ERROR)
    history = bus.history(min_severity=Severity.INFO, exclude=[EventType.CONTEXT_UPDATED])
    assert [e.type for e in history] == [EventType.JARVIS_MESSAGE, EventType.TOOL_FAILED]
