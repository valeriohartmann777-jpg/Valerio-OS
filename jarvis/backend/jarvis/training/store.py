"""Training runs (SQLite) and the model in use (a file in data/training/).

The model file is a pickle JARVIS writes itself. It is only loaded when it was
written by the same scikit-learn version for the same inputs; otherwise
JARVIS simply trains again.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.storage.database import Database
from jarvis.training.dataset import FEATURES
from jarvis.training.model import Trained
from jarvis.util import new_id, utcnow

log = logging.getLogger("jarvis.training")

FORMAT = 1


@dataclass
class Bundle:
    trained: Trained
    run: int  # the training run that made it
    data_until: str  # last day of market data it learned from


def _now() -> str:
    return utcnow().isoformat()


class TrainingStore:
    def __init__(self, db: Database, folder: Path) -> None:
        self._db = db
        self._path = folder / "model.pkl"

    # Runs ------------------------------------------------------------------------------

    async def interrupted(self) -> int:
        rows = await self._db.fetch_all("SELECT id FROM training_runs WHERE status = 'running'")
        await self._db.execute(
            "UPDATE training_runs SET status = 'interrupted', finished_at = ? "
            "WHERE status = 'running'",
            (_now(),),
        )
        return len(rows)

    async def start_run(self) -> tuple[str, int]:
        row = await self._db.fetch_one("SELECT MAX(number) AS n FROM training_runs")
        number = (int(row["n"]) if row and row["n"] is not None else 0) + 1
        run_id = new_id()
        await self._db.execute(
            "INSERT INTO training_runs (id, number, started_at, status) "
            "VALUES (?, ?, ?, 'running')",
            (run_id, number, _now()),
        )
        return run_id, number

    async def finish_run(
        self, run_id: str, *, data_until: str, report: dict[str, Any], seconds: float
    ) -> None:
        await self._db.execute(
            "UPDATE training_runs SET status = 'done', finished_at = ?, data_until = ?, "
            "verdict = ?, report = ?, seconds = ? WHERE id = ?",
            (
                _now(),
                data_until,
                report.get("status"),
                json.dumps(report),
                round(seconds, 1),
                run_id,
            ),
        )

    async def fail_run(self, run_id: str, *, error: str, seconds: float) -> None:
        await self._db.execute(
            "UPDATE training_runs SET status = 'failed', finished_at = ?, error = ?, seconds = ? "
            "WHERE id = ?",
            (_now(), error[:500], round(seconds, 1), run_id),
        )

    async def runs(self, limit: int = 30) -> list[dict[str, Any]]:
        """Recent runs, newest first, with the headline numbers of each."""
        rows = await self._db.fetch_all(
            "SELECT * FROM training_runs ORDER BY number DESC LIMIT ?", (limit,)
        )
        out = []
        for row in rows:
            report = json.loads(row["report"]) if row["report"] else {}
            holdout = report.get("holdout") or {}
            out.append(
                {
                    "number": row["number"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "status": row["status"],
                    "data_until": row["data_until"],
                    "verdict": row["verdict"],
                    "touches": (report.get("touches") or {}).get("decided"),
                    "holdout_touches": holdout.get("touches"),
                    "holdout_improvement": holdout.get("improvement"),
                    "holdout_t": holdout.get("t"),
                    "error": row["error"],
                    "seconds": row["seconds"],
                }
            )
        return out

    async def latest_report(self) -> tuple[int, str, dict[str, Any]] | None:
        """(run number, data_until, report) of the newest finished run."""
        row = await self._db.fetch_one(
            "SELECT number, data_until, report FROM training_runs WHERE status = 'done' "
            "ORDER BY number DESC LIMIT 1"
        )
        if row is None or not row["report"]:
            return None
        return int(row["number"]), str(row["data_until"]), json.loads(row["report"])

    # The model in use ------------------------------------------------------------------

    def save(self, bundle: Bundle) -> None:
        import sklearn

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        payload = {
            "format": FORMAT,
            "sklearn": sklearn.__version__,
            "features": list(FEATURES),
            "bundle": bundle,
        }
        with tmp.open("wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, self._path)

    def load(self) -> Bundle | None:
        if not self._path.exists():
            return None
        import sklearn  # also what unpickling the model needs

        try:
            with self._path.open("rb") as handle:
                payload = pickle.load(handle)  # JARVIS's own file, see the module docstring
        except Exception as exc:  # damaged or from an incompatible version
            log.warning("ignoring the saved model (%s)", type(exc).__name__)
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("format") != FORMAT
            or payload.get("sklearn") != sklearn.__version__
            or payload.get("features") != list(FEATURES)
            or not isinstance(payload.get("bundle"), Bundle)
        ):
            log.info("the saved model is from another version; it will be trained again")
            return None
        bundle: Bundle = payload["bundle"]
        return bundle
