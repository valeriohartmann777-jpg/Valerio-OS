"""SQLite access with versioned migrations.

All SQL lives in ``storage/`` and the repositories. Moving to PostgreSQL means
replacing this module and the repositories, nothing else.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger("jarvis.storage")

MIGRATIONS: list[list[str]] = [
    # 1 — foundation
    [
        """
        CREATE TABLE missions (
            id          TEXT PRIMARY KEY,
            number      INTEGER NOT NULL UNIQUE,
            status      TEXT NOT NULL,
            title       TEXT NOT NULL,
            trace_id    TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            data        TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_missions_status ON missions(status)",
        """
        CREATE TABLE events (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            severity    TEXT NOT NULL,
            source      TEXT NOT NULL,
            message     TEXT NOT NULL,
            trace_id    TEXT,
            mission_id  TEXT,
            payload     TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_events_timestamp ON events(timestamp)",
        """
        CREATE TABLE audit_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT NOT NULL,
            trace_id         TEXT,
            mission_id       TEXT,
            agent            TEXT,
            tool             TEXT NOT NULL,
            arguments        TEXT NOT NULL,
            permission_level INTEGER NOT NULL,
            approval         TEXT NOT NULL,
            success          INTEGER NOT NULL,
            summary          TEXT NOT NULL,
            simulated        INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX idx_audit_timestamp ON audit_log(timestamp)",
    ],
    # 2: the conversation is read by event type
    ["CREATE INDEX idx_events_type_timestamp ON events(type, timestamp)"],
    # 3: self-directed learning (rounds, backtests, knowledge notes)
    [
        """
        CREATE TABLE learning_rounds (
            id            TEXT PRIMARY KEY,
            number        INTEGER NOT NULL UNIQUE,
            day           TEXT NOT NULL,
            started_at    TEXT NOT NULL,
            finished_at   TEXT,
            status        TEXT NOT NULL,
            summary       TEXT NOT NULL DEFAULT '',
            next_focus    TEXT NOT NULL DEFAULT '',
            cost_usd      REAL NOT NULL DEFAULT 0,
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            searches      INTEGER NOT NULL DEFAULT 0,
            error         TEXT
        )
        """,
        "CREATE INDEX idx_learning_rounds_day ON learning_rounds(day)",
        """
        CREATE TABLE learning_tests (
            id                TEXT PRIMARY KEY,
            number            INTEGER NOT NULL UNIQUE,
            round_id          TEXT,
            created_at        TEXT NOT NULL,
            name              TEXT NOT NULL,
            instrument        TEXT,
            style             TEXT,
            timeframe         TEXT,
            status            TEXT NOT NULL,
            reason            TEXT NOT NULL,
            spec              TEXT NOT NULL,
            in_sample         TEXT,
            out_of_sample     TEXT,
            holdout           TEXT,
            holdout_confirmed INTEGER,
            t_required        REAL
        )
        """,
        "CREATE INDEX idx_learning_tests_status ON learning_tests(status)",
        """
        CREATE TABLE learning_notes (
            id          TEXT PRIMARY KEY,
            number      INTEGER NOT NULL UNIQUE,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            topic       TEXT NOT NULL,
            text        TEXT NOT NULL,
            sources     TEXT NOT NULL,
            active      INTEGER NOT NULL DEFAULT 1,
            round_id    TEXT
        )
        """,
    ],
    # 4: level studies (how support and resistance behave, in-sample)
    [
        """
        CREATE TABLE learning_studies (
            id          TEXT PRIMARY KEY,
            number      INTEGER NOT NULL UNIQUE,
            round_id    TEXT,
            created_at  TEXT NOT NULL,
            name        TEXT NOT NULL,
            instrument  TEXT,
            ok          INTEGER NOT NULL,
            spec        TEXT NOT NULL,
            result      TEXT NOT NULL
        )
        """,
    ],
    # 5: long-term memory of the user
    [
        """
        CREATE TABLE memories (
            id          TEXT PRIMARY KEY,
            number      INTEGER NOT NULL UNIQUE,
            kind        TEXT NOT NULL,
            text        TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            source      TEXT NOT NULL,
            active      INTEGER NOT NULL DEFAULT 1
        )
        """,
    ],
    # 6: the holdout confirms only significant results (t >= 1.645), not merely positive ones
    [
        """
        UPDATE learning_tests SET holdout_confirmed = CASE
            WHEN json_extract(holdout, '$.avg_r') > 0
             AND json_extract(holdout, '$.profit_factor') > 1.0
             AND json_extract(holdout, '$.t_stat') >= 1.6448536269514722
            THEN holdout_confirmed ELSE 0 END
        WHERE holdout IS NOT NULL
        """,
    ],
    # 7: training runs of JARVIS's own support/resistance model
    [
        """
        CREATE TABLE training_runs (
            id          TEXT PRIMARY KEY,
            number      INTEGER NOT NULL UNIQUE,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            status      TEXT NOT NULL,
            data_until  TEXT,
            verdict     TEXT,
            report      TEXT,
            error       TEXT,
            seconds     REAL
        )
        """,
    ],
    # 8: Bot Lab — the user's MetaTrader EAs, JARVIS's versions of them, backtests
    [
        """
        CREATE TABLE bots (
            name        TEXT PRIMARY KEY,
            path        TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            settings    TEXT NOT NULL,
            stall_since TEXT
        )
        """,
        """
        CREATE TABLE bot_versions (
            id          TEXT PRIMARY KEY,
            bot         TEXT NOT NULL,
            number      INTEGER NOT NULL,
            parent      INTEGER,
            created_at  TEXT NOT NULL,
            title       TEXT NOT NULL,
            hypothesis  TEXT,
            source      TEXT NOT NULL,
            compiled    INTEGER NOT NULL,
            errors      TEXT,
            diff        TEXT,
            UNIQUE (bot, number)
        )
        """,
        """
        CREATE TABLE bot_tests (
            id                TEXT PRIMARY KEY,
            bot               TEXT NOT NULL,
            number            INTEGER NOT NULL,
            version           INTEGER NOT NULL,
            inputs            TEXT NOT NULL,
            settings          TEXT NOT NULL,
            origin            TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            finished_at       TEXT,
            status            TEXT NOT NULL,
            error             TEXT,
            in_sample         TEXT,
            out_of_sample     TEXT,
            holdout           TEXT,
            unseen            TEXT,
            validated         INTEGER,
            validation_reason TEXT,
            holdout_confirmed INTEGER,
            seconds           REAL,
            UNIQUE (bot, number)
        )
        """,
        """
        CREATE TABLE bot_notes (
            id          TEXT PRIMARY KEY,
            bot         TEXT NOT NULL,
            number      INTEGER NOT NULL,
            created_at  TEXT NOT NULL,
            text        TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE bot_rounds (
            id          TEXT PRIMARY KEY,
            bot         TEXT NOT NULL,
            number      INTEGER NOT NULL,
            day         TEXT NOT NULL,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            status      TEXT NOT NULL,
            cost_usd    REAL NOT NULL DEFAULT 0,
            summary     TEXT,
            error       TEXT
        )
        """,
    ],
]


class Database:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def connect(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _migrate(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = await self.fetch_one("SELECT MAX(version) AS v FROM schema_version")
        current = int(row["v"]) if row and row["v"] is not None else 0
        for version, statements in enumerate(MIGRATIONS, start=1):
            if version <= current:
                continue
            for statement in statements:
                await self.conn.execute(statement)
            await self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            await self.conn.commit()
            log.info("database migrated", extra={"schema_version": version})

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await self.conn.execute(sql, params)
        await self.conn.commit()

    async def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        await self.conn.executemany(sql, rows)
        await self.conn.commit()

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cursor:
            row: aiosqlite.Row | None = await cursor.fetchone()
            return row

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())
