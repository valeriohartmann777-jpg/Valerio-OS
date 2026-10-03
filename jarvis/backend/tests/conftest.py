from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis.events.bus import EventBus
from jarvis.events.types import Event
from jarvis.runtime import Runtime
from jarvis.settings import Settings, load_settings


def make_settings(tmp_path: Path, **runtime_overrides: object) -> Settings:
    settings = load_settings(environ={})
    runtime = settings.runtime.model_copy(
        update={
            "system_backend": "simulated",
            "simulated_launch_latency_ms": 30,
            "settle_seconds": 0.05,
            "launch_verify_timeout_seconds": 1.0,
            "context_poll_seconds": 60.0,
            **runtime_overrides,
        }
    )
    permissions = settings.permissions.model_copy(update={"approval_timeout_seconds": 2.0})
    storage = settings.storage.model_copy(
        update={"database_path": tmp_path / "jarvis.db", "log_dir": tmp_path / "logs"}
    )
    # root_dir = tmp_path: nothing a test does can touch the real jarvis/.env.
    return settings.model_copy(
        update={
            "root_dir": tmp_path,
            "runtime": runtime,
            "permissions": permissions,
            "storage": storage,
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def runtime(settings: Settings) -> AsyncIterator[Runtime]:
    rt = Runtime(settings)
    await rt.start()
    try:
        yield rt
    finally:
        await rt.stop()


class Recorder:
    """Collects every event published on a bus."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self._record)

    async def _record(self, event: Event) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [str(e.type) for e in self.events]

    def of(self, event_type: str) -> list[Event]:
        return [e for e in self.events if e.type == event_type]


@pytest.fixture
def recorder(runtime: Runtime) -> Recorder:
    return Recorder(runtime.bus)
