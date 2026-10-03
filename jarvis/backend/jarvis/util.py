"""Tiny shared helpers with no dependencies on the rest of the system."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def join_names(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
