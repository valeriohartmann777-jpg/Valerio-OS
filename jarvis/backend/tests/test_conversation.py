"""The chat history survives restarts and pages backwards."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.events.store import EventStore
from jarvis.events.types import Event, EventType
from jarvis.llm.registry import ModelSet
from jarvis.runtime import Runtime
from jarvis.storage.database import Database
from jarvis.util import utcnow
from tests.conftest import make_settings
from tests.fakes import ScriptedChatModel, say


def test_conversation_is_kept_across_restarts_and_pages_back(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(runtime=Runtime(settings))) as client:
        for count, text in enumerate(("hello", "system status", "what's the active window")):
            client.post("/chat", json={"text": text})
            for _ in range(500):  # one exchange at a time
                if len(client.get("/conversation").json()) == 2 * (count + 1):
                    break

    with TestClient(create_app(runtime=Runtime(settings))) as client:  # a restart
        history = client.get("/conversation").json()
        kinds = [(e["type"], e["payload"].get("text", "")[:6]) for e in history]
        assert [k for k, _ in kinds] == ["command.received", "jarvis.message"] * 3
        assert history[0]["payload"]["text"] == "hello"
        assert history[1]["payload"]["text"] == "Online. Everything is nominal."
        assert history[0]["payload"]["source"] == "text"

        last_two = client.get("/conversation", params={"limit": 2}).json()
        assert [e["id"] for e in last_two] == [e["id"] for e in history[-2:]]
        earlier = client.get(
            "/conversation", params={"limit": 10, "before": last_two[0]["timestamp"]}
        ).json()
        assert [e["id"] for e in earlier] == [e["id"] for e in history[:-2]]
        assert client.get("/conversation", params={"before": "yesterday"}).status_code == 422


async def test_after_a_restart_jarvis_still_knows_the_conversation(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    first = Runtime(
        settings,
        models=ModelSet(fast=ScriptedChatModel([say("Noted.")], "claude-fast"), reasoning=None),
    )
    await first.start()
    try:
        for text in ("hello", "my setup is NQ at the open"):
            await first.core.handle(text)
            await first.core.wait_idle()
    finally:
        await first.stop()

    fast = ScriptedChatModel([say("You trade NQ at the open.")], "claude-fast")
    again = Runtime(settings, models=ModelSet(fast=fast, reasoning=None))
    await again.start()
    try:
        assert again.brain.history_size == 2
        await again.core.handle("what did I say about my setup?")
        await again.core.wait_idle()
        [request] = fast.calls
        said = [m["content"] for m in request["messages"][:-1]]
        assert said[0] == [{"type": "text", "text": "hello"}]
        assert said[1] == [{"type": "text", "text": "Online. Everything is nominal."}]
        assert said[2] == [{"type": "text", "text": "my setup is NQ at the open"}]
        assert said[3] == [{"type": "text", "text": "Noted."}]
    finally:
        await again.stop()


async def test_restored_exchanges_pair_up_and_old_activity_is_pruned(tmp_path: Path) -> None:
    db = Database(tmp_path / "jarvis.db")
    await db.connect()
    store = EventStore(db)
    try:

        async def put(
            type_: EventType, hours_ago: float, trace: str | None, **payload: Any
        ) -> None:
            moment = utcnow() - timedelta(hours=hours_ago)
            await store._on_event(
                Event(type=type_, timestamp=moment, trace_id=trace, payload=payload)
            )

        await put(EventType.COMMAND_RECEIVED, 30, "a", text="yesterday's question")
        await put(EventType.JARVIS_MESSAGE, 30, "a", text="yesterday's answer")
        await put(EventType.COMMAND_RECEIVED, 2, "b", text="wie steht NQ?")
        await put(EventType.MISSION_CREATED, 2, "b")
        await put(EventType.JARVIS_MESSAGE, 2, "b", text="NQ steht bei 21.400.")
        await put(EventType.JARVIS_MESSAGE, 1, None, text="Guten Morgen …", kind="briefing")
        await put(EventType.JARVIS_MESSAGE, 1, "c", text="an answer without its question")
        await put(EventType.MISSION_CREATED, 24 * 40, "d")
        await put(EventType.COMMAND_RECEIVED, 24 * 40, "d", text="long ago")

        since = (utcnow() - timedelta(hours=24)).isoformat()
        assert await store.exchanges(since, 12) == [
            ("wie steht NQ?", "NQ steht bei 21.400."),
            ("(morning briefing requested)", "Guten Morgen …"),
        ]
        assert await store.exchanges(since, 1) == [
            ("(morning briefing requested)", "Guten Morgen …"),
        ]

        removed = await store.prune((utcnow() - timedelta(days=30)).isoformat())
        assert removed == 1  # the old mission event; the old command stays
        texts = [e.payload.get("text") for e in await store.conversation()]
        assert "long ago" in texts
    finally:
        await db.close()
