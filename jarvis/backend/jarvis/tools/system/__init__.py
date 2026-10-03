"""System control: backend selection and tool registration."""

from __future__ import annotations

import logging

from jarvis.settings import Settings, resolve_backend
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system.apps import AppCatalog
from jarvis.tools.system.backend import SystemBackend
from jarvis.tools.system.tools import (
    GetActiveWindowTool,
    GetSystemInfoTool,
    ListRunningAppsTool,
    OpenApplicationTool,
)

log = logging.getLogger("jarvis.tools")


def create_backend(settings: Settings) -> SystemBackend:
    choice = resolve_backend(settings.runtime.system_backend)
    if choice == "windows":
        from jarvis.tools.system.windows import WindowsSystemBackend

        return WindowsSystemBackend()
    if choice == "macos":
        from jarvis.tools.system.macos import MacOSSystemBackend

        try:
            return MacOSSystemBackend()  # imports Quartz on this (main) thread
        except Exception:
            log.exception(
                "macOS control unavailable (pyobjc-framework-Quartz missing or broken). "
                "Run ./scripts/setup.sh — falling back to the SIMULATED desktop."
            )
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
