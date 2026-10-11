"""Persistence for team workspaces (``tm_*`` tables, migration 17)."""

from __future__ import annotations

import json
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import utcnow


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class TeamStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # projects ---------------------------------------------------------------------------------

    async def add_project(self, row: dict[str, Any]) -> None:
        now = utcnow().isoformat()
        await self._db.execute(
            "INSERT INTO tm_projects (id, name, solo_strategy_id, solo_version_id, "
            "team_strategy_id, grants, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (
                row["id"],
                row["name"],
                row["solo_strategy_id"],
                row["solo_version_id"],
                row["team_strategy_id"],
                json.dumps(row["grants"], sort_keys=True),
                now,
                now,
            ),
        )

    async def project(self, project_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM tm_projects WHERE id = ?", (project_id,))
        return self._project(row) if row else None

    async def projects(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM tm_projects ORDER BY created_at DESC")
        return [self._project(r) for r in rows]

    async def team_strategy_ids(self) -> set[str]:
        rows = await self._db.fetch_all("SELECT team_strategy_id FROM tm_projects")
        return {str(r["team_strategy_id"]) for r in rows}

    async def project_of_team_strategy(self, strategy_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM tm_projects WHERE team_strategy_id = ?", (strategy_id,)
        )
        return self._project(row) if row else None

    async def update_project(self, project_id: str, **fields: Any) -> None:
        if "grants" in fields:
            fields["grants"] = json.dumps(fields["grants"], sort_keys=True)
        fields["updated_at"] = utcnow().isoformat()
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(
            f"UPDATE tm_projects SET {sets} WHERE id = ?", (*fields.values(), project_id)
        )

    @staticmethod
    def _project(row: Any) -> dict[str, Any]:
        out = dict(row)
        out["grants"] = _loads(out["grants"])
        return out

    # the append-only audit ------------------------------------------------------------------

    async def audit(self, project_id: str, actor: str, action: str, detail: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO tm_audit (project_id, at, actor, action, detail) VALUES (?, ?, ?, ?, ?)",
            (project_id, utcnow().isoformat(), actor, action, json.dumps(detail, sort_keys=True)),
        )

    async def audit_log(self, project_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM tm_audit WHERE project_id = ? ORDER BY id", (project_id,)
        )
        return [{**dict(r), "detail": _loads(r["detail"])} for r in rows]

    # agent tasks ------------------------------------------------------------------------------

    async def add_task(self, row: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO tm_tasks (id, project_id, agent, kind, state, request, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["project_id"],
                row["agent"],
                row["kind"],
                row["state"],
                json.dumps(row["request"], sort_keys=True),
                utcnow().isoformat(),
            ),
        )

    async def update_task(self, task_id: str, **fields: Any) -> None:
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"], sort_keys=True)
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(
            f"UPDATE tm_tasks SET {sets} WHERE id = ?", (*fields.values(), task_id)
        )

    async def task(self, task_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM tm_tasks WHERE id = ?", (task_id,))
        return self._task(row) if row else None

    async def tasks(self, project_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM tm_tasks WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
        )
        return [self._task(r) for r in rows]

    async def tasks_in(self, *states: str) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in states)
        rows = await self._db.fetch_all(f"SELECT * FROM tm_tasks WHERE state IN ({marks})", states)
        return [self._task(r) for r in rows]

    async def hq_tasks(self, since: str) -> list[dict[str, Any]]:
        """Team tasks for the HQ: open ones, plus those finished at or after ``since``."""
        rows = await self._db.fetch_all(
            "SELECT t.*, p.name AS project_name FROM tm_tasks t "
            "JOIN tm_projects p ON p.id = t.project_id "
            "WHERE t.state IN ('QUEUED', 'RUNNING') OR t.finished_at >= ? ORDER BY t.created_at",
            (since,),
        )
        return [self._task(r) for r in rows]

    @staticmethod
    def _task(row: Any) -> dict[str, Any]:
        out = dict(row)
        out["request"] = _loads(out["request"])
        out["result"] = _loads(out["result"]) if out.get("result") else None
        return out
