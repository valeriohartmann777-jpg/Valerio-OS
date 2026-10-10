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
    # 9 — QuantLab: strategies, frozen versions and datasets, experiments, artifacts
    [
        """
        CREATE TABLE ql_strategies (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            hypothesis  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE ql_strategy_versions (
            id           TEXT PRIMARY KEY,
            strategy_id  TEXT NOT NULL REFERENCES ql_strategies(id),
            number       INTEGER NOT NULL,
            spec_json    TEXT NOT NULL,
            spec_sha256  TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            UNIQUE (strategy_id, number),
            UNIQUE (strategy_id, spec_sha256)
        )
        """,
        """
        CREATE TRIGGER ql_strategy_versions_immutable BEFORE UPDATE ON ql_strategy_versions
        BEGIN SELECT RAISE(ABORT, 'strategy versions are immutable'); END
        """,
        """
        CREATE TABLE ql_datasets (
            id                TEXT PRIMARY KEY,
            created_at        TEXT NOT NULL,
            status            TEXT NOT NULL,
            synthetic         INTEGER NOT NULL,
            symbol            TEXT NOT NULL,
            frequency         TEXT,
            filename          TEXT NOT NULL,
            original_sha256   TEXT NOT NULL,
            normalized_sha256 TEXT,
            snapshot_sha256   TEXT,
            rows              INTEGER NOT NULL,
            passport          TEXT NOT NULL,
            preview           TEXT NOT NULL
        )
        """,
        """
        CREATE TRIGGER ql_datasets_immutable BEFORE UPDATE ON ql_datasets
        BEGIN SELECT RAISE(ABORT, 'dataset snapshots are immutable'); END
        """,
        """
        CREATE TABLE ql_experiments (
            id                   TEXT PRIMARY KEY,
            strategy_version_id  TEXT NOT NULL REFERENCES ql_strategy_versions(id),
            dataset_id           TEXT NOT NULL REFERENCES ql_datasets(id),
            manifest             TEXT NOT NULL,
            manifest_sha256      TEXT NOT NULL,
            status               TEXT NOT NULL,
            stage                TEXT,
            created_at           TEXT NOT NULL,
            started_at           TEXT,
            finished_at          TEXT,
            attempts             INTEGER NOT NULL DEFAULT 0,
            error_code           TEXT,
            error                TEXT,
            verdict              TEXT,
            results_sha256       TEXT,
            summary              TEXT
        )
        """,
        "CREATE INDEX idx_ql_experiments_dataset ON ql_experiments(dataset_id)",
        """
        CREATE TABLE ql_artifacts (
            experiment_id  TEXT NOT NULL REFERENCES ql_experiments(id),
            kind           TEXT NOT NULL,
            relative_path  TEXT NOT NULL,
            sha256         TEXT NOT NULL,
            bytes          INTEGER NOT NULL,
            PRIMARY KEY (experiment_id, kind)
        )
        """,
        """
        CREATE TABLE ql_validations (
            experiment_id      TEXT NOT NULL REFERENCES ql_experiments(id),
            check_id           TEXT NOT NULL,
            gate               TEXT NOT NULL,
            method_version     TEXT NOT NULL,
            result             TEXT NOT NULL,
            observations       TEXT NOT NULL,
            metric             TEXT NOT NULL,
            assumptions        TEXT NOT NULL,
            evidence_artifact  TEXT,
            PRIMARY KEY (experiment_id, check_id)
        )
        """,
    ],
    # 10 — ULTRON: missions, tasks, agent runs, artifacts, approvals, mission event log
    [
        """
        CREATE TABLE ul_missions (
            id            TEXT PRIMARY KEY,
            number        INTEGER NOT NULL UNIQUE,
            title         TEXT NOT NULL,
            goal          TEXT NOT NULL,
            project_kind  TEXT NOT NULL,
            state         TEXT NOT NULL,
            charter       TEXT,
            plan_notes    TEXT,
            revision      INTEGER NOT NULL DEFAULT 0,
            budget_usd    REAL NOT NULL,
            spent_usd     REAL NOT NULL DEFAULT 0,
            workspace     TEXT NOT NULL,
            base_commit   TEXT,
            head_commit   TEXT,
            blocker       TEXT,
            report        TEXT,
            model_label   TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            finished_at   TEXT
        )
        """,
        """
        CREATE TABLE ul_tasks (
            id            TEXT PRIMARY KEY,
            mission_id    TEXT NOT NULL REFERENCES ul_missions(id),
            key           TEXT NOT NULL,
            title         TEXT NOT NULL,
            owner         TEXT NOT NULL,
            depends_on    TEXT NOT NULL,
            contract      TEXT NOT NULL,
            state         TEXT NOT NULL,
            attempts      INTEGER NOT NULL DEFAULT 0,
            max_attempts  INTEGER NOT NULL,
            feedback      TEXT,
            result        TEXT,
            cost_usd      REAL NOT NULL DEFAULT 0,
            started_at    TEXT,
            finished_at   TEXT,
            UNIQUE (mission_id, key)
        )
        """,
        """
        CREATE TABLE ul_runs (
            id             TEXT PRIMARY KEY,
            mission_id     TEXT NOT NULL REFERENCES ul_missions(id),
            task_id        TEXT,
            agent          TEXT NOT NULL,
            attempt        INTEGER NOT NULL,
            state          TEXT NOT NULL,
            model          TEXT,
            input_tokens   INTEGER NOT NULL DEFAULT 0,
            output_tokens  INTEGER NOT NULL DEFAULT 0,
            cost_usd       REAL NOT NULL DEFAULT 0,
            tool_calls     INTEGER NOT NULL DEFAULT 0,
            summary        TEXT,
            error          TEXT,
            started_at     TEXT NOT NULL,
            finished_at    TEXT
        )
        """,
        """
        CREATE TABLE ul_artifacts (
            id          TEXT PRIMARY KEY,
            mission_id  TEXT NOT NULL REFERENCES ul_missions(id),
            task_id     TEXT,
            run_id      TEXT,
            agent       TEXT NOT NULL,
            kind        TEXT NOT NULL,
            name        TEXT NOT NULL,
            path        TEXT NOT NULL,
            sha256      TEXT NOT NULL,
            bytes       INTEGER NOT NULL,
            created_at  TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE ul_approvals (
            id            TEXT PRIMARY KEY,
            mission_id    TEXT NOT NULL REFERENCES ul_missions(id),
            task_id       TEXT,
            category      TEXT NOT NULL,
            title         TEXT NOT NULL,
            effect        TEXT NOT NULL,
            signature     TEXT NOT NULL,
            state         TEXT NOT NULL,
            requested_by  TEXT NOT NULL,
            requested_at  TEXT NOT NULL,
            decided_at    TEXT,
            result        TEXT
        )
        """,
        """
        CREATE TABLE ul_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            at          TEXT NOT NULL,
            mission_id  TEXT,
            task_id     TEXT,
            agent       TEXT,
            kind        TEXT NOT NULL,
            severity    TEXT NOT NULL,
            message     TEXT NOT NULL,
            data        TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_ul_events_mission ON ul_events(mission_id, id)",
        "CREATE INDEX idx_ul_tasks_mission ON ul_tasks(mission_id)",
    ],
    # 11 — QuantLab Data Hub: provider connection (never the key), quotes, approved
    # download jobs, the day-partitioned cache, built datasets, spend caps, audit trail
    [
        """
        CREATE TABLE qh_connections (
            provider      TEXT PRIMARY KEY,
            status        TEXT NOT NULL,
            key_hint      TEXT,
            connected_at  TEXT,
            verified_at   TEXT,
            datasets      TEXT,
            error_code    TEXT,
            error         TEXT,
            fixture       INTEGER NOT NULL DEFAULT 0,
            updated_at    TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE qh_quotes (
            id                   TEXT PRIMARY KEY,
            provider             TEXT NOT NULL,
            created_at           TEXT NOT NULL,
            expires_at           TEXT NOT NULL,
            status               TEXT NOT NULL,
            request              TEXT NOT NULL,
            items                TEXT NOT NULL,
            cost_usd             REAL NOT NULL,
            billable_bytes       INTEGER NOT NULL,
            records              INTEGER NOT NULL,
            cached_days          INTEGER NOT NULL,
            conditions           TEXT NOT NULL,
            warnings             TEXT NOT NULL,
            signature            TEXT NOT NULL,
            fixture              INTEGER NOT NULL DEFAULT 0,
            decided_at           TEXT,
            approved_budget_usd  REAL,
            job_id               TEXT,
            note                 TEXT
        )
        """,
        """
        CREATE TABLE qh_jobs (
            id                   TEXT PRIMARY KEY,
            quote_id             TEXT NOT NULL REFERENCES qh_quotes(id),
            status               TEXT NOT NULL,
            created_at           TEXT NOT NULL,
            started_at           TEXT,
            finished_at          TEXT,
            approved_budget_usd  REAL NOT NULL,
            estimated_cost_usd   REAL NOT NULL,
            chunks_total         INTEGER NOT NULL,
            chunks_done          INTEGER NOT NULL DEFAULT 0,
            bytes                INTEGER NOT NULL DEFAULT 0,
            records              INTEGER NOT NULL DEFAULT 0,
            error_code           TEXT,
            error                TEXT,
            warnings             TEXT NOT NULL DEFAULT '[]',
            fixture              INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE qh_chunks (
            job_id      TEXT NOT NULL REFERENCES qh_jobs(id),
            idx         INTEGER NOT NULL,
            symbol      TEXT NOT NULL,
            schema      TEXT NOT NULL,
            stype_in    TEXT NOT NULL,
            dataset     TEXT NOT NULL,
            start       TEXT NOT NULL,
            end         TEXT NOT NULL,
            status      TEXT NOT NULL,
            raw_path    TEXT,
            raw_sha256  TEXT,
            bytes       INTEGER NOT NULL DEFAULT 0,
            records     INTEGER NOT NULL DEFAULT 0,
            error       TEXT,
            PRIMARY KEY (job_id, idx)
        )
        """,
        """
        CREATE TABLE qh_cache (
            cache_key          TEXT NOT NULL,
            day                TEXT NOT NULL,
            provider           TEXT NOT NULL,
            dataset            TEXT NOT NULL,
            schema             TEXT NOT NULL,
            stype_in           TEXT NOT NULL,
            symbol             TEXT NOT NULL,
            records            INTEGER NOT NULL,
            canonical_path     TEXT,
            sha256             TEXT,
            raw_path           TEXT NOT NULL,
            raw_sha256         TEXT NOT NULL,
            condition          TEXT,
            instrument_ids     TEXT NOT NULL,
            job_id             TEXT NOT NULL,
            transform_version  TEXT NOT NULL,
            fixture            INTEGER NOT NULL DEFAULT 0,
            created_at         TEXT NOT NULL,
            PRIMARY KEY (cache_key, day)
        )
        """,
        """
        CREATE TABLE qh_datasets (
            id               TEXT PRIMARY KEY,
            created_at       TEXT NOT NULL,
            provider         TEXT NOT NULL,
            dataset          TEXT NOT NULL,
            schema           TEXT NOT NULL,
            stype_in         TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            start            TEXT NOT NULL,
            end              TEXT NOT NULL,
            records          INTEGER NOT NULL,
            snapshot_path    TEXT NOT NULL,
            snapshot_sha256  TEXT NOT NULL,
            manifest         TEXT NOT NULL,
            quality          TEXT NOT NULL,
            fixture          INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TRIGGER qh_datasets_immutable BEFORE UPDATE ON qh_datasets
        BEGIN SELECT RAISE(ABORT, 'datasets are immutable'); END
        """,
        """
        CREATE TABLE qh_settings (
            provider            TEXT PRIMARY KEY,
            max_usd_per_request REAL NOT NULL,
            max_usd_per_month   REAL NOT NULL,
            updated_at          TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE qh_audit (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            at        TEXT NOT NULL,
            provider  TEXT NOT NULL,
            action    TEXT NOT NULL,
            detail    TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_qh_jobs_status ON qh_jobs(status)",
        "CREATE INDEX idx_qh_quotes_status ON qh_quotes(status)",
    ],
    # 12 — QuantLab research: futures strategies with insert-only versions, runs, the
    # trial registry (every evaluated variant), holdout unseals, notes and decisions
    [
        """
        CREATE TABLE qr_strategies (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            hypothesis  TEXT NOT NULL,
            product     TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE qr_versions (
            id           TEXT PRIMARY KEY,
            strategy_id  TEXT NOT NULL REFERENCES qr_strategies(id),
            number       INTEGER NOT NULL,
            parent_id    TEXT,
            spec_json    TEXT NOT NULL,
            spec_sha256  TEXT NOT NULL,
            origin       TEXT NOT NULL,
            note         TEXT,
            created_at   TEXT NOT NULL,
            UNIQUE (strategy_id, number),
            UNIQUE (strategy_id, spec_sha256)
        )
        """,
        """
        CREATE TRIGGER qr_versions_immutable BEFORE UPDATE ON qr_versions
        BEGIN SELECT RAISE(ABORT, 'strategy versions are immutable'); END
        """,
        """
        CREATE TABLE qr_runs (
            id               TEXT PRIMARY KEY,
            strategy_id      TEXT NOT NULL REFERENCES qr_strategies(id),
            version_id       TEXT NOT NULL REFERENCES qr_versions(id),
            dataset_id       TEXT NOT NULL,
            kind             TEXT NOT NULL,
            include_holdout  INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL,
            stage            TEXT,
            progress         REAL NOT NULL DEFAULT 0,
            manifest         TEXT NOT NULL,
            manifest_sha256  TEXT NOT NULL,
            verdict          TEXT,
            summary          TEXT,
            results_sha256   TEXT,
            error_code       TEXT,
            error            TEXT,
            fixture          INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL,
            started_at       TEXT,
            finished_at      TEXT
        )
        """,
        """
        CREATE TABLE qr_trials (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id   TEXT NOT NULL,
            run_id        TEXT NOT NULL,
            version_id    TEXT NOT NULL,
            params        TEXT NOT NULL,
            segment       TEXT NOT NULL,
            trades        INTEGER NOT NULL,
            net           REAL NOT NULL,
            sharpe_daily  REAL,
            created_at    TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE qr_holdouts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id  TEXT NOT NULL,
            product      TEXT NOT NULL,
            first_day    TEXT NOT NULL,
            last_day     TEXT NOT NULL,
            run_id       TEXT NOT NULL,
            at           TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE qr_notes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id  TEXT NOT NULL,
            run_id       TEXT,
            kind         TEXT NOT NULL,
            text         TEXT NOT NULL,
            created_at   TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_qr_runs_version ON qr_runs(version_id)",
        "CREATE INDEX idx_qr_trials_strategy ON qr_trials(strategy_id)",
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

    async def execute_count(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Execute and return the number of rows changed (for compare-and-set updates)."""
        cursor = await self.conn.execute(sql, params)
        count = cursor.rowcount
        await cursor.close()
        await self.conn.commit()
        return int(count)

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
