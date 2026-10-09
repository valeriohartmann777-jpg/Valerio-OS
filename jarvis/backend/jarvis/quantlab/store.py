"""QuantLab records in SQLite. Versions and dataset snapshots are insert-only
(triggers refuse updates); experiments change status, never their manifest."""

from __future__ import annotations

import json
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import utcnow


def _now() -> str:
    return utcnow().isoformat()


def _load(text: str | None) -> Any:
    return json.loads(text) if text else None


class QuantLabStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # Strategies -------------------------------------------------------------------------

    async def add_strategy(self, id_: str, name: str, hypothesis: str) -> None:
        await self._db.execute(
            "INSERT INTO ql_strategies (id, name, hypothesis, created_at) VALUES (?, ?, ?, ?)",
            (id_, name, hypothesis, _now()),
        )

    async def strategy(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM ql_strategies WHERE id = ?", (id_,))
        return dict(row) if row else None

    async def strategies(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM ql_strategies ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    async def add_version(self, id_: str, strategy_id: str, spec_json: str, sha: str) -> int:
        row = await self._db.fetch_one(
            "SELECT MAX(number) AS n FROM ql_strategy_versions WHERE strategy_id = ?",
            (strategy_id,),
        )
        number = 1 if row is None or row["n"] is None else int(row["n"]) + 1
        await self._db.execute(
            "INSERT INTO ql_strategy_versions (id, strategy_id, number, spec_json, spec_sha256, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (id_, strategy_id, number, spec_json, sha, _now()),
        )
        return number

    async def version(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM ql_strategy_versions WHERE id = ?", (id_,))
        return self._version(row) if row else None

    async def version_by_sha(self, strategy_id: str, sha: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ql_strategy_versions WHERE strategy_id = ? AND spec_sha256 = ?",
            (strategy_id, sha),
        )
        return self._version(row) if row else None

    async def versions(self, strategy_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ql_strategy_versions WHERE strategy_id = ? ORDER BY number",
            (strategy_id,),
        )
        return [self._version(r) for r in rows]

    @staticmethod
    def _version(row: Any) -> dict[str, Any]:
        return {**dict(row), "spec": json.loads(row["spec_json"])}

    # Datasets ---------------------------------------------------------------------------

    async def add_dataset(self, record: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO ql_datasets (id, created_at, status, synthetic, symbol, frequency, "
            "filename, original_sha256, normalized_sha256, snapshot_sha256, rows, passport, "
            "preview) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["id"],
                _now(),
                record["status"],
                int(record["synthetic"]),
                record["symbol"],
                record["frequency"],
                record["filename"],
                record["original_sha256"],
                record["normalized_sha256"],
                record["snapshot_sha256"],
                record["rows"],
                json.dumps(record["passport"]),
                json.dumps(record["preview"]),
            ),
        )

    async def dataset(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM ql_datasets WHERE id = ?", (id_,))
        return self._dataset(row) if row else None

    async def datasets(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM ql_datasets ORDER BY created_at DESC")
        return [self._dataset(r) for r in rows]

    @staticmethod
    def _dataset(row: Any) -> dict[str, Any]:
        return {
            **dict(row),
            "synthetic": bool(row["synthetic"]),
            "passport": json.loads(row["passport"]),
            "preview": json.loads(row["preview"]),
        }

    # Experiments ------------------------------------------------------------------------

    async def add_experiment(
        self, id_: str, version_id: str, dataset_id: str, manifest: str, sha: str
    ) -> None:
        await self._db.execute(
            "INSERT INTO ql_experiments (id, strategy_version_id, dataset_id, manifest, "
            "manifest_sha256, status, created_at) VALUES (?, ?, ?, ?, ?, 'queued', ?)",
            (id_, version_id, dataset_id, manifest, sha, _now()),
        )

    async def experiment(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM ql_experiments WHERE id = ?", (id_,))
        return self._experiment(row) if row else None

    async def experiments(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ql_experiments ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [self._experiment(r) for r in rows]

    async def experiments_with_status(self, *statuses: str) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in statuses)
        rows = await self._db.fetch_all(
            f"SELECT * FROM ql_experiments WHERE status IN ({marks})", statuses
        )
        return [self._experiment(r) for r in rows]

    async def variants_on_dataset(self, dataset_id: str) -> int:
        row = await self._db.fetch_one(
            "SELECT COUNT(DISTINCT strategy_version_id) AS n FROM ql_experiments "
            "WHERE dataset_id = ? AND status IN ('queued', 'running', 'completed')",
            (dataset_id,),
        )
        return int(row["n"]) if row else 0

    @staticmethod
    def _experiment(row: Any) -> dict[str, Any]:
        return {
            **dict(row),
            "manifest": json.loads(row["manifest"]),
            "summary": _load(row["summary"]),
        }

    async def set_queued(self, id_: str) -> None:
        await self._db.execute(
            "UPDATE ql_experiments SET status = 'queued', stage = NULL, error_code = NULL, "
            "error = NULL, started_at = NULL, finished_at = NULL WHERE id = ?",
            (id_,),
        )

    async def set_running(self, id_: str, stage: str) -> None:
        await self._db.execute(
            "UPDATE ql_experiments SET status = 'running', stage = ?, "
            "started_at = COALESCE(started_at, ?), attempts = attempts + "
            "(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) WHERE id = ?",
            (stage, _now(), id_),
        )

    async def set_finished(
        self,
        id_: str,
        status: str,
        *,
        error_code: str | None = None,
        error: str | None = None,
        verdict: str | None = None,
        results_sha256: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE ql_experiments SET status = ?, stage = NULL, finished_at = ?, "
            "error_code = ?, error = ?, verdict = ?, results_sha256 = ?, summary = ? "
            "WHERE id = ?",
            (
                status,
                _now(),
                error_code,
                error,
                verdict,
                results_sha256,
                json.dumps(summary) if summary is not None else None,
                id_,
            ),
        )

    async def add_artifacts(
        self, experiment_id: str, rows: list[tuple[str, str, str, int]]
    ) -> None:
        await self._db.execute("DELETE FROM ql_artifacts WHERE experiment_id = ?", (experiment_id,))
        await self._db.execute_many(
            "INSERT INTO ql_artifacts (experiment_id, kind, relative_path, sha256, bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            [(experiment_id, *row) for row in rows],
        )

    async def artifacts(self, experiment_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT kind, relative_path, sha256, bytes FROM ql_artifacts WHERE experiment_id = ? "
            "ORDER BY kind",
            (experiment_id,),
        )
        return [dict(r) for r in rows]

    async def add_validations(self, experiment_id: str, checks: list[dict[str, Any]]) -> None:
        await self._db.execute(
            "DELETE FROM ql_validations WHERE experiment_id = ?", (experiment_id,)
        )
        await self._db.execute_many(
            "INSERT INTO ql_validations (experiment_id, check_id, gate, method_version, result, "
            "observations, metric, assumptions, evidence_artifact) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    experiment_id,
                    c["id"],
                    c["gate"],
                    c["method_version"],
                    c["result"],
                    c["observations"],
                    json.dumps(c["metric"]),
                    json.dumps(c["assumptions"]),
                    c["evidence_artifact"],
                )
                for c in checks
            ],
        )
