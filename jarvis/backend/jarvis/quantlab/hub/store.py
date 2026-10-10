"""Data Hub records in SQLite. No credential is ever written here — only a hint."""

from __future__ import annotations

import json
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import utcnow


def now() -> str:
    return utcnow().isoformat()


def _row(row: Any, *json_fields: str) -> dict[str, Any]:
    out = dict(row)
    for name in json_fields:
        if out.get(name) is not None:
            out[name] = json.loads(out[name])
    for name in ("fixture",):
        if name in out:
            out[name] = bool(out[name])
    return out


class HubStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # Connection -------------------------------------------------------------------------

    async def connection(self, provider: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM qh_connections WHERE provider = ?", (provider,)
        )
        return _row(row, "datasets") if row else None

    async def save_connection(self, provider: str, **fields: Any) -> None:
        current = await self.connection(provider) or {}
        merged = {**current, **fields}
        datasets = merged.get("datasets")
        await self._db.execute(
            "INSERT INTO qh_connections (provider, status, key_hint, connected_at, verified_at, "
            "datasets, error_code, error, fixture, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET status = excluded.status, "
            "key_hint = excluded.key_hint, connected_at = excluded.connected_at, "
            "verified_at = excluded.verified_at, datasets = excluded.datasets, "
            "error_code = excluded.error_code, error = excluded.error, "
            "fixture = excluded.fixture, updated_at = excluded.updated_at",
            (
                provider,
                merged.get("status", "NOT_CONNECTED"),
                merged.get("key_hint"),
                merged.get("connected_at"),
                merged.get("verified_at"),
                json.dumps(datasets) if datasets is not None else None,
                merged.get("error_code"),
                merged.get("error"),
                int(bool(merged.get("fixture"))),
                now(),
            ),
        )

    # Settings ---------------------------------------------------------------------------

    async def settings(self, provider: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qh_settings WHERE provider = ?", (provider,))
        return dict(row) if row else None

    async def save_settings(self, provider: str, per_request: float, per_month: float) -> None:
        await self._db.execute(
            "INSERT INTO qh_settings (provider, max_usd_per_request, max_usd_per_month, "
            "updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(provider) DO UPDATE SET "
            "max_usd_per_request = excluded.max_usd_per_request, "
            "max_usd_per_month = excluded.max_usd_per_month, updated_at = excluded.updated_at",
            (provider, per_request, per_month, now()),
        )

    # Quotes -----------------------------------------------------------------------------

    async def add_quote(self, quote: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO qh_quotes (id, provider, created_at, expires_at, status, request, items, "
            "cost_usd, billable_bytes, records, cached_days, conditions, warnings, signature, "
            "fixture) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                quote["id"],
                quote["provider"],
                quote["created_at"],
                quote["expires_at"],
                json.dumps(quote["request"]),
                json.dumps(quote["items"]),
                quote["cost_usd"],
                quote["billable_bytes"],
                quote["records"],
                quote["cached_days"],
                json.dumps(quote["conditions"]),
                json.dumps(quote["warnings"]),
                quote["signature"],
                int(quote["fixture"]),
            ),
        )

    async def quote(self, quote_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qh_quotes WHERE id = ?", (quote_id,))
        return _row(row, "request", "items", "conditions", "warnings") if row else None

    async def quotes(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qh_quotes ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_row(r, "request", "items", "conditions", "warnings") for r in rows]

    async def claim_quote(self, quote_id: str, budget: float, at: str) -> bool:
        """Compare-and-set OPEN → APPROVED: an approval can be used exactly once."""
        changed = await self._db.execute_count(
            "UPDATE qh_quotes SET status = 'APPROVED', decided_at = ?, approved_budget_usd = ? "
            "WHERE id = ? AND status = 'OPEN' AND expires_at > ?",
            (at, budget, quote_id, at),
        )
        return changed == 1

    async def close_quote(self, quote_id: str, status: str, note: str | None = None) -> bool:
        changed = await self._db.execute_count(
            "UPDATE qh_quotes SET status = ?, decided_at = ?, note = ? "
            "WHERE id = ? AND status = 'OPEN'",
            (status, now(), note, quote_id),
        )
        return changed == 1

    async def link_quote(self, quote_id: str, job_id: str) -> None:
        await self._db.execute("UPDATE qh_quotes SET job_id = ? WHERE id = ?", (job_id, quote_id))

    async def expire_quotes(self, at: str) -> None:
        await self._db.execute(
            "UPDATE qh_quotes SET status = 'EXPIRED' WHERE status = 'OPEN' AND expires_at <= ?",
            (at,),
        )

    # Jobs -------------------------------------------------------------------------------

    async def add_job(self, job: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
        await self._db.execute(
            "INSERT INTO qh_jobs (id, quote_id, status, created_at, approved_budget_usd, "
            "estimated_cost_usd, chunks_total, fixture) VALUES (?, ?, 'QUEUED', ?, ?, ?, ?, ?)",
            (
                job["id"],
                job["quote_id"],
                now(),
                job["approved_budget_usd"],
                job["estimated_cost_usd"],
                len(chunks),
                int(job["fixture"]),
            ),
        )
        await self._db.execute_many(
            "INSERT INTO qh_chunks (job_id, idx, symbol, schema, stype_in, dataset, start, end, "
            "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')",
            [
                (
                    job["id"],
                    i,
                    c["symbol"],
                    c["schema"],
                    c["stype_in"],
                    c["dataset"],
                    c["start"],
                    c["end"],
                )
                for i, c in enumerate(chunks)
            ],
        )

    async def job(self, job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qh_jobs WHERE id = ?", (job_id,))
        return _row(row, "warnings") if row else None

    async def jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qh_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_row(r, "warnings") for r in rows]

    async def jobs_with_status(self, *statuses: str) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in statuses)
        rows = await self._db.fetch_all(
            f"SELECT * FROM qh_jobs WHERE status IN ({marks}) ORDER BY created_at", statuses
        )
        return [_row(r, "warnings") for r in rows]

    async def update_job(self, job_id: str, **fields: Any) -> None:
        if "warnings" in fields:
            fields["warnings"] = json.dumps(fields["warnings"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(
            f"UPDATE qh_jobs SET {sets} WHERE id = ?", (*fields.values(), job_id)
        )

    async def transition_job(self, job_id: str, expected: tuple[str, ...], **fields: Any) -> bool:
        """Compare-and-set on the job status: True only if the job was in ``expected``."""
        sets = ", ".join(f"{k} = ?" for k in fields)
        marks = ",".join("?" for _ in expected)
        changed = await self._db.execute_count(
            f"UPDATE qh_jobs SET {sets} WHERE id = ? AND status IN ({marks})",
            (*fields.values(), job_id, *expected),
        )
        return changed == 1

    async def chunks(self, job_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qh_chunks WHERE job_id = ? ORDER BY idx", (job_id,)
        )
        return [dict(r) for r in rows]

    async def update_chunk(self, job_id: str, idx: int, **fields: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(
            f"UPDATE qh_chunks SET {sets} WHERE job_id = ? AND idx = ?",
            (*fields.values(), job_id, idx),
        )

    async def approved_since(self, since: str) -> float:
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS s FROM qh_jobs WHERE created_at >= ?",
            (since,),
        )
        return float(row["s"]) if row else 0.0

    # Cache ------------------------------------------------------------------------------

    async def add_cache_days(self, rows: list[dict[str, Any]]) -> None:
        await self._db.execute_many(
            "INSERT OR REPLACE INTO qh_cache (cache_key, day, provider, dataset, schema, "
            "stype_in, symbol, records, canonical_path, sha256, raw_path, raw_sha256, "
            "condition, instrument_ids, job_id, transform_version, fixture, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["cache_key"],
                    r["day"],
                    r["provider"],
                    r["dataset"],
                    r["schema"],
                    r["stype_in"],
                    r["symbol"],
                    r["records"],
                    r["canonical_path"],
                    r["sha256"],
                    r["raw_path"],
                    r["raw_sha256"],
                    r["condition"],
                    json.dumps(r["instrument_ids"]),
                    r["job_id"],
                    r["transform_version"],
                    int(r["fixture"]),
                    now(),
                )
                for r in rows
            ],
        )

    async def cached_days(self, cache_key: str, start: str, end: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qh_cache WHERE cache_key = ? AND day >= ? AND day < ? ORDER BY day",
            (cache_key, start, end),
        )
        return [_row(r, "instrument_ids") for r in rows]

    async def cache_summary(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT cache_key, provider, dataset, schema, stype_in, symbol, MIN(day) AS first_day, "
            "MAX(day) AS last_day, COUNT(*) AS days, SUM(records) AS records, "
            "SUM(CASE WHEN condition IS NOT NULL AND condition != 'available' THEN 1 ELSE 0 END) "
            "AS flagged_days, MAX(fixture) AS fixture FROM qh_cache "
            "GROUP BY cache_key ORDER BY dataset, symbol, schema"
        )
        return [_row(r) for r in rows]

    # Datasets ---------------------------------------------------------------------------

    async def add_dataset(self, record: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO qh_datasets (id, created_at, provider, dataset, schema, stype_in, symbol, "
            "start, end, records, snapshot_path, snapshot_sha256, manifest, quality, fixture) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["id"],
                now(),
                record["provider"],
                record["dataset"],
                record["schema"],
                record["stype_in"],
                record["symbol"],
                record["start"],
                record["end"],
                record["records"],
                record["snapshot_path"],
                record["snapshot_sha256"],
                json.dumps(record["manifest"]),
                json.dumps(record["quality"]),
                int(record["fixture"]),
            ),
        )

    async def dataset(self, dataset_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qh_datasets WHERE id = ?", (dataset_id,))
        return _row(row, "manifest", "quality") if row else None

    async def datasets(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM qh_datasets ORDER BY created_at DESC")
        return [_row(r, "manifest", "quality") for r in rows]

    # Audit ------------------------------------------------------------------------------

    async def audit(self, provider: str, action: str, detail: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO qh_audit (at, provider, action, detail) VALUES (?, ?, ?, ?)",
            (now(), provider, action, json.dumps(detail, default=str)),
        )

    async def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM qh_audit ORDER BY id DESC LIMIT ?", (limit,))
        return [_row(r, "detail") for r in rows]
