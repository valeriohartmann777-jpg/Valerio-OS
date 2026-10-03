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
