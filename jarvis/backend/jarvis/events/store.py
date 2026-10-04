"""Persists meaningful events so the activity history survives restarts."""

from __future__ import annotations

import json
from typing import Any

from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType, Severity
from jarvis.storage.database import Database

# High-frequency telemetry is streamed live but never persisted.
_NOT_PERSISTED = {EventType.CONTEXT_UPDATED}


class EventStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def attach(self, bus: EventBus) -> None:
        bus.subscribe("*", self._on_event)

    async def _on_event(self, event: Event) -> None:
        if event.type in _NOT_PERSISTED or not event.at_least(Severity.INFO):
            return
        await self._db.execute(
            """
            INSERT INTO events (id, type, timestamp, severity, source, message,
                                trace_id, mission_id, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.type,
                event.timestamp.isoformat(),
                event.severity,
                event.source,
                event.message,
                event.trace_id,
                event.mission_id,
                json.dumps(event.payload, default=str),
            ),
        )

    async def recent(self, limit: int = 200) -> list[Event]:
        rows = await self._db.fetch_all(
            "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        return _oldest_first(rows)

    async def conversation(self, limit: int = 100, before: str | None = None) -> list[Event]:
        """What was said: commands and JARVIS's replies, oldest first.

        ``before`` (an event timestamp) pages back through older history.
        """
        types = (EventType.COMMAND_RECEIVED.value, EventType.JARVIS_MESSAGE.value)
        rows = await self._db.fetch_all(
            "SELECT * FROM events WHERE type IN (?, ?) AND timestamp < ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (*types, before or "9999", limit),
        )
        return _oldest_first(rows)


def _oldest_first(rows: list[Any]) -> list[Event]:
    events = [
        Event(
            id=row["id"],
            type=EventType(row["type"]),
            timestamp=row["timestamp"],
            severity=Severity(row["severity"]),
            source=row["source"],
            message=row["message"],
            trace_id=row["trace_id"],
            mission_id=row["mission_id"],
            payload=json.loads(row["payload"]),
        )
        for row in rows
    ]
    events.reverse()
    return events
