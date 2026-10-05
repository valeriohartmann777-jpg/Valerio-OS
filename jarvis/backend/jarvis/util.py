"""Tiny shared helpers with no dependencies on the rest of the system."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
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


async def wait_wall(
    event: asyncio.Event,
    seconds: float,
    *,
    step: float = 60.0,
    clock: Callable[[], float] = time.time,
) -> bool:
    """Wait until ``event`` is set or ``seconds`` of wall-clock time have passed.

    asyncio's timers run on a monotonic clock, which stands still while a Mac
    sleeps: "wait until midnight" started in the evening would end hours late
    the next morning. So the wait is cut into ``step`` slices and checked
    against the wall clock. Returns whether the event was set."""
    deadline = clock() + max(0.0, seconds)
    while (remaining := deadline - clock()) > 0:
        try:
            await asyncio.wait_for(event.wait(), timeout=min(step, remaining))
            return True
        except TimeoutError:
            continue
    return event.is_set()
