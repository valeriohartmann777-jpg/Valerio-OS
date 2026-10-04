"""Platform boundary for system control.

Nothing outside ``tools/system`` touches the operating system. A new platform
(macOS, Linux) is a new ``SystemBackend`` implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class WindowInfo:
    handle: int
    title: str
    pid: int
    process_name: str  # lowercase key: "notepad.exe" (Windows), "textedit.app" (macOS)
    is_foreground: bool = False
    app_name: str = ""  # the OS's own (possibly localized) application name, if known
    minimized: bool = False  # Windows: iconic windows still count as "visible"


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


class ActionError(Exception):
    """The OS refused a system action, or it isn't supported on this platform."""

    def __init__(
        self,
        code: str,
        message: str,
        detail: str | None = None,
        *,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.suggestion = suggestion


class LaunchError(ActionError):
    pass


def unsupported(what: str) -> ActionError:
    return ActionError("unsupported", f"{what} isn't supported on this system yet.")


@dataclass(frozen=True, slots=True)
class VolumeState:
    level: int | None  # 0-100; None when the output device has no adjustable volume
    muted: bool | None


PlayerStatus = Literal["playing", "paused", "stopped", "unknown"]
MediaCommand = Literal["play", "pause", "toggle", "next", "previous"]


@dataclass(frozen=True, slots=True)
class PlayerState:
    app: str  # "Spotify", "Music"
    key: str  # process key of the player app
    status: PlayerStatus
    track: str | None = None
    artist: str | None = None


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

    # Optional capabilities. A backend that lacks one raises ``unsupported`` and
    # the tool explains that honestly instead of pretending. None of these verify
    # themselves: tools observe the result afterwards.

    async def open_location(self, location: str) -> None:
        """Hand a web address or a file path to its default handler."""
        raise unsupported("Opening web pages and files")

    async def hide_app(self, processes: frozenset[str]) -> int:
        """Hide (macOS) or minimise (Windows) the app's windows; returns how many
        app instances or windows were asked to."""
        raise unsupported("Hiding applications")

    async def quit_app(self, processes: frozenset[str]) -> int:
        """Ask the app to quit gracefully (it may still ask to save); returns how
        many app instances or windows were asked to."""
        raise unsupported("Quitting applications")

    async def volume(self) -> VolumeState:
        raise unsupported("Reading the volume")

    async def set_volume(self, *, level: int | None = None, muted: bool | None = None) -> None:
        raise unsupported("Setting the volume")

    async def step_volume(self, steps: int) -> None:
        """Relative change in the OS's own volume steps (for backends that can't
        read the level). Negative = quieter."""
        raise unsupported("Changing the volume")

    async def player(self) -> PlayerState | None:
        """The music player that is running (preferring one that plays), if any."""
        raise unsupported("Media control")

    async def media(self, command: MediaCommand, player: PlayerState | None) -> None:
        raise unsupported("Media control")
