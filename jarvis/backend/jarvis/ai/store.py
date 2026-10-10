"""Persistence for AI routing: settings, approvals, usage, events, checkpoints, tool ledger."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import utcnow


def now() -> str:
    return utcnow().isoformat()


def month_of(stamp: str) -> str:
    return stamp[:7]


class AiStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # settings --------------------------------------------------------------------------------

    async def settings(self) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT data FROM ai_settings WHERE id = 1")
        return json.loads(row["data"]) if row else None

    async def save_settings(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO ai_settings (id, data, updated_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (json.dumps(data), now()),
        )

    # approvals (append-only) -----------------------------------------------------------------

    async def approve(self, kind: str, detail: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO ai_approvals (kind, detail, at) VALUES (?, ?, ?)",
            (kind, json.dumps(detail), now()),
        )

    async def approvals(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_approvals ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    # usage (append-only) -----------------------------------------------------------------------

    async def add_usage(self, row: dict[str, Any]) -> None:
        stamp = now()
        data = {"at": stamp, "month": month_of(stamp), **row}
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        await self._db.execute(
            f"INSERT INTO ai_usage ({cols}) VALUES ({marks})", tuple(data.values())
        )

    async def billed(self, month: str, mission_id: str | None = None) -> float:
        if mission_id is None:
            row = await self._db.fetch_one(
                "SELECT COALESCE(SUM(cost_usd), 0) AS usd FROM ai_usage "
                "WHERE month = ? AND billed = 1",
                (month,),
            )
        else:
            row = await self._db.fetch_one(
                "SELECT COALESCE(SUM(cost_usd), 0) AS usd FROM ai_usage "
                "WHERE billed = 1 AND mission_id = ?",
                (mission_id,),
            )
        return float(row["usd"]) if row else 0.0

    async def month_summary(self, month: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT provider, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, "
            "SUM(output_tokens) AS output_tokens, SUM(cache_read_tokens) AS cache_read_tokens, "
            "SUM(cache_write_tokens) AS cache_write_tokens, SUM(cost_usd) AS cost_usd, "
            "SUM(COALESCE(list_cost_usd, 0)) AS list_cost_usd, "
            "SUM(CASE WHEN outcome = 'ok' THEN 0 ELSE 1 END) AS failures "
            "FROM ai_usage WHERE month = ? GROUP BY provider",
            (month,),
        )
        return [dict(r) for r in rows]

    async def by_consumer(self, month: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT consumer, provider, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd "
            "FROM ai_usage WHERE month = ? AND outcome = 'ok' GROUP BY consumer, provider "
            "ORDER BY consumer",
            (month,),
        )
        return [dict(r) for r in rows]

    async def recent_usage(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM ai_usage ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # events --------------------------------------------------------------------------------------

    async def event(
        self,
        kind: str,
        message: str,
        provider: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO ai_events (at, kind, provider, message, data) VALUES (?, ?, ?, ?, ?)",
            (now(), kind, provider, message, json.dumps(data) if data else None),
        )

    async def events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_events ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [{**dict(r), "data": json.loads(r["data"]) if r["data"] else None} for r in rows]

    async def has_event(self, kind: str, marker: str) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 FROM ai_events WHERE kind = ? AND data LIKE ? LIMIT 1",
            (kind, f"%{marker}%"),
        )
        return row is not None

    # checkpoints and the tool-action ledger -------------------------------------------------------

    async def checkpoint(self, key: str) -> tuple[list[Any], int] | None:
        row = await self._db.fetch_one(
            "SELECT messages, rounds FROM ai_checkpoints WHERE key = ?", (key,)
        )
        return (json.loads(row["messages"]), int(row["rounds"])) if row else None

    async def save_checkpoint(self, key: str, messages: list[Any], rounds: int) -> None:
        await self._db.execute(
            "INSERT INTO ai_checkpoints (key, messages, rounds, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET messages = excluded.messages, "
            "rounds = excluded.rounds, updated_at = excluded.updated_at",
            (key, json.dumps(messages, default=str), rounds, now()),
        )

    async def clear_checkpoint(self, key: str) -> None:
        await self._db.execute("DELETE FROM ai_checkpoints WHERE key = ?", (key,))
        await self._db.execute("DELETE FROM ai_tool_ledger WHERE key = ?", (key,))

    async def ledger(self, key: str, call_id: str) -> tuple[str, bool] | None:
        row = await self._db.fetch_one(
            "SELECT content, is_error FROM ai_tool_ledger WHERE key = ? AND call_id = ?",
            (key, call_id),
        )
        return (str(row["content"]), bool(row["is_error"])) if row else None

    async def record_tool(
        self,
        key: str,
        call_id: str,
        name: str,
        payload: dict[str, Any],
        content: str,
        is_error: bool,
    ) -> None:
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        await self._db.execute(
            "INSERT OR IGNORE INTO ai_tool_ledger "
            "(key, call_id, name, input_sha256, content, is_error, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, call_id, name, digest, content, int(is_error), now()),
        )
