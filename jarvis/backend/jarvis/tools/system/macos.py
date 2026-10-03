"""Real macOS system backend.

Launching uses ``open -a <bundle>`` (LaunchServices — the same path as a
double-click; an app that is already running is brought to the front).
Observation needs no privacy permission:

- processes: ``psutil``; a process belongs to an app when its executable lives
  inside that ``.app`` bundle (``…/Spotify.app/Contents/MacOS/Spotify``),
- windows: ``CGWindowListCopyWindowInfo`` (on-screen, front-to-back). Window
  titles of other apps require the Screen Recording permission; without it
  the app's own name is used as the title.

Only ``.app`` bundles from the standard application folders are ever
launched — never arbitrary executables.

The window source and the ``open`` command are injectable so everything but
the Quartz call itself is exercised by the test-suite on any OS.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import psutil

from jarvis.tools.system.backend import (
    EnvironmentSnapshot,
    LaunchError,
    ProcessInfo,
    SystemBackend,
    WindowInfo,
)

WindowSource = Callable[[], list[dict[str, Any]]]

DEFAULT_APP_DIRS = (
    Path("/Applications"),
    Path("/System/Applications"),
    Path("/System/Library/CoreServices/Applications"),
    Path.home() / "Applications",
)
_MIN_WINDOW_SIDE = 50  # ignore invisible helper windows


def bundle_key(executable: str) -> str | None:
    """``/Applications/Spotify.app/Contents/MacOS/Spotify`` → ``spotify.app``.

    The outermost bundle wins, so helper processes count for their app.
    """
    marker = executable.find(".app/")
    if marker == -1:
        return None
    return os.path.basename(executable[: marker + 4]).lower()


class AppIndex:
    """Installed ``.app`` bundles, looked up by name (case-insensitive)."""

    def __init__(self, directories: Iterable[Path] = DEFAULT_APP_DIRS) -> None:
        self._directories = tuple(directories)

    def scan(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for directory in self._directories:
            for entry in _entries(directory):
                if entry.suffix == ".app":
                    found.setdefault(entry.stem.lower(), entry)
                elif entry.is_dir() and not entry.name.startswith("."):
                    # one level of grouping folders, e.g. /Applications/Utilities
                    for nested in _entries(entry):
                        if nested.suffix == ".app":
                            found.setdefault(nested.stem.lower(), nested)
        return found

    def find(self, name: str) -> Path | None:
        candidate = Path(os.path.expanduser(name))
        if candidate.is_absolute():
            return candidate if candidate.suffix == ".app" and candidate.is_dir() else None
        return self.scan().get(name.lower().removesuffix(".app"))


def _entries(directory: Path) -> list[Path]:
    try:
        return sorted(directory.iterdir())
    except OSError:
        return []


def windows_from_cg(
    infos: Iterable[Mapping[str, Any]], keys_by_pid: Mapping[int, str]
) -> list[WindowInfo]:
    """Normal app windows from CGWindowList entries (front-to-back order)."""
    windows: list[WindowInfo] = []
    for info in infos:
        if int(info.get("kCGWindowLayer", 0)) != 0:
            continue
        if float(info.get("kCGWindowAlpha", 1.0)) <= 0:
            continue
        bounds = info.get("kCGWindowBounds") or {}
        if float(bounds.get("Width", 0)) < _MIN_WINDOW_SIDE:
            continue
        if float(bounds.get("Height", 0)) < _MIN_WINDOW_SIDE:
            continue
        pid = int(info.get("kCGWindowOwnerPID", 0))
        owner = str(info.get("kCGWindowOwnerName") or "")
        windows.append(
            WindowInfo(
                handle=int(info.get("kCGWindowNumber", 0)),
                title=str(info.get("kCGWindowName") or "") or owner,
                pid=pid,
                process_name=keys_by_pid.get(pid) or owner.lower(),
                is_foreground=not windows,
                app_name=owner,
            )
        )
    return windows


def quartz_windows() -> list[dict[str, Any]]:
    import Quartz  # pyobjc-framework-Quartz, installed on macOS only

    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    infos = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
    return [dict(info) for info in infos]


def _processes() -> tuple[tuple[ProcessInfo, ...], dict[int, str]]:
    processes: list[ProcessInfo] = []
    keys: dict[int, str] = {}
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        info = proc.info
        key = bundle_key(info.get("exe") or "") or str(info.get("name") or "").lower()
        if not key:
            continue
        pid = int(info["pid"])
        processes.append(ProcessInfo(pid=pid, name=key))
        keys[pid] = key
    return tuple(processes), keys


def _key_for_pid(pid: int) -> str | None:
    try:
        return bundle_key(psutil.Process(pid).exe())
    except (psutil.Error, OSError):
        return None


class MacOSSystemBackend(SystemBackend):
    name = "macos"
    simulated = False

    def __init__(
        self,
        *,
        app_index: AppIndex | None = None,
        window_source: WindowSource = quartz_windows,
        opener: str = "/usr/bin/open",
    ) -> None:
        self._index = app_index or AppIndex()
        self._window_source = window_source
        self._opener = opener

    async def snapshot(self) -> EnvironmentSnapshot:
        return await asyncio.to_thread(self._snapshot)

    def _snapshot(self) -> EnvironmentSnapshot:
        processes, keys = _processes()
        windows = windows_from_cg(self._window_source(), keys)
        return EnvironmentSnapshot(windows=tuple(windows), processes=processes)

    async def active_window(self) -> WindowInfo | None:
        return await asyncio.to_thread(self._active_window)

    def _active_window(self) -> WindowInfo | None:
        windows = windows_from_cg(self._window_source(), {})
        if not windows:
            return None
        front = windows[0]
        return replace(front, process_name=_key_for_pid(front.pid) or front.process_name)

    async def resolve(self, candidate: str) -> str | None:
        path = await asyncio.to_thread(self._index.find, candidate)
        return str(path) if path else None

    async def launch(self, target: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                self._opener,
                "-a",
                target,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise LaunchError(
                "launch_failed", "macOS couldn't start the application.", str(exc)
            ) from exc
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise LaunchError(
                "launch_failed",
                "macOS couldn't open the application.",
                stderr.decode(errors="replace").strip(),
            )
