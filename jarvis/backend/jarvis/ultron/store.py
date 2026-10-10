"""ULTRON's durable state (SQLite): the mission ledger the scheduler resumes from."""

from __future__ import annotations

import json
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import new_id, utcnow


def now() -> str:
    return utcnow().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load(text: str | None) -> Any:
    return json.loads(text) if text else None


class UltronStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # Missions ---------------------------------------------------------------------------

    async def add_mission(
        self, *, goal: str, kind: str, budget: float, workspace: str, model_label: str
    ) -> dict[str, Any]:
        row = await self._db.fetch_one("SELECT MAX(number) AS n FROM ul_missions")
        number = 1 if row is None or row["n"] is None else int(row["n"]) + 1
        mission_id = f"m{number:03d}-{new_id()[:6]}"
        stamp = now()
        title = goal.strip().splitlines()[0][:80] if goal.strip() else "Mission"
        await self._db.execute(
            "INSERT INTO ul_missions (id, number, title, goal, project_kind, state, budget_usd, "
            "workspace, model_label, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'PLANNING', ?, ?, ?, ?, ?)",
            (
                mission_id,
                number,
                title,
                goal,
                kind,
                budget,
                workspace.replace("{id}", mission_id),
                model_label,
                stamp,
                stamp,
            ),
        )
        mission = await self.mission(mission_id)
        assert mission is not None
        return mission

    async def mission(self, mission_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM ul_missions WHERE id = ?", (mission_id,))
        return self._mission(row) if row else None

    async def missions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ul_missions ORDER BY number DESC LIMIT ?", (limit,)
        )
        return [self._mission(r) for r in rows]

    async def missions_in(self, *states: str) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in states)
        rows = await self._db.fetch_all(
            f"SELECT * FROM ul_missions WHERE state IN ({marks}) ORDER BY number", states
        )
        return [self._mission(r) for r in rows]

    @staticmethod
    def _mission(row: Any) -> dict[str, Any]:
        out = dict(row)
        for key in ("charter", "plan_notes", "report", "blocker"):
            out[key] = _load(out[key])
        return out

    async def update_mission(self, mission_id: str, **fields: Any) -> None:
        for key in ("charter", "plan_notes", "report", "blocker"):
            if key in fields and fields[key] is not None:
                fields[key] = _json(fields[key])
        fields["updated_at"] = now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(
            f"UPDATE ul_missions SET {sets} WHERE id = ?", (*fields.values(), mission_id)
        )

    async def add_spend(self, mission_id: str, usd: float) -> None:
        await self._db.execute(
            "UPDATE ul_missions SET spent_usd = spent_usd + ?, updated_at = ? WHERE id = ?",
            (usd, now(), mission_id),
        )

    # Tasks ------------------------------------------------------------------------------

    async def add_task(
        self,
        mission_id: str,
        *,
        key: str,
        title: str,
        owner: str,
        depends_on: list[str],
        contract: dict[str, Any],
        max_attempts: int,
    ) -> str:
        task_id = f"{mission_id}:{key}"
        await self._db.execute(
            "INSERT INTO ul_tasks (id, mission_id, key, title, owner, depends_on, contract, "
            "state, max_attempts) VALUES (?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?)",
            (
                task_id,
                mission_id,
                key,
                title,
                owner,
                _json(depends_on),
                _json(contract),
                max_attempts,
            ),
        )
        return task_id

    async def delete_tasks(self, mission_id: str) -> None:
        await self._db.execute("DELETE FROM ul_tasks WHERE mission_id = ?", (mission_id,))

    async def task(self, task_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM ul_tasks WHERE id = ?", (task_id,))
        return self._task(row) if row else None

    async def tasks(self, mission_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ul_tasks WHERE mission_id = ? ORDER BY rowid", (mission_id,)
        )
        return [self._task(r) for r in rows]

    async def tasks_in(self, *states: str) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in states)
        rows = await self._db.fetch_all(
            f"SELECT * FROM ul_tasks WHERE state IN ({marks}) ORDER BY rowid", states
        )
        return [self._task(r) for r in rows]

    @staticmethod
    def _task(row: Any) -> dict[str, Any]:
        out = dict(row)
        out["depends_on"] = json.loads(out["depends_on"])
        out["contract"] = json.loads(out["contract"])
        out["result"] = _load(out["result"])
        return out

    async def update_task(self, task_id: str, **fields: Any) -> None:
        if "result" in fields and fields["result"] is not None:
            fields["result"] = _json(fields["result"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(
            f"UPDATE ul_tasks SET {sets} WHERE id = ?", (*fields.values(), task_id)
        )

    async def add_task_cost(self, task_id: str, usd: float) -> None:
        await self._db.execute(
            "UPDATE ul_tasks SET cost_usd = cost_usd + ? WHERE id = ?", (usd, task_id)
        )

    # Runs -------------------------------------------------------------------------------

    async def add_run(
        self, mission_id: str, task_id: str | None, agent: str, attempt: int, model: str
    ) -> str:
        run_id = f"r-{new_id()[:10]}"
        await self._db.execute(
            "INSERT INTO ul_runs (id, mission_id, task_id, agent, attempt, state, model, "
            "started_at) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?)",
            (run_id, mission_id, task_id, agent, attempt, model, now()),
        )
        return run_id

    async def update_run(self, run_id: str, **fields: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(
            f"UPDATE ul_runs SET {sets} WHERE id = ?", (*fields.values(), run_id)
        )

    async def finish_run(self, run_id: str, **fields: Any) -> None:
        fields["finished_at"] = now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(
            f"UPDATE ul_runs SET {sets} WHERE id = ?", (*fields.values(), run_id)
        )

    async def runs(self, mission_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ul_runs WHERE mission_id = ? ORDER BY started_at", (mission_id,)
        )
        return [dict(r) for r in rows]

    async def active_runs(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM ul_runs WHERE state = 'RUNNING'")
        return [dict(r) for r in rows]

    async def interrupt_runs(self) -> int:
        rows = await self.active_runs()
        await self._db.execute(
            "UPDATE ul_runs SET state = 'INTERRUPTED', finished_at = ?, "
            "error = 'JARVIS stopped during this run' WHERE state = 'RUNNING'",
            (now(),),
        )
        return len(rows)

    async def spend_since(self, since: str) -> float:
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM ul_runs WHERE started_at >= ?", (since,)
        )
        return float(row["c"]) if row else 0.0

    async def spend_by_agent(self) -> dict[str, float]:
        rows = await self._db.fetch_all(
            "SELECT agent, SUM(cost_usd) AS c, COUNT(*) AS n FROM ul_runs GROUP BY agent"
        )
        return {r["agent"]: float(r["c"] or 0) for r in rows}

    async def run_counts(self) -> dict[str, dict[str, int]]:
        rows = await self._db.fetch_all(
            "SELECT agent, state, COUNT(*) AS n FROM ul_runs GROUP BY agent, state"
        )
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["agent"], {})[r["state"]] = int(r["n"])
        return out

    # Artifacts --------------------------------------------------------------------------

    async def add_artifact(self, record: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO ul_artifacts (id, mission_id, task_id, run_id, agent, kind, name, path, "
            "sha256, bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["id"],
                record["mission_id"],
                record.get("task_id"),
                record.get("run_id"),
                record["agent"],
                record["kind"],
                record["name"],
                record["path"],
                record["sha256"],
                record["bytes"],
                now(),
            ),
        )

    async def artifacts(self, mission_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ul_artifacts WHERE mission_id = ? ORDER BY created_at", (mission_id,)
        )
        return [dict(r) for r in rows]

    async def artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM ul_artifacts WHERE id = ?", (artifact_id,))
        return dict(row) if row else None

    async def artifacts_of_kind(self, kind: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ul_artifacts WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
            (kind, limit),
        )
        return [dict(r) for r in rows]

    # Approvals --------------------------------------------------------------------------

    async def add_approval(self, record: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO ul_approvals (id, mission_id, task_id, category, title, effect, "
            "signature, state, requested_by, requested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (
                record["id"],
                record["mission_id"],
                record.get("task_id"),
                record["category"],
                record["title"],
                _json(record["effect"]),
                record["signature"],
                record["requested_by"],
                now(),
            ),
        )

    async def approval(self, approval_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM ul_approvals WHERE id = ?", (approval_id,))
        return self._approval(row) if row else None

    async def approvals(
        self, mission_id: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ul_approvals WHERE 1 = 1"
        params: list[Any] = []
        if mission_id:
            sql += " AND mission_id = ?"
            params.append(mission_id)
        if state:
            sql += " AND state = ?"
            params.append(state)
        rows = await self._db.fetch_all(sql + " ORDER BY requested_at DESC", params)
        return [self._approval(r) for r in rows]

    @staticmethod
    def _approval(row: Any) -> dict[str, Any]:
        return {**dict(row), "effect": json.loads(row["effect"])}

    async def decide_approval(
        self, approval_id: str, state: str, result: str | None = None
    ) -> bool:
        """Atomic: only a pending approval can be decided (no double execution)."""
        await self._db.execute(
            "UPDATE ul_approvals SET state = ?, decided_at = ?, result = ? "
            "WHERE id = ? AND state = 'pending'",
            (state, now(), result, approval_id),
        )
        row = await self.approval(approval_id)
        return row is not None and row["state"] == state

    async def finish_approval(self, approval_id: str, state: str, result: str) -> None:
        await self._db.execute(
            "UPDATE ul_approvals SET state = ?, result = ? WHERE id = ?",
            (state, result, approval_id),
        )

    # Events -----------------------------------------------------------------------------

    async def add_event(
        self,
        *,
        kind: str,
        message: str,
        severity: str = "info",
        mission_id: str | None = None,
        task_id: str | None = None,
        agent: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stamp = now()
        await self._db.execute(
            "INSERT INTO ul_events (at, mission_id, task_id, agent, kind, severity, message, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (stamp, mission_id, task_id, agent, kind, severity, message, _json(data or {})),
        )
        return {
            "at": stamp,
            "mission_id": mission_id,
            "task_id": task_id,
            "agent": agent,
            "kind": kind,
            "severity": severity,
            "message": message,
            "data": data or {},
        }

    async def events(
        self, mission_id: str | None = None, limit: int = 200, before: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ul_events WHERE 1 = 1"
        params: list[Any] = []
        if mission_id:
            sql += " AND mission_id = ?"
            params.append(mission_id)
        if before:
            sql += " AND id < ?"
            params.append(before)
        rows = await self._db.fetch_all(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]
