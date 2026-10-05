"""Shared helpers."""

from __future__ import annotations

import asyncio
import time

from jarvis.util import join_names, wait_wall


def test_join_names() -> None:
    assert join_names(["A"]) == "A"
    assert join_names(["A", "B", "C"]) == "A, B and C"


async def test_a_long_wait_ends_on_time_after_the_computer_slept() -> None:
    """The wall clock jumps ahead while a Mac sleeps; the monotonic clock doesn't."""
    readings = iter([0.0, 0.0, 9 * 3600.0])  # start, first slice, after "sleeping" 9 hours
    started = time.monotonic()
    woken = await wait_wall(asyncio.Event(), 8 * 3600, step=0.05, clock=lambda: next(readings))
    assert not woken
    assert time.monotonic() - started < 1.0


async def test_a_wait_ends_when_woken() -> None:
    event = asyncio.Event()
    asyncio.get_running_loop().call_later(0.05, event.set)
    assert await wait_wall(event, 3600)
    assert await wait_wall(event, 0)  # already set
    assert not await wait_wall(asyncio.Event(), 0.05, step=0.01)
