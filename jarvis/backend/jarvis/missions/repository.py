"""Mission persistence (SQLite; JSON document + indexed columns)."""

from __future__ import annotations

from jarvis.missions.models import Mission, MissionStatus, StepStatus
from jarvis.storage.database import Database
from jarvis.tools.base import ToolError
from jarvis.util import utcnow

_UNFINISHED = (
    MissionStatus.PENDING,
    MissionStatus.ACTIVE,
    MissionStatus.PAUSED,
    MissionStatus.WAITING_FOR_APPROVAL,
)


class MissionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def next_number(self) -> int:
        row = await self._db.fetch_one("SELECT COALESCE(MAX(number), 0) + 1 AS n FROM missions")
        return int(row["n"]) if row else 1

    async def save(self, mission: Mission) -> None:
        await self._db.execute(
            """
            INSERT INTO missions (id, number, status, title, trace_id, created_at, updated_at, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                title = excluded.title,
                updated_at = excluded.updated_at,
                data = excluded.data
            """,
            (
                mission.id,
                mission.number,
                mission.status,
                mission.title,
                mission.trace_id,
                mission.created_at.isoformat(),
                mission.updated_at.isoformat(),
                mission.model_dump_json(),
            ),
        )

    async def get(self, mission_id: str) -> Mission | None:
        row = await self._db.fetch_one("SELECT data FROM missions WHERE id = ?", (mission_id,))
        return Mission.model_validate_json(row["data"]) if row else None

    async def list(self, limit: int = 50) -> list[Mission]:
        rows = await self._db.fetch_all(
            "SELECT data FROM missions ORDER BY number DESC LIMIT ?", (limit,)
        )
        return [Mission.model_validate_json(row["data"]) for row in rows]

    async def fail_interrupted(self) -> int:
        """Missions that were running when JARVIS stopped can't resume safely."""
        placeholders = ",".join("?" * len(_UNFINISHED))
        rows = await self._db.fetch_all(
            f"SELECT data FROM missions WHERE status IN ({placeholders})",
            tuple(str(s) for s in _UNFINISHED),
        )
        for row in rows:
            mission = Mission.model_validate_json(row["data"])
            mission.status = MissionStatus.FAILED
            mission.result = "Interrupted — JARVIS was restarted while this mission was running."
            mission.errors.append(
                ToolError(code="interrupted", message="JARVIS restarted during this mission.")
            )
            for step in mission.steps:
                if step.status not in (StepStatus.COMPLETE, StepStatus.FAILED, StepStatus.REJECTED):
                    step.status = StepStatus.SKIPPED
            mission.finished_at = utcnow()
            mission.touch()
            await self.save(mission)
        return len(rows)
