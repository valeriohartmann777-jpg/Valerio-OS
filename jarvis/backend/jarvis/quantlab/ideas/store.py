"""Research mission records: missions, typed agent tasks, activity, the trial ledger."""

from __future__ import annotations

import json
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import utcnow

_MISSION_JSON = ("waiting", "budget", "refs")
_TASK_JSON = (
    "allowed_tools",
    "budget",
    "actions",
    "artifacts",
    "results",
    "evidence_refs",
    "limitations",
)
_TRIAL_JSON = ("changes", "metrics", "audit")


def now() -> str:
    return utcnow().isoformat()


def _decode(row: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    out = dict(row)
    for key in keys:
        if out.get(key) is not None:
            out[key] = json.loads(out[key])
    return out


class MissionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # missions -------------------------------------------------------------------------------

    async def add(self, row: dict[str, Any]) -> None:
        stamp = now()
        data = {k: (json.dumps(v) if k in _MISSION_JSON else v) for k, v in row.items()}
        data.update(created_at=stamp, updated_at=stamp)
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        await self._db.execute(
            f"INSERT INTO qm_missions ({cols}) VALUES ({marks})", tuple(data.values())
        )

    async def get(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qm_missions WHERE id = ?", (id_,))
        return _decode(row, _MISSION_JSON) if row else None

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qm_missions ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_decode(r, _MISSION_JSON) for r in rows]

    async def for_source(self, source_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qm_missions WHERE source_id = ? ORDER BY created_at DESC", (source_id,)
        )
        return [_decode(r, _MISSION_JSON) for r in rows]

    async def in_state(self, *states: str) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in states)
        rows = await self._db.fetch_all(
            f"SELECT * FROM qm_missions WHERE state IN ({marks}) ORDER BY created_at", states
        )
        return [_decode(r, _MISSION_JSON) for r in rows]

    async def update(self, id_: str, **fields: Any) -> None:
        values = {k: (json.dumps(v) if k in _MISSION_JSON else v) for k, v in fields.items()}
        values["updated_at"] = now()
        sets = ", ".join(f"{k} = ?" for k in values)
        await self._db.execute(
            f"UPDATE qm_missions SET {sets} WHERE id = ?", (*values.values(), id_)
        )

    async def transition(
        self, id_: str, allowed: tuple[str, ...], state: str, **fields: Any
    ) -> bool:
        values = {k: (json.dumps(v) if k in _MISSION_JSON else v) for k, v in fields.items()}
        values.update(state=state, updated_at=now())
        sets = ", ".join(f"{k} = ?" for k in values)
        marks = ", ".join("?" for _ in allowed)
        changed = await self._db.execute_count(
            f"UPDATE qm_missions SET {sets} WHERE id = ? AND state IN ({marks})",
            (*values.values(), id_, *allowed),
        )
        return changed == 1

    async def add_spend(self, id_: str, usd: float) -> None:
        await self._db.execute(
            "UPDATE qm_missions SET spent_usd = spent_usd + ?, updated_at = ? WHERE id = ?",
            (usd, now(), id_),
        )

    # tasks ----------------------------------------------------------------------------------

    async def add_task(self, row: dict[str, Any]) -> None:
        last = await self._db.fetch_one(
            "SELECT MAX(seq) AS n FROM qm_tasks WHERE mission_id = ?", (row["mission_id"],)
        )
        seq = 1 if last is None or last["n"] is None else int(last["n"]) + 1
        data = {k: (json.dumps(v) if k in _TASK_JSON else v) for k, v in row.items()}
        data.update(seq=seq, created_at=now())
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        await self._db.execute(
            f"INSERT INTO qm_tasks ({cols}) VALUES ({marks})", tuple(data.values())
        )

    async def update_task(self, id_: str, **fields: Any) -> None:
        values = {k: (json.dumps(v) if k in _TASK_JSON else v) for k, v in fields.items()}
        sets = ", ".join(f"{k} = ?" for k in values)
        await self._db.execute(f"UPDATE qm_tasks SET {sets} WHERE id = ?", (*values.values(), id_))

    async def add_task_cost(self, id_: str, usd: float) -> None:
        await self._db.execute(
            "UPDATE qm_tasks SET cost_usd = cost_usd + ? WHERE id = ?", (usd, id_)
        )

    async def task(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qm_tasks WHERE id = ?", (id_,))
        return _decode(row, _TASK_JSON) if row else None

    async def tasks(self, mission_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qm_tasks WHERE mission_id = ? ORDER BY seq", (mission_id,)
        )
        return [_decode(r, _TASK_JSON) for r in rows]

    async def tasks_in(self, *states: str) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in states)
        rows = await self._db.fetch_all(
            f"SELECT * FROM qm_tasks WHERE state IN ({marks}) ORDER BY created_at", states
        )
        return [_decode(r, _TASK_JSON) for r in rows]

    async def agent_stats(self) -> dict[str, dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT agent, state, COUNT(*) AS n, SUM(cost_usd) AS usd FROM qm_tasks "
            "GROUP BY agent, state"
        )
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            entry = out.setdefault(r["agent"], {"tasks": {}, "spent_usd": 0.0})
            entry["tasks"][r["state"]] = int(r["n"])
            entry["spent_usd"] += float(r["usd"] or 0)
        return out

    # activity -------------------------------------------------------------------------------

    async def event(
        self,
        mission_id: str,
        kind: str,
        message: str,
        *,
        task_id: str | None = None,
        agent: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO qm_events (mission_id, task_id, agent, kind, message, data, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                mission_id,
                task_id,
                agent,
                kind,
                message,
                json.dumps(data, default=str) if data else None,
                now(),
            ),
        )

    async def events(self, mission_id: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        if mission_id is None:
            rows = await self._db.fetch_all(
                "SELECT * FROM qm_events ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM qm_events WHERE mission_id = ? ORDER BY id DESC LIMIT ?",
                (mission_id, limit),
            )
        return [_decode(r, ("data",)) for r in rows]

    # trials (append-only) -------------------------------------------------------------------

    async def add_trial(self, row: dict[str, Any]) -> None:
        data = {k: (json.dumps(v) if k in _TRIAL_JSON else v) for k, v in row.items()}
        data["created_at"] = now()
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        await self._db.execute(
            f"INSERT INTO qm_trials ({cols}) VALUES ({marks})", tuple(data.values())
        )

    async def trials(
        self, *, mission_id: str | None = None, family_id: str | None = None
    ) -> list[dict[str, Any]]:
        if mission_id is not None:
            rows = await self._db.fetch_all(
                "SELECT * FROM qm_trials WHERE mission_id = ? ORDER BY created_at", (mission_id,)
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM qm_trials WHERE family_id = ? ORDER BY created_at", (family_id,)
            )
        out = []
        for r in rows:
            item = _decode(r, _TRIAL_JSON)
            item["post_holdout"] = bool(item["post_holdout"])
            out.append(item)
        return out
