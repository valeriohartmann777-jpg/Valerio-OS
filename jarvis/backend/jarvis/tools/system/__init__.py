"""System control: backend selection and tool registration."""

from __future__ import annotations

import sys

from jarvis.settings import Settings
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system.apps import AppCatalog
from jarvis.tools.system.backend import SystemBackend
from jarvis.tools.system.tools import (
    GetActiveWindowTool,
    GetSystemInfoTool,
    ListRunningAppsTool,
    OpenApplicationTool,
)


def create_backend(settings: Settings) -> SystemBackend:
    choice = settings.runtime.system_backend
    if choice == "auto":
        choice = "windows" if sys.platform == "win32" else "simulated"
    if choice == "windows":
        from jarvis.tools.system.windows import WindowsSystemBackend

        return WindowsSystemBackend()
    from jarvis.tools.system.simulated import SimulatedSystemBackend

    return SimulatedSystemBackend(
        settings.apps, launch_latency_ms=settings.runtime.simulated_launch_latency_ms
    )


def register_system_tools(
    registry: ToolRegistry, backend: SystemBackend, catalog: AppCatalog, settings: Settings
) -> None:
    registry.register(GetSystemInfoTool(backend))
    registry.register(GetActiveWindowTool(backend, catalog))
    registry.register(ListRunningAppsTool(backend, catalog))
    registry.register(
        OpenApplicationTool(
            backend, catalog, verify_timeout=settings.runtime.launch_verify_timeout_seconds
        )
    )
