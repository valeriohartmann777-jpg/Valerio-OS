"""Import a macro calendar the owner provides (CSV) — point in time, DST-correct.

Columns (header row, any order): ``event``, ``scheduled`` (ISO-8601 with offset, or local time
with a ``timezone`` column such as ``America/New_York``), ``importance`` (high / medium / low),
optional ``category``, ``currency`` (default USD), ``schedule_known`` (when the schedule was
published — its vintage; default: 7 days before the release, recorded as an assumption),
``actual`` and ``released`` (when the actual value became public).

Nothing is fetched from the internet. A value is visible to an analysis only once released;
the schedule only from its known-at time. Rows that can't be placed in time are refused with
their reason.
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VINTAGE_DAYS = 7


def _when(value: str, tz: str | None) -> int:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        if not tz:
            raise ValueError("a time without offset needs a timezone column")
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return int(parsed.timestamp())


def parse_calendar(text: str, source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(rows to store, refused rows with their reason)."""
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for k, raw in enumerate(reader, start=2):
        r = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        try:
            if not r.get("event") or not r.get("scheduled"):
                raise ValueError("event and scheduled are required")
            tz = r.get("timezone") or None
            scheduled = _when(r["scheduled"], tz)
            assumed = not r.get("schedule_known")
            known = scheduled - VINTAGE_DAYS * 86400 if assumed else _when(r["schedule_known"], tz)
            released = _when(r["released"], tz) if r.get("released") else None
            if released is not None and released < scheduled - 3600:
                raise ValueError("released more than an hour before its schedule")
            importance = (r.get("importance") or "medium").lower()
            if importance not in ("high", "medium", "low"):
                raise ValueError("importance must be high, medium or low")
        except (ValueError, ZoneInfoNotFoundError, KeyError) as exc:
            refused.append({"line": k, "reason": str(exc), "row": r})
            continue
        ident = hashlib.sha1(f"{source}|{r['event']}|{scheduled}".encode()).hexdigest()[:16]
        rows.append({
            "id": "mx-" + ident,
            "source": source + (" (schedule vintage assumed 7 days ahead)" if assumed else ""),
            "event": r["event"],
            "category": r.get("category") or "macro",
            "importance": importance,
            "currency": (r.get("currency") or "USD").upper(),
            "scheduled_utc": scheduled,
            "schedule_known_utc": known,
            "actual": r.get("actual") or None,
            "released_utc": released,
        })  # fmt: skip
    return rows, refused
