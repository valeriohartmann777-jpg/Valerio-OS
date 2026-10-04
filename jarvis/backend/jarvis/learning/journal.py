"""What JARVIS has learned: rounds, backtests and knowledge notes (SQLite).

Holdout results are stored here for the user and never leave through
``prompt_tests`` — that is what the research model reads.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from jarvis.learning.backtest import Stats
from jarvis.learning.evaluate import Outcome
from jarvis.storage.database import Database
from jarvis.util import new_id, utcnow


def _now() -> str:
    return utcnow().isoformat()


def _stats(stats: Stats | None) -> str | None:
    return json.dumps(asdict(stats)) if stats is not None else None


def _load(text: str | None) -> Any:
    return json.loads(text) if text else None


class LearningJournal:
    def __init__(self, db: Database) -> None:
        self._db = db

    # Rounds ----------------------------------------------------------------------------

    async def interrupted(self) -> int:
        """Rounds left 'running' by a crash or quit become 'interrupted'."""
        rows = await self._db.fetch_all("SELECT id FROM learning_rounds WHERE status = 'running'")
        await self._db.execute(
            "UPDATE learning_rounds SET status = 'interrupted', finished_at = ? "
            "WHERE status = 'running'",
            (_now(),),
        )
        return len(rows)

    async def start_round(self, day: str) -> tuple[str, int]:
        row = await self._db.fetch_one("SELECT MAX(number) AS n FROM learning_rounds")
        number = (int(row["n"]) if row and row["n"] is not None else 0) + 1
        round_id = new_id()
        await self._db.execute(
            "INSERT INTO learning_rounds (id, number, day, started_at, status) "
            "VALUES (?, ?, ?, ?, 'running')",
            (round_id, number, day, _now()),
        )
        return round_id, number

    async def add_usage(
        self, round_id: str, *, cost: float, input_tokens: int, output_tokens: int, searches: int
    ) -> None:
        await self._db.execute(
            "UPDATE learning_rounds SET cost_usd = cost_usd + ?, input_tokens = input_tokens + ?, "
            "output_tokens = output_tokens + ?, searches = searches + ? WHERE id = ?",
            (cost, input_tokens, output_tokens, searches, round_id),
        )

    async def finish_round(
        self,
        round_id: str,
        status: str,
        *,
        summary: str = "",
        next_focus: str = "",
        error: str | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE learning_rounds SET status = ?, summary = ?, next_focus = ?, error = ?, "
            "finished_at = ? WHERE id = ?",
            (status, summary, next_focus, error, _now(), round_id),
        )

    async def spent_on(self, day: str) -> float:
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM learning_rounds WHERE day = ?", (day,)
        )
        return float(row["spent"]) if row else 0.0

    async def rounds(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM learning_rounds ORDER BY number DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    async def rounds_without_progress(self, since: str | None) -> int:
        """Completed rounds after ``since`` and after the last validated test."""
        row = await self._db.fetch_one(
            "SELECT MAX(created_at) AS at FROM learning_tests WHERE status = 'validated'"
        )
        marks = [m for m in (since, row["at"] if row else None) if m]
        after = max(marks) if marks else ""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM learning_rounds "
            "WHERE status = 'completed' AND started_at > ?",
            (after,),
        )
        return int(row["n"]) if row else 0

    async def last_focus(self) -> str:
        row = await self._db.fetch_one(
            "SELECT next_focus FROM learning_rounds WHERE status = 'completed' "
            "ORDER BY number DESC LIMIT 1"
        )
        return str(row["next_focus"]) if row else ""

    # Tests -----------------------------------------------------------------------------

    async def add_test(
        self, round_id: str | None, name: str, spec: dict[str, Any], outcome: Outcome
    ) -> int:
        row = await self._db.fetch_one("SELECT MAX(number) AS n FROM learning_tests")
        number = (int(row["n"]) if row and row["n"] is not None else 0) + 1
        await self._db.execute(
            "INSERT INTO learning_tests (id, number, round_id, created_at, name, instrument, "
            "style, timeframe, status, reason, spec, in_sample, out_of_sample, holdout, "
            "holdout_confirmed, t_required) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                number,
                round_id,
                _now(),
                name[:80],
                spec.get("instrument"),
                spec.get("style"),
                spec.get("timeframe"),
                outcome.status,
                outcome.reason,
                json.dumps(spec),
                _stats(outcome.in_sample),
                _stats(outcome.out_of_sample),
                _stats(outcome.holdout),
                None if outcome.holdout_confirmed is None else int(outcome.holdout_confirmed),
                outcome.t_required,
            ),
        )
        return number

    async def oos_evaluations(self) -> int:
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM learning_tests WHERE out_of_sample IS NOT NULL"
        )
        return int(row["n"]) if row else 0

    async def tests(
        self, limit: int = 50, before: int | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Full records for the user, holdout included."""
        sql = "SELECT * FROM learning_tests WHERE number < ?"
        params: list[Any] = [before or 2**31]
        if status:
            sql += " AND status = ?"
            params.append(status)
        rows = await self._db.fetch_all(sql + " ORDER BY number DESC LIMIT ?", (*params, limit))
        return [_test(row) for row in rows]

    async def prompt_tests(self, limit: int) -> list[dict[str, Any]]:
        """What the research model may see: in-sample results and out-of-sample
        pass/fail. Never out-of-sample numbers, never the holdout."""
        rows = await self._db.fetch_all(
            "SELECT number, name, instrument, style, timeframe, status, reason, spec, in_sample "
            "FROM learning_tests ORDER BY number DESC LIMIT ?",
            (limit,),
        )
        out = []
        for row in reversed(rows):
            stats = _load(row["in_sample"])
            entry: dict[str, Any] = {
                "test": f"T{row['number']}",
                "name": row["name"],
                "market": f"{row['instrument']} {row['style']} {row['timeframe']}",
                "result": row["status"],
                "why": row["reason"],
                "strategy": _load(row["spec"]),
            }
            if stats:
                entry["in_sample"] = _brief(stats)
            out.append(entry)
        return out

    async def validated_for_prompt(self, limit: int = 30) -> list[dict[str, Any]]:
        """Validated findings without any out-of-sample or holdout numbers."""
        rows = await self._db.fetch_all(
            "SELECT number, name, instrument, style, timeframe, in_sample FROM learning_tests "
            "WHERE status = 'validated' ORDER BY number LIMIT ?",
            (limit,),
        )
        return [{**dict(row), "in_sample": _load(row["in_sample"])} for row in rows]

    async def counts(self) -> dict[str, int]:
        rows = await self._db.fetch_all(
            "SELECT status, COUNT(*) AS n FROM learning_tests GROUP BY status"
        )
        by_status = {row["status"]: int(row["n"]) for row in rows}
        confirmed = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM learning_tests WHERE holdout_confirmed = 1"
        )
        notes = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM learning_notes WHERE active = 1"
        )
        rounds = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM learning_rounds WHERE status = 'completed'"
        )
        studies = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM learning_studies WHERE ok = 1"
        )
        return {
            "tests": sum(by_status.values()),
            "invalid": by_status.get("invalid", 0),
            "rejected": by_status.get("rejected", 0),
            "in_sample_passed": by_status.get("oos_failed", 0) + by_status.get("validated", 0),
            "validated": by_status.get("validated", 0),
            "confirmed": int(confirmed["n"]) if confirmed else 0,
            "notes": int(notes["n"]) if notes else 0,
            "rounds": int(rounds["n"]) if rounds else 0,
            "studies": int(studies["n"]) if studies else 0,
        }

    # Level studies ---------------------------------------------------------------------

    async def add_study(
        self,
        round_id: str | None,
        name: str,
        spec: dict[str, Any],
        result: dict[str, Any],
        *,
        ok: bool = True,
    ) -> int:
        row = await self._db.fetch_one("SELECT MAX(number) AS n FROM learning_studies")
        number = (int(row["n"]) if row and row["n"] is not None else 0) + 1
        await self._db.execute(
            "INSERT INTO learning_studies (id, number, round_id, created_at, name, instrument, "
            "ok, spec, result) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                number,
                round_id,
                _now(),
                name[:80],
                spec.get("instrument"),
                int(ok),
                json.dumps(spec),
                json.dumps(result),
            ),
        )
        return number

    async def studies(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT number, created_at, name, instrument, ok, spec, result FROM learning_studies "
            "ORDER BY number DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                **dict(row),
                "ok": bool(row["ok"]),
                "spec": _load(row["spec"]),
                "result": _load(row["result"]),
            }
            for row in rows
        ]

    async def prompt_studies(self, limit: int) -> list[dict[str, Any]]:
        """Recent studies, compact, oldest first (they are in-sample only)."""
        out = []
        for study in reversed(await self.studies(limit)):
            spec, result = study["spec"] or {}, study["result"] or {}
            entry: dict[str, Any] = {
                "study": f"S{study['number']}",
                "name": study["name"],
                "setup": {
                    k: spec.get(k)
                    for k in ("instrument", "timeframe", "side", "level", "when", "session")
                    if spec.get(k)
                },
            }
            if study["ok"]:
                entry["result"] = {
                    k: result.get(k)
                    for k in (
                        "touches",
                        "broken_on_touch",
                        "held_rate",
                        "expected_rate",
                        "edge_z",
                        "by_touch",
                        "by_year",
                        "avg_favourable_points",
                        "avg_adverse_points",
                    )
                }
            else:
                entry["error"] = result.get("error")
            out.append(entry)
        return out

    # Notes -----------------------------------------------------------------------------

    async def add_note(
        self,
        topic: str,
        text: str,
        sources: list[str],
        *,
        round_id: str | None,
        replaces: int | None = None,
    ) -> int:
        row = await self._db.fetch_one("SELECT MAX(number) AS n FROM learning_notes")
        number = (int(row["n"]) if row and row["n"] is not None else 0) + 1
        now = _now()
        if replaces is not None:
            await self._db.execute(
                "UPDATE learning_notes SET active = 0, updated_at = ? WHERE number = ?",
                (now, replaces),
            )
        await self._db.execute(
            "INSERT INTO learning_notes (id, number, created_at, updated_at, topic, text, "
            "sources, active, round_id) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (new_id(), number, now, now, topic, text, json.dumps(sources), round_id),
        )
        return number

    async def note_exists(self, number: int) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 AS x FROM learning_notes WHERE number = ? AND active = 1", (number,)
        )
        return row is not None

    async def notes(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT number, created_at, updated_at, topic, text, sources FROM learning_notes "
            "WHERE active = 1 ORDER BY number DESC LIMIT ?",
            (limit,),
        )
        return [{**dict(row), "sources": _load(row["sources"]) or []} for row in rows]


def _brief(stats: dict[str, Any]) -> dict[str, Any]:
    keys = ("trades", "win_rate", "avg_r", "t_stat", "profit_factor", "max_drawdown_r")
    out: dict[str, Any] = {
        k: round(stats[k], 3) if isinstance(stats[k], float) else stats[k] for k in keys
    }
    out["by_year_avg_r"] = {y: v["avg_r"] for y, v in stats.get("by_year", {}).items()}
    out["exits"] = stats.get("exits", {})
    return out


def _test(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ("spec", "in_sample", "out_of_sample", "holdout"):
        data[key] = _load(data[key])
    if data["holdout_confirmed"] is not None:
        data["holdout_confirmed"] = bool(data["holdout_confirmed"])
    return data
