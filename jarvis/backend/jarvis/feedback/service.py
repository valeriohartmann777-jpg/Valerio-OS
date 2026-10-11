"""Feedback intake: the owner rates a reply, JARVIS keeps it with what was asked.

This is evidence for the Training Hub — which commands fail, on which route — not training:
nothing here changes a model, a prompt or a rule by itself.
"""

from __future__ import annotations

import json
from typing import Any

from jarvis.events.bus import EventBus
from jarvis.events.types import EventType
from jarvis.storage.database import Database
from jarvis.util import utcnow

MAX_NOTE = 1000


class FeedbackError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class FeedbackService:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self._db = db
        self._bus = bus

    async def rate(self, event_id: str, rating: int, note: str | None = None) -> dict[str, Any]:
        if rating not in (-1, 1):
            raise FeedbackError("INVALID_RATING", "A rating is 👍 (1) or 👎 (-1).")
        reply = await self._db.fetch_one(
            "SELECT * FROM events WHERE id = ? AND type = ?",
            (event_id, EventType.JARVIS_MESSAGE.value),
        )
        if reply is None:
            raise FeedbackError("NOT_FOUND", "That reply isn't in the conversation.", 404)
        command, intent, route = None, None, None
        if reply["trace_id"]:
            asked = await self._db.fetch_one(
                "SELECT payload FROM events WHERE trace_id = ? AND type = ?",
                (reply["trace_id"], EventType.COMMAND_RECEIVED.value),
            )
            if asked is not None:
                command = str(json.loads(asked["payload"]).get("text", ""))[:500]
            classified = await self._db.fetch_one(
                "SELECT payload FROM events WHERE trace_id = ? AND type = ?",
                (reply["trace_id"], EventType.INTENT_CLASSIFIED.value),
            )
            if classified is not None:
                data = json.loads(classified["payload"])
                route = data.get("route")
                intent = (data.get("intent") or {}).get("kind")
        text = str(json.loads(reply["payload"]).get("text") or reply["message"])[:1000]
        clean = (note or "").strip()[:MAX_NOTE] or None
        # One rating per reply: a new one replaces the old (the owner changed their mind).
        await self._db.execute(
            "INSERT INTO fb_feedback "
            "(at, event_id, trace_id, rating, note, reply, command, intent, route) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id) DO UPDATE SET at = excluded.at, rating = excluded.rating, "
            "note = excluded.note",
            (
                utcnow().isoformat(),
                event_id,
                reply["trace_id"],
                rating,
                clean,
                text,
                command,
                intent,
                route,
            ),
        )
        await self._bus.emit(
            EventType.FEEDBACK_RECORDED,
            message="Feedback recorded" + (" — marked as wrong" if rating < 0 else ""),
            payload={"event_id": event_id, "rating": rating},
        )
        return await self.overview()

    async def overview(self, limit: int = 100) -> dict[str, Any]:
        rows = await self._db.fetch_all(
            "SELECT * FROM fb_feedback ORDER BY at DESC LIMIT ?", (min(max(limit, 1), 500),)
        )
        totals = await self._db.fetch_all(
            "SELECT COALESCE(route, 'unknown') AS route, "
            "SUM(CASE WHEN rating > 0 THEN 1 ELSE 0 END) AS up, "
            "SUM(CASE WHEN rating < 0 THEN 1 ELSE 0 END) AS down "
            "FROM fb_feedback GROUP BY COALESCE(route, 'unknown') ORDER BY down DESC"
        )
        rated = await self._db.fetch_all("SELECT event_id, rating FROM fb_feedback")
        return {
            "items": [dict(r) for r in rows],
            "by_route": [dict(r) for r in totals],
            "up": sum(int(r["up"]) for r in totals),
            "down": sum(int(r["down"]) for r in totals),
            "ratings": {r["event_id"]: r["rating"] for r in rated},
        }
