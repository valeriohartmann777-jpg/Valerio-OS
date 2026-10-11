"""Data Vault records in SQLite (migration 16). No credential is ever written here."""

from __future__ import annotations

import json
from typing import Any

from jarvis.quantlab.hub.store import now
from jarvis.storage.database import Database

_JSON = {
    "qv_imports": ("preview", "mapping", "findings"),
    "qv_derived": ("stats",),
    "qv_backups": ("detail",),
    "qv_issues": ("detail",),
    "qv_acquisitions": ("request", "items", "quote", "job", "files"),
    "qv_approvals": ("detail",),
}
_BOOL = ("authoritative", "fixture")


def _row(table: str, row: Any) -> dict[str, Any]:
    out = dict(row)
    for name in _JSON.get(table, ()):
        if out.get(name) is not None:
            out[name] = json.loads(out[name])
    for name in _BOOL:
        if name in out:
            out[name] = bool(out[name])
    return out


class VaultStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def _insert(self, table: str, record: dict[str, Any]) -> None:
        values = {
            k: json.dumps(v, default=str) if k in _JSON.get(table, ()) else v
            for k, v in record.items()
        }
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        await self._db.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values())
        )

    async def _update(self, table: str, key: str, ident: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        values = {
            k: json.dumps(v, default=str) if k in _JSON.get(table, ()) else v
            for k, v in fields.items()
        }
        sets = ", ".join(f"{k} = ?" for k in values)
        await self._db.execute(
            f"UPDATE {table} SET {sets} WHERE {key} = ?", (*values.values(), ident)
        )

    # Imports ------------------------------------------------------------------------------

    async def add_import(self, record: dict[str, Any]) -> None:
        await self._insert("qv_imports", {"created_at": now(), **record})

    async def update_import(self, import_id: str, **fields: Any) -> None:
        await self._update("qv_imports", "id", import_id, fields)

    async def import_(self, import_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qv_imports WHERE id = ?", (import_id,))
        return _row("qv_imports", row) if row else None

    async def import_by_sha(self, sha: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM qv_imports WHERE raw_sha256 = ? AND status = 'COMMITTED' "
            "ORDER BY created_at LIMIT 1",
            (sha,),
        )
        return _row("qv_imports", row) if row else None

    async def imports(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qv_imports ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_row("qv_imports", r) for r in rows]

    # Derived ------------------------------------------------------------------------------

    async def derived(
        self, dataset_id: str, minutes: int, profile_sha: str, aggregator: str
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM qv_derived WHERE dataset_id = ? AND minutes = ? AND profile_sha = ? "
            "AND aggregator = ?",
            (dataset_id, minutes, profile_sha, aggregator),
        )
        return _row("qv_derived", row) if row else None

    async def save_derived(self, record: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO qv_derived (id, dataset_id, minutes, profile_sha, aggregator, "
            "path, file_sha256, data_sha256, bars, stats, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["id"],
                record["dataset_id"],
                record["minutes"],
                record["profile_sha"],
                record["aggregator"],
                record["path"],
                record["file_sha256"],
                record["data_sha256"],
                record["bars"],
                json.dumps(record["stats"]),
                now(),
            ),
        )

    async def derived_all(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM qv_derived ORDER BY created_at DESC")
        return [_row("qv_derived", r) for r in rows]

    async def delete_derived(self, derived_id: str) -> None:
        await self._db.execute("DELETE FROM qv_derived WHERE id = ?", (derived_id,))

    # Settings -----------------------------------------------------------------------------

    async def setting(self, key: str, default: Any = None) -> Any:
        row = await self._db.fetch_one("SELECT value FROM qv_settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    async def save_setting(self, key: str, value: Any) -> None:
        await self._db.execute(
            "INSERT INTO qv_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, json.dumps(value), now()),
        )

    # Backups ------------------------------------------------------------------------------

    async def add_backup(self, record: dict[str, Any]) -> None:
        await self._insert("qv_backups", {"created_at": now(), **record})

    async def update_backup(self, backup_id: str, **fields: Any) -> None:
        await self._update("qv_backups", "id", backup_id, fields)

    async def backups(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qv_backups ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_row("qv_backups", r) for r in rows]

    # Issues -------------------------------------------------------------------------------

    async def add_issue(self, kind: str, subject: str, detail: dict[str, Any]) -> None:
        existing = await self._db.fetch_one(
            "SELECT id FROM qv_issues WHERE kind = ? AND subject = ? AND resolved_at IS NULL",
            (kind, subject),
        )
        if existing is None:
            await self._db.execute(
                "INSERT INTO qv_issues (at, kind, subject, detail) VALUES (?, ?, ?, ?)",
                (now(), kind, subject, json.dumps(detail, default=str)),
            )

    async def resolve_issues(self, kind: str, subject: str) -> None:
        await self._db.execute(
            "UPDATE qv_issues SET resolved_at = ? WHERE kind = ? AND subject = ? "
            "AND resolved_at IS NULL",
            (now(), kind, subject),
        )

    async def open_issues(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qv_issues WHERE resolved_at IS NULL ORDER BY id DESC"
        )
        return [_row("qv_issues", r) for r in rows]

    # Acquisitions -------------------------------------------------------------------------

    async def add_acquisition(self, record: dict[str, Any]) -> None:
        stamp = now()
        await self._insert("qv_acquisitions", {"created_at": stamp, "updated_at": stamp, **record})

    async def acquisition(self, acquisition_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM qv_acquisitions WHERE id = ?", (acquisition_id,)
        )
        return _row("qv_acquisitions", row) if row else None

    async def acquisitions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qv_acquisitions ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_row("qv_acquisitions", r) for r in rows]

    async def acquisitions_with_status(self, *statuses: str) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in statuses)
        rows = await self._db.fetch_all(
            f"SELECT * FROM qv_acquisitions WHERE status IN ({marks}) ORDER BY created_at",
            statuses,
        )
        return [_row("qv_acquisitions", r) for r in rows]

    async def acquisition_by_hash(self, request_hash: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM qv_acquisitions WHERE request_hash = ? AND status NOT IN "
            "('EXPIRED', 'REJECTED', 'FAILED', 'CANCELED') ORDER BY created_at DESC LIMIT 1",
            (request_hash,),
        )
        return _row("qv_acquisitions", row) if row else None

    async def update_acquisition(self, acquisition_id: str, **fields: Any) -> None:
        await self._update("qv_acquisitions", "id", acquisition_id, {**fields, "updated_at": now()})

    async def transition_acquisition(
        self, acquisition_id: str, expected: tuple[str, ...], **fields: Any
    ) -> bool:
        """Compare-and-set on the status: exactly one caller wins (no double submission)."""
        values = {
            k: json.dumps(v, default=str) if k in _JSON["qv_acquisitions"] else v
            for k, v in {**fields, "updated_at": now()}.items()
        }
        sets = ", ".join(f"{k} = ?" for k in values)
        marks = ", ".join("?" for _ in expected)
        changed = await self._db.execute_count(
            f"UPDATE qv_acquisitions SET {sets} WHERE id = ? AND status IN ({marks})",
            (*values.values(), acquisition_id, *expected),
        )
        return changed == 1

    async def add_approval(self, record: dict[str, Any]) -> None:
        await self._insert("qv_approvals", {"at": now(), **record})

    async def approvals(self, acquisition_id: str | None = None) -> list[dict[str, Any]]:
        if acquisition_id:
            rows = await self._db.fetch_all(
                "SELECT * FROM qv_approvals WHERE acquisition_id = ? ORDER BY id",
                (acquisition_id,),
            )
        else:
            rows = await self._db.fetch_all("SELECT * FROM qv_approvals ORDER BY id DESC LIMIT 50")
        return [_row("qv_approvals", r) for r in rows]
