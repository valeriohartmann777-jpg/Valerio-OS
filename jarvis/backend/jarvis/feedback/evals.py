"""Behaviour evaluation: does JARVIS still understand what the owner says?

Curated cases (``config/evals.yaml``) plus cases the owner pinned from a 👍 reply run against
the local command router — offline, free and without side effects (nothing is opened or
changed). A run is stored with every case's result; the Training Hub shows the last one.
This checks understanding, not wording: requests handed to the AI route count as
``unsupported`` here, and their answers are judged through feedback instead.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import yaml

from jarvis.core.router import Router
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType
from jarvis.storage.database import Database
from jarvis.util import utcnow

from .service import FeedbackError

FIELDS = ("kind", "tool", "topic", "targets")
MAX_PINNED = 200


def load_cases(path: Path) -> list[dict[str, Any]]:
    """The curated cases; a missing or broken file means no curated cases, never a crash."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    cases = []
    for i, case in enumerate(data.get("cases") or []):
        if isinstance(case, dict) and case.get("text") and isinstance(case.get("expect"), dict):
            expect = {k: v for k, v in case["expect"].items() if k in FIELDS}
            cases.append(
                {"id": f"curated-{i + 1}", "text": str(case["text"]), "expect": expect}
                | {"source": "curated"}
            )
    return cases


def understood(router: Router, text: str) -> dict[str, Any]:
    intent = router.route(text)
    return {
        "kind": intent.kind.value,
        "tool": intent.tool,
        "topic": intent.topic,
        "targets": [t.lower() for t in intent.targets],
    }


def compare(expect: dict[str, Any], got: dict[str, Any]) -> list[str]:
    """The fields that differ (only fields the case names are checked)."""
    wrong = []
    for field, want in expect.items():
        have = got.get(field)
        if field == "targets":
            want = [str(t).lower() for t in want or []]
        if have != want:
            wrong.append(field)
    return wrong


class BehaviorEvals:
    def __init__(self, db: Database, router: Router, bus: EventBus, path: Path) -> None:
        self._db = db
        self._router = router
        self._bus = bus
        self._path = path

    async def cases(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all("SELECT * FROM ev_cases ORDER BY created_at")
        pinned = [
            {
                "id": r["id"],
                "text": r["text"],
                "expect": json.loads(r["expect"]),
                "source": "pinned",
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return load_cases(self._path) + pinned

    async def run(self) -> dict[str, Any]:
        results = []
        for case in await self.cases():
            got = understood(self._router, case["text"])
            wrong = compare(case["expect"], got)
            results.append(
                {
                    "id": case["id"],
                    "text": case["text"],
                    "source": case["source"],
                    "expect": case["expect"],
                    "got": got,
                    "passed": not wrong,
                    "wrong": wrong,
                }
            )
        passed = sum(1 for r in results if r["passed"])
        at = utcnow().isoformat()
        await self._db.execute(
            "INSERT INTO ev_runs (at, total, passed, results) VALUES (?, ?, ?, ?)",
            (at, len(results), passed, json.dumps(results)),
        )
        await self._bus.emit(
            EventType.EVALUATION_COMPLETED,
            message=f"Behaviour check: {passed} of {len(results)} cases understood as expected",
            payload={"total": len(results), "passed": passed},
        )
        return await self.overview()

    async def pin(self, event_id: str) -> dict[str, Any]:
        """Keep how JARVIS understood a 👍 reply as a regression case."""
        row = await self._db.fetch_one(
            "SELECT command, rating FROM fb_feedback WHERE event_id = ?", (event_id,)
        )
        if row is None or row["rating"] != 1:
            raise FeedbackError("NOT_APPROVED", "Only a reply you rated 👍 can become a case.")
        if not row["command"]:
            raise FeedbackError("NO_COMMAND", "The question for this reply wasn't recorded.")
        count = await self._db.fetch_one("SELECT COUNT(*) AS n FROM ev_cases")
        if count is not None and count["n"] >= MAX_PINNED:
            raise FeedbackError("TOO_MANY", f"At most {MAX_PINNED} pinned cases — remove one.")
        expect = understood(self._router, row["command"])
        expect = {k: v for k, v in expect.items() if v not in (None, [])}
        await self._db.execute(
            "INSERT INTO ev_cases (id, text, expect, feedback_event_id, created_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(feedback_event_id) DO NOTHING",
            (
                f"pin-{secrets.token_hex(4)}",
                row["command"],
                json.dumps(expect),
                event_id,
                utcnow().isoformat(),
            ),
        )
        return await self.overview()

    async def unpin(self, case_id: str) -> dict[str, Any]:
        removed = await self._db.execute_count("DELETE FROM ev_cases WHERE id = ?", (case_id,))
        if not removed:
            raise FeedbackError("NOT_FOUND", "There is no pinned case with that id.", 404)
        return await self.overview()

    async def overview(self) -> dict[str, Any]:
        last = await self._db.fetch_one("SELECT * FROM ev_runs ORDER BY id DESC LIMIT 1")
        cases = await self.cases()
        return {
            "cases": cases,
            "pinned": [c for c in cases if c["source"] == "pinned"],
            "pinned_events": [
                r["feedback_event_id"]
                for r in await self._db.fetch_all("SELECT feedback_event_id FROM ev_cases")
            ],
            "last_run": None
            if last is None
            else {
                "at": last["at"],
                "total": last["total"],
                "passed": last["passed"],
                "results": json.loads(last["results"]),
            },
        }
