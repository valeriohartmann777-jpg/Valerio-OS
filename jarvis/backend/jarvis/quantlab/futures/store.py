"""Research records: strategies, insert-only versions, runs, trials, holdouts, notes."""

from __future__ import annotations

import json
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import utcnow


def now() -> str:
    return utcnow().isoformat()


def _run(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["manifest"] = json.loads(out["manifest"])
    out["summary"] = json.loads(out["summary"]) if out.get("summary") else None
    out["include_holdout"] = bool(out["include_holdout"])
    out["fixture"] = bool(out["fixture"])
    return out


class ResearchStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # Strategies and versions -------------------------------------------------------------

    async def add_strategy(self, id_: str, name: str, hypothesis: str, product: str) -> None:
        await self._db.execute(
            "INSERT INTO qr_strategies (id, name, hypothesis, product, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (id_, name, hypothesis, product, now()),
        )

    async def strategy(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qr_strategies WHERE id = ?", (id_,))
        return dict(row) if row else None

    async def strategies(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM qr_strategies ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def add_version(
        self,
        id_: str,
        strategy_id: str,
        spec_json: str,
        sha: str,
        origin: str,
        note: str | None,
        parent_id: str | None,
    ) -> int:
        row = await self._db.fetch_one(
            "SELECT MAX(number) AS n FROM qr_versions WHERE strategy_id = ?", (strategy_id,)
        )
        number = 1 if row is None or row["n"] is None else int(row["n"]) + 1
        await self._db.execute(
            "INSERT INTO qr_versions (id, strategy_id, number, parent_id, spec_json, spec_sha256, "
            "origin, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id_, strategy_id, number, parent_id, spec_json, sha, origin, note, now()),
        )
        return number

    async def version(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qr_versions WHERE id = ?", (id_,))
        return {**dict(row), "spec": json.loads(row["spec_json"])} if row else None

    async def version_by_sha(self, strategy_id: str, sha: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM qr_versions WHERE strategy_id = ? AND spec_sha256 = ?",
            (strategy_id, sha),
        )
        return {**dict(row), "spec": json.loads(row["spec_json"])} if row else None

    async def versions(self, strategy_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qr_versions WHERE strategy_id = ? ORDER BY number", (strategy_id,)
        )
        return [{**dict(r), "spec": json.loads(r["spec_json"])} for r in rows]

    # Runs -------------------------------------------------------------------------------

    async def add_run(self, record: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO qr_runs (id, strategy_id, version_id, dataset_id, kind, include_holdout, "
            "status, manifest, manifest_sha256, fixture, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?)",
            (
                record["id"],
                record["strategy_id"],
                record["version_id"],
                record["dataset_id"],
                record["kind"],
                int(record["include_holdout"]),
                json.dumps(record["manifest"]),
                record["manifest_sha256"],
                int(record["fixture"]),
                now(),
            ),
        )

    async def run(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qr_runs WHERE id = ?", (id_,))
        return _run(row) if row else None

    async def runs(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qr_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_run(r) for r in rows]

    async def runs_with_status(self, *statuses: str) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in statuses)
        rows = await self._db.fetch_all(
            f"SELECT * FROM qr_runs WHERE status IN ({marks})", statuses
        )
        return [_run(r) for r in rows]

    async def update_run(self, id_: str, **fields: Any) -> None:
        if "summary" in fields and fields["summary"] is not None:
            fields["summary"] = json.dumps(fields["summary"], default=str)
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(f"UPDATE qr_runs SET {sets} WHERE id = ?", (*fields.values(), id_))

    async def transition(self, id_: str, expected: tuple[str, ...], **fields: Any) -> bool:
        """Compare-and-set on the run status: True only if the run was in ``expected``."""
        if "summary" in fields and fields["summary"] is not None:
            fields["summary"] = json.dumps(fields["summary"], default=str)
        sets = ", ".join(f"{k} = ?" for k in fields)
        marks = ",".join("?" for _ in expected)
        changed = await self._db.execute_count(
            f"UPDATE qr_runs SET {sets} WHERE id = ? AND status IN ({marks})",
            (*fields.values(), id_, *expected),
        )
        return changed == 1

    # Trials, holdouts, notes ------------------------------------------------------------

    async def add_trials(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        await self._db.execute_many(
            "INSERT INTO qr_trials (strategy_id, run_id, version_id, params, segment, trades, "
            "net, sharpe_daily, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["strategy_id"],
                    r["run_id"],
                    r["version_id"],
                    json.dumps(r["params"], sort_keys=True),
                    r["segment"],
                    r["trades"],
                    r["net"],
                    r["sharpe_daily"],
                    now(),
                )
                for r in rows
            ],
        )

    async def trial_keys(self, strategy_id: str) -> dict[tuple[str, str], float | None]:
        """Every distinct variant (version × parameters) tried for a strategy family, with its
        in-sample daily Sharpe — the universe a selection-bias correction has to account for."""
        rows = await self._db.fetch_all(
            "SELECT version_id, params, MAX(sharpe_daily) AS s FROM qr_trials "
            "WHERE strategy_id = ? AND segment IN ('IS', 'BASE') GROUP BY version_id, params",
            (strategy_id,),
        )
        return {
            (str(r["version_id"]), str(r["params"])): (
                float(r["s"]) if r["s"] is not None else None
            )
            for r in rows
        }

    async def trial_stats(self, strategy_id: str) -> tuple[int, list[float]]:
        keys = await self.trial_keys(strategy_id)
        return len(keys), [s for s in keys.values() if s is not None]

    async def trials(self, strategy_id: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qr_trials WHERE strategy_id = ? ORDER BY id DESC LIMIT ?",
            (strategy_id, limit),
        )
        return [{**dict(r), "params": json.loads(r["params"])} for r in rows]

    async def add_holdout(
        self, strategy_id: str, product: str, first: str, last: str, run_id: str
    ) -> None:
        await self._db.execute(
            "INSERT INTO qr_holdouts (strategy_id, product, first_day, last_day, run_id, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (strategy_id, product, first, last, run_id, now()),
        )

    async def holdout_looks(self, strategy_id: str, first: str, last: str) -> int:
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM qr_holdouts WHERE strategy_id = ? "
            "AND first_day <= ? AND last_day >= ?",
            (strategy_id, last, first),
        )
        return int(row["n"]) if row else 0

    async def holdouts(self, strategy_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qr_holdouts WHERE strategy_id = ? ORDER BY id", (strategy_id,)
        )
        return [dict(r) for r in rows]

    async def add_note(self, strategy_id: str, run_id: str | None, kind: str, text: str) -> None:
        await self._db.execute(
            "INSERT INTO qr_notes (strategy_id, run_id, kind, text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (strategy_id, run_id, kind, text, now()),
        )

    async def notes(self, strategy_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qr_notes WHERE strategy_id = ? ORDER BY id DESC", (strategy_id,)
        )
        return [dict(r) for r in rows]
