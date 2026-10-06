"""Bot Lab records (SQLite): bots, versions, backtests, notes, research rounds."""

from __future__ import annotations

import json
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import new_id, utcnow


def _now() -> str:
    return utcnow().isoformat()


def _load(text: str | None) -> Any:
    return json.loads(text) if text else None


class BotStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # Bots -------------------------------------------------------------------------------

    async def add_bot(self, name: str, path: str, settings: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO bots (name, path, imported_at, settings) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET path = excluded.path",
            (name, path, _now(), json.dumps(settings)),
        )

    async def bot(self, name: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM bots WHERE name = ?", (name,))
        if row is None:
            return None
        return {**dict(row), "settings": _load(row["settings"])}

    async def bots(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM bots ORDER BY name")
        return [{**dict(r), "settings": _load(r["settings"])} for r in rows]

    async def set_settings(self, name: str, settings: dict[str, Any]) -> None:
        await self._db.execute(
            "UPDATE bots SET settings = ? WHERE name = ?", (json.dumps(settings), name)
        )

    async def set_stall_since(self, name: str, moment: str) -> None:
        await self._db.execute("UPDATE bots SET stall_since = ? WHERE name = ?", (moment, name))

    # Versions ---------------------------------------------------------------------------

    async def add_version(
        self,
        bot: str,
        *,
        parent: int | None,
        title: str,
        hypothesis: str,
        source: str,
        compiled: bool,
        errors: list[str],
        diff: dict[str, int] | None,
    ) -> int:
        row = await self._db.fetch_one(
            "SELECT MAX(number) AS n FROM bot_versions WHERE bot = ?", (bot,)
        )
        number = 0 if row is None or row["n"] is None else int(row["n"]) + 1
        await self._db.execute(
            "INSERT INTO bot_versions (id, bot, number, parent, created_at, title, hypothesis, "
            "source, compiled, errors, diff) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                bot,
                number,
                parent,
                _now(),
                title,
                hypothesis,
                source,
                int(compiled),
                json.dumps(errors),
                json.dumps(diff) if diff else None,
            ),
        )
        return number

    async def set_compiled(self, bot: str, number: int, ok: bool, errors: list[str]) -> None:
        await self._db.execute(
            "UPDATE bot_versions SET compiled = ?, errors = ? WHERE bot = ? AND number = ?",
            (int(ok), json.dumps(errors), bot, number),
        )

    async def set_source(self, bot: str, number: int, source: str) -> None:
        await self._db.execute(
            "UPDATE bot_versions SET source = ? WHERE bot = ? AND number = ?",
            (source, bot, number),
        )

    async def version(self, bot: str, number: int) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM bot_versions WHERE bot = ? AND number = ?", (bot, number)
        )
        return _version(row) if row else None

    async def versions(self, bot: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM bot_versions WHERE bot = ? ORDER BY number", (bot,)
        )
        return [_version(r, with_source=False) for r in rows]

    # Tests ------------------------------------------------------------------------------

    async def start_test(
        self,
        bot: str,
        version: int,
        inputs: dict[str, str],
        settings: dict[str, Any],
        origin: str,
    ) -> tuple[str, int]:
        row = await self._db.fetch_one(
            "SELECT MAX(number) AS n FROM bot_tests WHERE bot = ?", (bot,)
        )
        number = 1 if row is None or row["n"] is None else int(row["n"]) + 1
        test_id = new_id()
        await self._db.execute(
            "INSERT INTO bot_tests (id, bot, number, version, inputs, settings, origin, "
            "created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')",
            (
                test_id,
                bot,
                number,
                version,
                json.dumps(inputs),
                json.dumps(settings),
                origin,
                _now(),
            ),
        )
        return test_id, number

    async def finish_test(
        self,
        test_id: str,
        *,
        status: str,
        seconds: float,
        error: str | None = None,
        periods: dict[str, Any] | None = None,
    ) -> None:
        periods = periods or {}
        await self._db.execute(
            "UPDATE bot_tests SET status = ?, finished_at = ?, seconds = ?, error = ?, "
            "in_sample = ?, out_of_sample = ?, holdout = ?, unseen = ? WHERE id = ?",
            (
                status,
                _now(),
                round(seconds, 1),
                error,
                *(
                    json.dumps(periods[k]) if periods.get(k) else None
                    for k in ("in_sample", "out_of_sample", "holdout", "unseen")
                ),
                test_id,
            ),
        )

    async def set_validation(
        self, bot: str, number: int, *, validated: bool, reason: str, confirmed: bool | None
    ) -> None:
        await self._db.execute(
            "UPDATE bot_tests SET validated = ?, validation_reason = ?, holdout_confirmed = ? "
            "WHERE bot = ? AND number = ?",
            (
                int(validated),
                reason,
                None if confirmed is None else int(confirmed),
                bot,
                number,
            ),
        )

    async def validations(self, bot: str) -> int:
        """Out-of-sample checks made for this bot (the bar rises with each)."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM bot_tests WHERE bot = ? AND validated IS NOT NULL",
            (bot,),
        )
        return int(row["n"]) if row else 0

    async def test(self, bot: str, number: int) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM bot_tests WHERE bot = ? AND number = ?", (bot, number)
        )
        return _test(row) if row else None

    async def tests(self, bot: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM bot_tests WHERE bot = ? ORDER BY number DESC LIMIT ?", (bot, limit)
        )
        return [_test(r) for r in rows]

    async def interrupted(self) -> int:
        rows = await self._db.fetch_all("SELECT id FROM bot_tests WHERE status = 'running'")
        await self._db.execute(
            "UPDATE bot_tests SET status = 'failed', error = 'interrupted', finished_at = ? "
            "WHERE status = 'running'",
            (_now(),),
        )
        await self._db.execute(
            "UPDATE bot_rounds SET status = 'interrupted', finished_at = ? "
            "WHERE status = 'running'",
            (_now(),),
        )
        return len(rows)

    # Notes ------------------------------------------------------------------------------

    async def add_note(self, bot: str, text: str) -> int:
        row = await self._db.fetch_one(
            "SELECT MAX(number) AS n FROM bot_notes WHERE bot = ?", (bot,)
        )
        number = 1 if row is None or row["n"] is None else int(row["n"]) + 1
        await self._db.execute(
            "INSERT INTO bot_notes (id, bot, number, created_at, text) VALUES (?, ?, ?, ?, ?)",
            (new_id(), bot, number, _now(), text),
        )
        return number

    async def notes(self, bot: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT number, created_at, text FROM bot_notes WHERE bot = ? ORDER BY number",
            (bot,),
        )
        return [dict(r) for r in rows]

    # Rounds -----------------------------------------------------------------------------

    async def start_round(self, bot: str, day: str) -> tuple[str, int]:
        row = await self._db.fetch_one(
            "SELECT MAX(number) AS n FROM bot_rounds WHERE bot = ?", (bot,)
        )
        number = 1 if row is None or row["n"] is None else int(row["n"]) + 1
        round_id = new_id()
        await self._db.execute(
            "INSERT INTO bot_rounds (id, bot, number, day, started_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'running')",
            (round_id, bot, number, day, _now()),
        )
        return round_id, number

    async def add_cost(self, round_id: str, cost: float) -> None:
        await self._db.execute(
            "UPDATE bot_rounds SET cost_usd = cost_usd + ? WHERE id = ?", (cost, round_id)
        )

    async def finish_round(
        self, round_id: str, status: str, summary: str = "", error: str | None = None
    ) -> None:
        await self._db.execute(
            "UPDATE bot_rounds SET status = ?, finished_at = ?, summary = ?, error = ? "
            "WHERE id = ?",
            (status, _now(), summary, error, round_id),
        )

    async def spent_on(self, day: str) -> float:
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM bot_rounds WHERE day = ?", (day,)
        )
        return float(row["c"]) if row else 0.0

    async def rounds(self, bot: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT number, day, started_at, finished_at, status, cost_usd, summary, error "
            "FROM bot_rounds WHERE bot = ? ORDER BY number DESC LIMIT ?",
            (bot, limit),
        )
        return [dict(r) for r in rows]

    async def rounds_without_progress(self, bot: str, since: str | None) -> int:
        """Completed rounds after the last validated test (or ``since``)."""
        row = await self._db.fetch_one(
            "SELECT MAX(created_at) AS t FROM bot_tests WHERE bot = ? AND validated = 1", (bot,)
        )
        last = max(filter(None, [row["t"] if row else None, since]), default="")
        count = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM bot_rounds WHERE bot = ? AND status = 'completed' "
            "AND started_at > ?",
            (bot, last),
        )
        return int(count["n"]) if count else 0


def _version(row: Any, *, with_source: bool = True) -> dict[str, Any]:
    out = {
        "number": row["number"],
        "parent": row["parent"],
        "created_at": row["created_at"],
        "title": row["title"],
        "hypothesis": row["hypothesis"],
        "compiled": bool(row["compiled"]),
        "errors": _load(row["errors"]) or [],
        "diff": _load(row["diff"]),
    }
    if with_source:
        out["source"] = row["source"]
    return out


def _test(row: Any) -> dict[str, Any]:
    return {
        "number": row["number"],
        "version": row["version"],
        "inputs": _load(row["inputs"]) or {},
        "settings": _load(row["settings"]) or {},
        "origin": row["origin"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "status": row["status"],
        "error": row["error"],
        "in_sample": _load(row["in_sample"]),
        "out_of_sample": _load(row["out_of_sample"]),
        "holdout": _load(row["holdout"]),
        "unseen": _load(row["unseen"]),
        "validated": None if row["validated"] is None else bool(row["validated"]),
        "validation_reason": row["validation_reason"],
        "holdout_confirmed": None
        if row["holdout_confirmed"] is None
        else bool(row["holdout_confirmed"]),
        "seconds": row["seconds"],
    }
