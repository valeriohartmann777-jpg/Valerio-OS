"""The single, explicit operating state of JARVIS."""

from __future__ import annotations

import asyncio
import contextlib
from enum import StrEnum

from pydantic import BaseModel

from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity


class JarvisState(StrEnum):
    DORMANT = "DORMANT"
    LISTENING = "LISTENING"
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    THINKING = "THINKING"
    DELEGATING = "DELEGATING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    SPEAKING = "SPEAKING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


class StateSnapshot(BaseModel):
    state: JarvisState
    detail: str


class StateService:
    def __init__(self, bus: EventBus, *, settle_seconds: float) -> None:
        self._bus = bus
        self._settle_seconds = settle_seconds
        self._state = JarvisState.DORMANT
        self._detail = ""
        self._settle_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> JarvisState:
        return self._state

    def snapshot(self) -> StateSnapshot:
        return StateSnapshot(state=self._state, detail=self._detail)

    async def set(
        self, state: JarvisState, *, detail: str = "", ctx: TraceContext | None = None
    ) -> None:
        self._cancel_settle()
        if state is self._state and detail == self._detail:
            return
        previous = self._state
        self._state, self._detail = state, detail
        await self._bus.emit(
            EventType.JARVIS_STATE_CHANGED,
            message=f"{previous} → {state}" + (f" · {detail}" if detail else ""),
            severity=Severity.DEBUG,
            ctx=ctx,
            payload={"previous": previous, "state": state, "detail": detail},
        )
        if state in (JarvisState.COMPLETE, JarvisState.FAILED):
            self._settle_task = asyncio.create_task(self._settle(ctx))

    async def _settle(self, ctx: TraceContext | None) -> None:
        await asyncio.sleep(self._settle_seconds)
        self._settle_task = None
        await self.set(JarvisState.DORMANT, ctx=ctx)

    def _cancel_settle(self) -> None:
        task, self._settle_task = self._settle_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def close(self) -> None:
        task, self._settle_task = self._settle_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
