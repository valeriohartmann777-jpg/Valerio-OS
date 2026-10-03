"""Persistent audit trail for consequential actions."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from jarvis.storage.database import Database
from jarvis.util import utcnow

_SECRET_KEY = re.compile(r"pass(word)?|secret|token|api[_-]?key|credential|auth", re.I)
_MAX_VALUE_LEN = 200


def redact(arguments: dict[str, Any]) -> dict[str, Any]:
    """Summarise arguments for the audit log without leaking secrets."""
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        if _SECRET_KEY.search(key):
            summary[key] = "[redacted]"
        elif isinstance(value, str) and len(value) > _MAX_VALUE_LEN:
            summary[key] = value[:_MAX_VALUE_LEN] + "…"
        elif isinstance(value, dict):
            summary[key] = redact(value)
        else:
            summary[key] = value
    return summary


class AuditEntry(BaseModel):
    id: int | None = None
    timestamp: datetime
    trace_id: str | None
    mission_id: str | None
    agent: str | None
    tool: str
    arguments: dict[str, Any]
    permission_level: int
    approval: str
    success: bool
    summary: str
    simulated: bool = False


class AuditLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        permission_level: int,
        approval: str,
        success: bool,
        summary: str,
        trace_id: str | None,
        mission_id: str | None,
        agent: str | None,
        simulated: bool = False,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO audit_log (timestamp, trace_id, mission_id, agent, tool, arguments,
                                   permission_level, approval, success, summary, simulated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utcnow().isoformat(),
                trace_id,
                mission_id,
                agent,
                tool,
                json.dumps(redact(arguments)),
                permission_level,
                approval,
                int(success),
                summary,
                int(simulated),
            ),
        )

    async def recent(self, limit: int = 100) -> list[AuditEntry]:
        rows = await self._db.fetch_all(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [
            AuditEntry(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                trace_id=row["trace_id"],
                mission_id=row["mission_id"],
                agent=row["agent"],
                tool=row["tool"],
                arguments=json.loads(row["arguments"]),
                permission_level=row["permission_level"],
                approval=row["approval"],
                success=bool(row["success"]),
                summary=row["summary"],
                simulated=bool(row["simulated"]),
            )
            for row in rows
        ]
