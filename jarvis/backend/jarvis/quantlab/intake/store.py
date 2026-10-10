"""Source records: sources, time-coded segments, keyframes, notes, claims, blueprints, audit."""

from __future__ import annotations

import json
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import utcnow

_SOURCE_JSON = ("meta",)
_UPDATABLE = {
    "status",
    "stage",
    "capability",
    "fallback",
    "title",
    "duration_ms",
    "language",
    "meta",
    "media_path",
    "media_expires_at",
    "media_deleted_at",
    "linked_source_id",
    "error",
}


def now() -> str:
    return utcnow().isoformat()


def _source(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["meta"] = json.loads(out["meta"] or "{}")
    return out


def _segment(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["flags"] = json.loads(out["flags"] or "[]")
    return out


def _claim(row: Any) -> dict[str, Any]:
    out = dict(row)
    for key in ("segment_ids", "field_mapping", "unresolved"):
        out[key] = json.loads(out[key])
    out["quote_verified"] = bool(out["quote_verified"])
    return out


def _blueprint(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["spec"] = json.loads(out.pop("spec_json"))
    for key in ("provenance", "ambiguities", "questions", "unsupported"):
        out[key] = json.loads(out[key])
    return out


class SourceStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # sources --------------------------------------------------------------------------------

    async def add(self, row: dict[str, Any]) -> None:
        stamp = now()
        data = {
            **row,
            "meta": json.dumps(row.get("meta") or {}),
            "created_at": stamp,
            "updated_at": stamp,
        }
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        await self._db.execute(
            f"INSERT INTO qs_sources ({cols}) VALUES ({marks})", tuple(data.values())
        )

    async def get(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qs_sources WHERE id = ?", (id_,))
        return _source(row) if row else None

    async def by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM qs_sources WHERE fingerprint = ?", (fingerprint,)
        )
        return _source(row) if row else None

    async def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qs_sources ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [_source(r) for r in rows]

    async def in_status(self, *statuses: str) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in statuses)
        rows = await self._db.fetch_all(
            f"SELECT * FROM qs_sources WHERE status IN ({marks}) ORDER BY created_at", statuses
        )
        return [_source(r) for r in rows]

    async def update(self, id_: str, **fields: Any) -> None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"not updatable: {sorted(unknown)}")
        values = {k: (json.dumps(v) if k in _SOURCE_JSON else v) for k, v in fields.items()}
        values["updated_at"] = now()
        sets = ", ".join(f"{k} = ?" for k in values)
        await self._db.execute(
            f"UPDATE qs_sources SET {sets} WHERE id = ?", (*values.values(), id_)
        )

    async def transition(
        self, id_: str, allowed: tuple[str, ...], status: str, **fields: Any
    ) -> bool:
        """Compare-and-set: change the status only if it is still one of ``allowed``."""
        values = {k: (json.dumps(v) if k in _SOURCE_JSON else v) for k, v in fields.items()}
        values["status"] = status
        values["updated_at"] = now()
        sets = ", ".join(f"{k} = ?" for k in values)
        marks = ", ".join("?" for _ in allowed)
        changed = await self._db.execute_count(
            f"UPDATE qs_sources SET {sets} WHERE id = ? AND status IN ({marks})",
            (*values.values(), id_, *allowed),
        )
        return changed == 1

    async def delete(self, id_: str) -> None:
        await self._db.execute("DELETE FROM qs_sources WHERE id = ?", (id_,))

    async def media_due(self, stamp: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qs_sources WHERE media_path IS NOT NULL "
            "AND media_expires_at IS NOT NULL AND media_expires_at <= ?",
            (stamp,),
        )
        return [_source(r) for r in rows]

    # segments and frames --------------------------------------------------------------------

    async def replace_segments(self, source_id: str, segments: list[dict[str, Any]]) -> None:
        await self._db.execute("DELETE FROM qs_segments WHERE source_id = ?", (source_id,))
        rows = [
            (
                f"{source_id}/s{seq}",
                source_id,
                seq,
                s["modality"],
                s.get("start_ms"),
                s.get("end_ms"),
                s.get("page"),
                s.get("char_start"),
                s.get("char_end"),
                s["text"],
                s.get("confidence"),
                s.get("quality", "ok"),
                s["provider"],
                s.get("frame_id"),
                json.dumps(s.get("flags") or []),
            )
            for seq, s in enumerate(segments, start=1)
        ]
        if rows:
            await self._db.execute_many(
                "INSERT INTO qs_segments (id, source_id, seq, modality, start_ms, end_ms, page, "
                "char_start, char_end, text, confidence, quality, provider, frame_id, flags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    async def segments(self, source_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qs_segments WHERE source_id = ? ORDER BY seq", (source_id,)
        )
        return [_segment(r) for r in rows]

    async def replace_frames(self, source_id: str, frames: list[dict[str, Any]]) -> None:
        await self._db.execute("DELETE FROM qs_frames WHERE source_id = ?", (source_id,))
        if frames:
            await self._db.execute_many(
                "INSERT INTO qs_frames (id, source_id, t_ms, path, sha256, width, height, "
                "scene_score, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        f["id"],
                        source_id,
                        f.get("t_ms"),
                        f["path"],
                        f["sha256"],
                        f.get("width"),
                        f.get("height"),
                        f.get("scene_score"),
                        f.get("text"),
                    )
                    for f in frames
                ],
            )

    async def frames(self, source_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qs_frames WHERE source_id = ? ORDER BY t_ms", (source_id,)
        )
        return [dict(r) for r in rows]

    async def frame(self, source_id: str, frame_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM qs_frames WHERE source_id = ? AND id = ?", (source_id, frame_id)
        )
        return dict(row) if row else None

    # notes ----------------------------------------------------------------------------------

    async def add_note(self, id_: str, source_id: str, text: str) -> None:
        await self._db.execute(
            "INSERT INTO qs_notes (id, source_id, text, created_at) VALUES (?, ?, ?, ?)",
            (id_, source_id, text, now()),
        )

    async def notes(self, source_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qs_notes WHERE source_id = ? ORDER BY created_at", (source_id,)
        )
        return [dict(r) for r in rows]

    # extractions and claims -----------------------------------------------------------------

    async def add_extraction(
        self, id_: str, source_id: str, kind: str, agent: str, model: str | None, inputs_hash: str
    ) -> None:
        await self._db.execute(
            "INSERT INTO qs_extractions (id, source_id, kind, agent, model, status, inputs_hash, "
            "started_at) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?)",
            (id_, source_id, kind, agent, model, inputs_hash, now()),
        )

    async def finish_extraction(
        self, id_: str, status: str, data: dict[str, Any] | None, error: str | None, cost: float
    ) -> None:
        await self._db.execute(
            "UPDATE qs_extractions SET status = ?, data = ?, error = ?, cost_usd = ?, "
            "finished_at = ? WHERE id = ?",
            (status, json.dumps(data) if data is not None else None, error, cost, now(), id_),
        )

    async def extractions(self, source_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qs_extractions WHERE source_id = ? ORDER BY started_at DESC",
            (source_id,),
        )
        out = []
        for r in rows:
            item = dict(r)
            item["data"] = json.loads(item["data"]) if item["data"] else None
            out.append(item)
        return out

    async def add_claims(
        self, source_id: str, extraction_id: str, claims: list[dict[str, Any]]
    ) -> None:
        stamp = now()
        await self._db.execute_many(
            "INSERT INTO qs_claims (id, source_id, extraction_id, seq, kind, content, quote, "
            "quote_verified, segment_ids, field_mapping, extraction_confidence, claim_status, "
            "unresolved, trading_truth, origin, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    c["id"],
                    source_id,
                    extraction_id,
                    seq,
                    c["kind"],
                    c["content"],
                    c.get("quote"),
                    int(bool(c.get("quote_verified"))),
                    json.dumps(c["segment_ids"]),
                    json.dumps(c["field_mapping"]),
                    c["extraction_confidence"],
                    c["claim_status"],
                    json.dumps(c["unresolved"]),
                    c.get("trading_truth", "NOT_TESTED"),
                    c.get("origin", "model"),
                    stamp,
                )
                for seq, c in enumerate(claims, start=1)
            ],
        )

    async def claims(
        self, source_id: str, extraction_id: str | None = None
    ) -> list[dict[str, Any]]:
        if extraction_id is None:
            row = await self._db.fetch_one(
                "SELECT extraction_id FROM qs_claims WHERE source_id = ? "
                "ORDER BY created_at DESC, seq LIMIT 1",
                (source_id,),
            )
            if row is None:
                return []
            extraction_id = str(row["extraction_id"])
        rows = await self._db.fetch_all(
            "SELECT * FROM qs_claims WHERE source_id = ? AND extraction_id = ? ORDER BY seq",
            (source_id, extraction_id),
        )
        return [_claim(r) for r in rows]

    # blueprints -----------------------------------------------------------------------------

    async def add_blueprint(self, row: dict[str, Any]) -> int:
        source_id = row.get("source_id")
        prev = await self._db.fetch_one(
            "SELECT MAX(number) AS n FROM qs_blueprints WHERE source_id IS ?", (source_id,)
        )
        number = 1 if prev is None or prev["n"] is None else int(prev["n"]) + 1
        await self._db.execute(
            "INSERT INTO qs_blueprints (id, source_id, number, parent_id, status, spec_json, "
            "spec_sha256, provenance, ambiguities, questions, unsupported, summary, origin, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                source_id,
                number,
                row.get("parent_id"),
                row["status"],
                json.dumps(row["spec"]),
                row.get("spec_sha256"),
                json.dumps(row["provenance"]),
                json.dumps(row["ambiguities"]),
                json.dumps(row["questions"]),
                json.dumps(row["unsupported"]),
                row.get("summary"),
                row["origin"],
                now(),
            ),
        )
        return number

    async def blueprint(self, id_: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM qs_blueprints WHERE id = ?", (id_,))
        return _blueprint(row) if row else None

    async def blueprints(self, source_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM qs_blueprints WHERE source_id = ? ORDER BY number DESC", (source_id,)
        )
        return [_blueprint(r) for r in rows]

    async def link_blueprint(self, id_: str, strategy_id: str, version_id: str) -> None:
        await self._db.execute(
            "UPDATE qs_blueprints SET strategy_id = ?, version_id = ? WHERE id = ?",
            (strategy_id, version_id, id_),
        )

    async def blueprint_for_version(self, version_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM qs_blueprints WHERE version_id = ? ORDER BY created_at DESC LIMIT 1",
            (version_id,),
        )
        return _blueprint(row) if row else None

    # audit ----------------------------------------------------------------------------------

    async def audit(self, source_id: str | None, action: str, detail: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO qs_audit (source_id, action, detail, at) VALUES (?, ?, ?, ?)",
            (source_id, action, json.dumps(detail, default=str), now()),
        )

    async def audit_log(
        self, source_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if source_id is None:
            rows = await self._db.fetch_all(
                "SELECT * FROM qs_audit ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM qs_audit WHERE source_id = ? ORDER BY id DESC LIMIT ?",
                (source_id, limit),
            )
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
