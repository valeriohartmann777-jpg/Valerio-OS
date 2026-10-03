"""Environment context: what is happening on the computer right now.

Samples cheap signals (CPU, memory, foreground window) on a slow interval and
publishes ``context.updated`` only when something changed. No screenshots are
taken here — vision is explicit, permission-aware and event-driven (Phase 5).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import platform
from datetime import datetime

from pydantic import BaseModel, Field

from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.tools.system.apps import AppCatalog
from jarvis.tools.system.backend import SystemBackend
from jarvis.tools.system.metrics import prime_cpu_counter, read_metrics
from jarvis.util import utcnow

log = logging.getLogger("jarvis.context")


class EnvironmentContext(BaseModel):
    hostname: str
    platform: str
    system_backend: str
    simulated: bool
    active_app: str | None
    active_window: str | None
    cpu_percent: float
    memory_percent: float
    memory_used_gb: float
    memory_total_gb: float
    uptime_seconds: int
    updated_at: datetime = Field(default_factory=utcnow)

    def fingerprint(self) -> tuple[object, ...]:
        return (
            self.active_app,
            self.active_window,
            round(self.cpu_percent),
            round(self.memory_percent),
        )


class EnvironmentContextService:
    def __init__(
        self, bus: EventBus, backend: SystemBackend, catalog: AppCatalog, *, interval: float
    ) -> None:
        self._bus = bus
        self._backend = backend
        self._catalog = catalog
        self._interval = interval
        self._current: EnvironmentContext | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def current(self) -> EnvironmentContext | None:
        return self._current

    async def start(self) -> None:
        prime_cpu_counter()
        await self.refresh()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def refresh(self) -> EnvironmentContext:
        metrics = read_metrics()
        window = await self._backend.active_window()
        context = EnvironmentContext(
            hostname=metrics.hostname,
            platform=platform_label(),
            system_backend=self._backend.name,
            simulated=self._backend.simulated,
            active_app=self._catalog.window_label(window) if window else None,
            active_window=window.title if window else None,
            cpu_percent=metrics.cpu_percent,
            memory_percent=metrics.memory_percent,
            memory_used_gb=metrics.memory_used_gb,
            memory_total_gb=metrics.memory_total_gb,
            uptime_seconds=metrics.uptime_seconds,
        )
        changed = self._current is None or context.fingerprint() != self._current.fingerprint()
        self._current = context
        if changed:
            await self._bus.emit(
                EventType.CONTEXT_UPDATED,
                message="Environment context updated",
                source="system",
                severity=Severity.DEBUG,
                payload={"context": context.model_dump(mode="json")},
            )
        return context

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.refresh()
            except Exception:
                log.exception("context refresh failed")


def platform_label() -> str:
    if mac_version := platform.mac_ver()[0]:
        return f"macOS {mac_version}"
    if platform.system() == "Windows":
        return f"Windows {platform.release()}"
    return f"{platform.system()} {platform.release()}"
