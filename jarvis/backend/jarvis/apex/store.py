"""Persistence for APEX (``apex_*`` tables, migrations 20 and 21).

Candidates and signals are written once and never changed (database triggers); an outcome may
move from ``pending`` / ``open`` to a final state, and a final outcome is never changed again.
The trial register is append-only; a holdout period, once locked, is frozen, and each
strategy version opens it once.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from jarvis.storage.database import Database
from jarvis.util import utcnow


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class ApexStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # analyses -------------------------------------------------------------------------------

    async def add_analysis(self, analysis_id: str, analysis: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO apex_analyses "
            "(id, market, as_of, created_at, source, synthetic, data_sha256, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                analysis_id,
                analysis["market"],
                analysis["as_of"],
                utcnow().isoformat(),
                analysis["data"]["source"],
                1 if analysis["data"]["synthetic"] else 0,
                analysis["data"]["sha256"],
                json.dumps(analysis),
            ),
        )

    async def analysis(self, analysis_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT payload FROM apex_analyses WHERE id = ?", (analysis_id,)
        )
        return _loads(row["payload"]) if row else None

    async def analyses(self, market: str | None, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT id, market, as_of, created_at, source, synthetic FROM apex_analyses "
            + ("WHERE market = ? " if market else "")
            + "ORDER BY as_of DESC LIMIT ?",
            ((market, limit) if market else (limit,)),
        )
        return [dict(r) | {"synthetic": bool(r["synthetic"])} for r in rows]

    # candidates -----------------------------------------------------------------------------

    async def add_candidate(self, analysis_id: str, c: dict[str, Any]) -> bool:
        """Record a candidate once; returns False if it was already in the journal."""
        exists = await self._db.fetch_one("SELECT 1 FROM apex_candidates WHERE id = ?", (c["id"],))
        if exists:
            return False
        await self._db.execute(
            "INSERT INTO apex_candidates (id, analysis_id, market, template, direction, "
            "created_at, valid_until, entry, stop, target, synthetic, payload, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                c["id"],
                analysis_id,
                c["market"],
                c["template"],
                c["direction"],
                c["created_at"],
                c["valid_until"],
                c["entry"]["price"],
                c["stop"],
                c["targets"][0]["price"],
                1 if c.get("synthetic") else 0,
                json.dumps(c),
                utcnow().isoformat(),
            ),
        )
        await self.set_outcome(
            c["id"],
            {"state": "pending", "final": False, "r": None, "filled_at": None, "exit_at": None},
        )
        return True

    async def candidates(
        self, market: str | None = None, *, open_only: bool = False, limit: int = 200
    ) -> list[dict[str, Any]]:
        where, args = [], []
        if market:
            where.append("c.market = ?")
            args.append(market)
        if open_only:
            where.append("o.final = 0")
        sql = (
            "SELECT c.payload, c.analysis_id, o.payload AS outcome FROM apex_candidates c "
            "LEFT JOIN apex_outcomes o ON o.candidate_id = c.id "
            + ("WHERE " + " AND ".join(where) + " " if where else "")
            + "ORDER BY c.created_at DESC LIMIT ?"
        )
        rows = await self._db.fetch_all(sql, (*args, limit))
        return [
            _loads(r["payload"])
            | {"outcome": _loads(r["outcome"]), "analysis_id": r["analysis_id"]}
            for r in rows
        ]

    async def set_outcome(self, candidate_id: str, outcome: dict[str, Any]) -> None:
        current = await self._db.fetch_one(
            "SELECT final FROM apex_outcomes WHERE candidate_id = ?", (candidate_id,)
        )
        if current is not None and current["final"]:
            return
        values = (
            outcome["state"],
            1 if outcome.get("final") else 0,
            outcome.get("r"),
            outcome.get("filled_at"),
            outcome.get("exit_at"),
            json.dumps(outcome),
            utcnow().isoformat(),
        )
        if current is None:
            await self._db.execute(
                "INSERT INTO apex_outcomes (state, final, r, filled_at, exit_at, payload, "
                "checked_at, candidate_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, candidate_id),
            )
        else:
            await self._db.execute(
                "UPDATE apex_outcomes SET state = ?, final = ?, r = ?, filled_at = ?, "
                "exit_at = ?, payload = ?, checked_at = ? WHERE candidate_id = ?",
                (*values, candidate_id),
            )

    # signals --------------------------------------------------------------------------------

    async def add_signal(
        self, analysis_id: str, signal: dict[str, Any], *, synthetic: bool, replay: bool
    ) -> bool:
        """Record a signal once (same content id → already there); returns False then."""
        exists = await self._db.fetch_one(
            "SELECT 1 FROM apex_signals WHERE id = ?", (signal["signal_id"],)
        )
        if exists:
            return False
        await self._db.execute(
            "INSERT INTO apex_signals (id, analysis_id, market, strategy, version, status, "
            "direction, as_of, validation_state, synthetic, replay, candidate_id, payload, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                signal["signal_id"],
                analysis_id,
                signal["symbol"],
                signal["strategy_id"],
                signal["strategy_version"],
                signal["status"],
                signal["direction"],
                int(datetime.fromisoformat(signal["as_of_utc"]).timestamp()),
                signal["validation_state"],
                1 if synthetic else 0,
                1 if replay else 0,
                signal.get("candidate_id"),
                json.dumps(signal),
                utcnow().isoformat(),
            ),
        )
        return True

    async def signals(
        self,
        market: str | None = None,
        *,
        strategy: str | None = None,
        analysis_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where, args = [], []
        for column, value in (
            ("s.market", market), ("s.strategy", strategy), ("s.analysis_id", analysis_id),
        ):  # fmt: skip
            if value:
                where.append(f"{column} = ?")
                args.append(value)
        rows = await self._db.fetch_all(
            "SELECT s.payload, s.synthetic, s.replay, s.analysis_id, o.payload AS outcome "
            "FROM apex_signals s LEFT JOIN apex_outcomes o ON o.candidate_id = s.candidate_id "
            + ("WHERE " + " AND ".join(where) + " " if where else "")
            + "ORDER BY s.as_of DESC, s.id LIMIT ?",
            (*args, limit),
        )
        return [
            _loads(r["payload"])
            | {
                "synthetic": bool(r["synthetic"]),
                "replay": bool(r["replay"]),
                "analysis_id": r["analysis_id"],
                "outcome": _loads(r["outcome"]),
            }
            for r in rows
        ]

    # alerts -----------------------------------------------------------------------------------

    async def add_alert(self, alert: dict[str, Any]) -> bool:
        """Record a qualified paper alert once (immutable); False if it was already there."""
        return (
            await self._db.execute_count(
                "INSERT OR IGNORE INTO apex_alerts (id, market, trading_day, signal_id, "
                "candidate_id, setup, direction, zone_id, as_of, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alert["id"],
                    alert["market"],
                    alert["trading_day"],
                    alert["signal_id"],
                    alert["candidate_id"],
                    alert["setup"],
                    alert["direction"],
                    alert["zone_id"],
                    int(alert["as_of"]),
                    json.dumps(alert),
                    utcnow().isoformat(),
                ),
            )
            == 1
        )

    async def alerts(
        self, market: str | None = None, *, since: int | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        args: list[Any] = []
        if market:
            where.append("market = ?")
            args.append(market)
        if since is not None:
            where.append("as_of >= ?")
            args.append(since)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = await self._db.fetch_all(
            f"SELECT payload FROM apex_alerts{clause} ORDER BY as_of DESC LIMIT ?",
            (*args, limit),
        )
        return [_loads(r["payload"]) for r in rows]

    # probability heads ------------------------------------------------------------------------

    async def add_probability_model(self, model: dict[str, Any]) -> bool:
        """Store a fitted probability head once (immutable) — validated or not."""
        return (
            await self._db.execute_count(
                "INSERT OR IGNORE INTO apex_probability_models (id, market, setup, version, "
                "protocol_version, source_id, synthetic, validated, validation_id, payload, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    model["id"],
                    model["market"],
                    model["setup"],
                    model["version"],
                    model["protocol_version"],
                    model["source_id"],
                    int(bool(model["synthetic"])),
                    int(bool(model["validated"])),
                    model["validation_id"],
                    json.dumps(model),
                    utcnow().isoformat(),
                ),
            )
            == 1
        )

    async def probability_model(
        self, market: str, setup: str, version: str, source_id: str, synthetic: bool
    ) -> dict[str, Any] | None:
        """The latest head of this setup version on this source and kind of data."""
        row = await self._db.fetch_one(
            "SELECT payload FROM apex_probability_models WHERE market = ? AND setup = ? AND "
            "version = ? AND source_id = ? AND synthetic = ? ORDER BY created_at DESC, rowid "
            "DESC LIMIT 1",
            (market, setup, version, source_id, int(synthetic)),
        )
        return None if row is None else dict(_loads(row["payload"]))

    async def probability_model_by_id(self, model_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT payload FROM apex_probability_models WHERE id = ?", (model_id,)
        )
        return None if row is None else dict(_loads(row["payload"]))

    async def probability_models(
        self, market: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        where = " WHERE market = ?" if market else ""
        args: tuple[Any, ...] = (market, limit) if market else (limit,)
        rows = await self._db.fetch_all(
            f"SELECT payload FROM apex_probability_models{where} "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            args,
        )
        return [_loads(r["payload"]) for r in rows]

    # promotions -------------------------------------------------------------------------------

    async def add_promotion_event(self, event: dict[str, Any]) -> None:
        """Append one promotion event (immutable; the state is the last event)."""
        await self._db.execute(
            "INSERT INTO apex_promotions (id, model_id, market, setup, version, state, actor, "
            "payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["id"],
                event["model_id"],
                event["market"],
                event["setup"],
                event["version"],
                event["state"],
                event["actor"],
                json.dumps(event),
                utcnow().isoformat(),
            ),
        )

    async def promotion_events(self, model_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT payload FROM apex_promotions WHERE model_id = ? ORDER BY created_at, rowid",
            (model_id,),
        )
        return [_loads(r["payload"]) for r in rows]

    # zone snapshots ---------------------------------------------------------------------------

    async def add_zone_snapshot(
        self, snapshot_id: str, analysis_id: str, snapshot: dict[str, Any], sha256: str
    ) -> bool:
        """Store a zone snapshot once (immutable); False if it was already there."""
        return (
            await self._db.execute_count(
                "INSERT OR IGNORE INTO apex_zone_snapshots (id, analysis_id, market, as_of, "
                "version, sha256, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    analysis_id,
                    snapshot["market"],
                    int(snapshot["as_of"]),
                    snapshot["version"],
                    sha256,
                    json.dumps(snapshot),
                    utcnow().isoformat(),
                ),
            )
            == 1
        )

    async def zone_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT id, analysis_id, sha256, payload FROM apex_zone_snapshots WHERE id = ?",
            (snapshot_id,),
        )
        if row is None:
            return None
        payload: dict[str, Any] = _loads(row["payload"])
        return payload | {
            "snapshot_id": row["id"], "analysis_id": row["analysis_id"], "sha256": row["sha256"],
        }  # fmt: skip

    async def zone_snapshots(self, market: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT id, analysis_id, as_of, version, sha256 FROM apex_zone_snapshots "
            "WHERE market = ? ORDER BY as_of DESC LIMIT ?",
            (market, limit),
        )
        return [dict(r) for r in rows]

    # macro calendar ---------------------------------------------------------------------------

    async def add_macro_events(self, rows: list[dict[str, Any]]) -> int:
        added = 0
        for r in rows:
            added += await self._db.execute_count(
                "INSERT OR IGNORE INTO apex_macro_events (id, source, event, category, importance, "
                "currency, scheduled_utc, schedule_known_utc, actual, released_utc, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["id"], r["source"], r["event"], r["category"], r["importance"],
                    r["currency"], r["scheduled_utc"], r["schedule_known_utc"], r.get("actual"),
                    r.get("released_utc"), utcnow().isoformat(),
                ),
            )  # fmt: skip
        return added

    async def macro_events(self, start: int, end: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM apex_macro_events WHERE scheduled_utc >= ? AND scheduled_utc < ? "
            "ORDER BY scheduled_utc",
            (start, end),
        )
        return [dict(r) for r in rows]

    async def macro_count(self) -> int:
        row = await self._db.fetch_one("SELECT COUNT(*) AS n FROM apex_macro_events")
        return int(row["n"]) if row else 0

    # validation (docs/APEX_VALIDATION_PROTOCOL.md) ----------------------------------------

    async def add_validation(self, row: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO apex_validations (id, market, strategy, version, protocol_version, "
            "source_id, state, progress, synthetic, data_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', 0, ?, ?, ?)",
            (
                row["id"],
                row["market"],
                row["strategy"],
                row["version"],
                row["protocol_version"],
                row["source_id"],
                1 if row["synthetic"] else 0,
                row["data_sha256"],
                utcnow().isoformat(),
            ),
        )

    async def update_validation(self, validation_id: str, **fields: Any) -> None:
        if "result" in fields:
            fields["result"] = json.dumps(fields["result"])
        if fields.get("state") in ("COMPLETED", "FAILED", "CANCELLED"):
            fields["finished_at"] = utcnow().isoformat()
        cols = ", ".join(f'"{k}" = ?' for k in fields)
        await self._db.execute(
            f"UPDATE apex_validations SET {cols} WHERE id = ?", (*fields.values(), validation_id)
        )

    async def validation(self, validation_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM apex_validations WHERE id = ?", (validation_id,)
        )
        return self._validation(row) if row else None

    async def validations(self, market: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM apex_validations "
            + ("WHERE market = ? " if market else "")
            + "ORDER BY created_at DESC LIMIT ?",
            ((market, limit) if market else (limit,)),
        )
        return [self._validation(r) for r in rows]

    async def verdicts(self, market: str, synthetic: bool) -> dict[str, dict[str, Any]]:
        """Each strategy version's latest completed validation on real (or test) data."""
        rows = await self._db.fetch_all(
            "SELECT * FROM apex_validations WHERE market = ? AND synthetic = ? "
            "AND state = 'COMPLETED' ORDER BY finished_at",
            (market, 1 if synthetic else 0),
        )
        return {f"{r['strategy']}@{r['version']}": self._validation(r) for r in rows}

    async def fail_running_validations(self) -> int:
        return await self._db.execute_count(
            "UPDATE apex_validations SET state = 'FAILED', error = 'Interrupted by a restart.' "
            "WHERE state = 'RUNNING'"
        )

    @staticmethod
    def _validation(row: Any) -> dict[str, Any]:
        out = dict(row)
        out["synthetic"] = bool(out["synthetic"])
        out["result"] = _loads(out["result"])
        return out

    async def add_trial(self, row: dict[str, Any]) -> int:
        await self._db.execute(
            "INSERT INTO apex_trials (family, strategy, version, kind, params, validation_id, "
            "data_sha256, synthetic, summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["family"],
                row["strategy"],
                row["version"],
                row["kind"],
                json.dumps(row["params"], sort_keys=True),
                row["validation_id"],
                row["data_sha256"],
                1 if row["synthetic"] else 0,
                json.dumps(row["summary"]),
                utcnow().isoformat(),
            ),
        )
        found = await self._db.fetch_one("SELECT MAX(id) AS id FROM apex_trials")
        return int(found["id"]) if found else 0

    async def trials(self, family: str, synthetic: bool) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM apex_trials WHERE family = ? AND synthetic = ? ORDER BY id",
            (family, 1 if synthetic else 0),
        )
        return [
            dict(r) | {"params": _loads(r["params"]), "summary": _loads(r["summary"]),
                       "synthetic": bool(r["synthetic"])}
            for r in rows
        ]  # fmt: skip

    async def holdout(self, market: str, source_id: str, synthetic: bool) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            'SELECT market, source_id, synthetic, start, "end", locked_at FROM apex_holdouts '
            "WHERE market = ? AND source_id = ? AND synthetic = ?",
            (market, source_id, 1 if synthetic else 0),
        )
        return dict(row) | {"synthetic": bool(row["synthetic"])} if row else None

    async def lock_holdout(
        self, market: str, source_id: str, synthetic: bool, start: int, end: int
    ) -> dict[str, Any]:
        """Freeze the holdout period at the first validation run; later calls return it."""
        await self._db.execute(
            'INSERT OR IGNORE INTO apex_holdouts (market, source_id, synthetic, start, "end", '
            "locked_at) VALUES (?, ?, ?, ?, ?, ?)",
            (market, source_id, 1 if synthetic else 0, start, end, utcnow().isoformat()),
        )
        found = await self.holdout(market, source_id, synthetic)
        assert found is not None
        return found

    async def holdout_opened(
        self, market: str, source_id: str, synthetic: bool, strategy: str, version: str
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM apex_holdout_opens WHERE market = ? AND source_id = ? AND synthetic = ? "
            "AND strategy = ? AND version = ?",
            (market, source_id, 1 if synthetic else 0, strategy, version),
        )
        return dict(row) | {"result": _loads(row["result"])} if row else None

    async def open_holdout(self, row: dict[str, Any]) -> bool:
        """Record the one opening of a holdout for a strategy version; False if it was opened."""
        return (
            await self._db.execute_count(
                "INSERT OR IGNORE INTO apex_holdout_opens (market, source_id, synthetic, "
                "strategy, version, validation_id, result, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["market"],
                    row["source_id"],
                    1 if row["synthetic"] else 0,
                    row["strategy"],
                    row["version"],
                    row["validation_id"],
                    json.dumps(row["result"]),
                    utcnow().isoformat(),
                ),
            )
            == 1
        )

    # audits ---------------------------------------------------------------------------------

    async def add_audit(
        self, audit_id: str, analysis_id: str, market: str, passed: bool, result: dict[str, Any]
    ) -> None:
        now = utcnow().isoformat()
        await self._db.execute(
            "INSERT INTO apex_audits (id, analysis_id, market, state, passed, result, created_at, "
            "finished_at) VALUES (?, ?, ?, 'COMPLETED', ?, ?, ?, ?)",
            (audit_id, analysis_id, market, 1 if passed else 0, json.dumps(result), now, now),
        )

    async def audit(self, audit_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM apex_audits WHERE id = ?", (audit_id,))
        return self._audit(row) if row else None

    async def audits(self, market: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM apex_audits "
            + ("WHERE market = ? " if market else "")
            + "ORDER BY created_at DESC LIMIT ?",
            ((market, limit) if market else (limit,)),
        )
        return [self._audit(r) for r in rows]

    @staticmethod
    def _audit(row: Any) -> dict[str, Any]:
        out = dict(row)
        out["passed"] = None if out["passed"] is None else bool(out["passed"])
        out["result"] = _loads(out["result"])
        return out

    async def hq_rows(self, since: str) -> list[dict[str, Any]]:
        """Track records (CIPHER) and re-checks (SENTINEL) running or finished since ``since``."""
        runs = await self._db.fetch_all(
            'SELECT id, market, start, "end", state, finished_at, error FROM apex_evaluations '
            "WHERE state = 'RUNNING' OR finished_at >= ?",
            (since,),
        )
        checks = await self._db.fetch_all(
            "SELECT id, market, analysis_id, passed, finished_at FROM apex_audits "
            "WHERE finished_at >= ?",
            (since,),
        )
        validations = await self._db.fetch_all(
            "SELECT id, market, strategy, version, state, finished_at, error "
            "FROM apex_validations WHERE state = 'RUNNING' OR finished_at >= ?",
            (since,),
        )
        out = [
            {
                "agent": "cipher",
                "id": r["id"],
                "market": r["market"],
                "state": r["state"],
                "title": f"Track record of the zone rule {r['start']} → {r['end']}",
                "finished_at": r["finished_at"],
                "error": r["error"],
            }
            for r in runs
        ]
        out += [
            {
                "agent": "cipher",
                "id": r["id"],
                "market": r["market"],
                "state": r["state"],
                "title": f"Validation of {r['strategy']} {r['version']} (pre-registered protocol)",
                "finished_at": r["finished_at"],
                "error": r["error"],
            }
            for r in validations
        ]
        out += [
            {
                "agent": "sentinel",
                "id": r["id"],
                "market": r["market"],
                "state": "COMPLETED" if r["passed"] else "FAILED",
                "title": f"Re-check of analysis {r['analysis_id']}",
                "finished_at": r["finished_at"],
                "error": None if r["passed"] else "A check failed — see the audit.",
            }
            for r in checks
        ]
        return out

    # evaluations ----------------------------------------------------------------------------

    async def add_evaluation(self, row: dict[str, Any]) -> None:
        await self._db.execute(
            'INSERT INTO apex_evaluations (id, market, template, start, "end", state, progress, '
            "synthetic, created_at) VALUES (?, ?, ?, ?, ?, 'RUNNING', 0, ?, ?)",
            (
                row["id"],
                row["market"],
                row["template"],
                row["start"],
                row["end"],
                1 if row["synthetic"] else 0,
                utcnow().isoformat(),
            ),
        )

    async def update_evaluation(self, evaluation_id: str, **fields: Any) -> None:
        if "result" in fields:
            fields["result"] = json.dumps(fields["result"])
        if fields.get("state") in ("COMPLETED", "FAILED"):
            fields["finished_at"] = utcnow().isoformat()
        cols = ", ".join(f'"{k}" = ?' for k in fields)
        await self._db.execute(
            f"UPDATE apex_evaluations SET {cols} WHERE id = ?",
            (*fields.values(), evaluation_id),
        )

    async def evaluation(self, evaluation_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM apex_evaluations WHERE id = ?", (evaluation_id,)
        )
        return self._evaluation(row) if row else None

    async def evaluations(self, market: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM apex_evaluations "
            + ("WHERE market = ? " if market else "")
            + "ORDER BY created_at DESC LIMIT ?",
            ((market, limit) if market else (limit,)),
        )
        return [self._evaluation(r, full=False) for r in rows]

    async def latest_completed(
        self, market: str, template: str | None = None
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM apex_evaluations WHERE market = ? AND state = 'COMPLETED' "
            + ("AND template = ? " if template else "")
            + "ORDER BY finished_at DESC LIMIT 1",
            (market, template) if template else (market,),
        )
        return self._evaluation(row) if row else None

    async def fail_running(self) -> int:
        return await self._db.execute_count(
            "UPDATE apex_evaluations SET state = 'FAILED', error = 'Interrupted by a restart.' "
            "WHERE state = 'RUNNING'"
        )

    @staticmethod
    def _evaluation(row: Any, *, full: bool = True) -> dict[str, Any]:
        out = dict(row)
        out["synthetic"] = bool(out["synthetic"])
        result = _loads(out.pop("result"))
        if result is not None and not full:
            result = {k: v for k, v in result.items() if k != "trades"}
        out["result"] = result
        return out
