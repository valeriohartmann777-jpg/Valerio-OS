"""Platform boundary for system control.

Nothing outside ``tools/system`` touches the operating system. A new platform
(macOS, Linux) is a new ``SystemBackend`` implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WindowInfo:
    handle: int
    title: str
    pid: int
    process_name: str  # lowercase, e.g. "notepad.exe"
    is_foreground: bool = False


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    name: str  # lowercase


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    windows: tuple[WindowInfo, ...]
    processes: tuple[ProcessInfo, ...]

    @property
    def foreground(self) -> WindowInfo | None:
        return next((w for w in self.windows if w.is_foreground), None)


class LaunchError(Exception):
    def __init__(self, code: str, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class SystemBackend(ABC):
    name: str
    simulated: bool

    @abstractmethod
    async def snapshot(self) -> EnvironmentSnapshot:
        """Visible top-level application windows plus running processes."""

    @abstractmethod
    async def active_window(self) -> WindowInfo | None: ...

    @abstractmethod
    async def resolve(self, candidate: str) -> str | None:
        """Return a launchable target for ``candidate`` or ``None`` if unavailable."""

    @abstractmethod
    async def launch(self, target: str) -> None:
        """Ask the OS to start ``target``. Raises ``LaunchError``. Does not verify."""
