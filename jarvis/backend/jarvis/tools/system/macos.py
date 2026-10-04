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
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

import psutil

from jarvis.tools.system.backend import (
    ActionError,
    EnvironmentSnapshot,
    LaunchError,
    MediaCommand,
    PlayerState,
    ProcessInfo,
    SystemBackend,
    VolumeState,
    WindowInfo,
    unsupported,
)
from jarvis.tools.system.macos_audio import PLAYERS, MacAudio

log = logging.getLogger("jarvis.tools")

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


class QuartzWindowSource:
    """On-screen windows from the window server (front-to-back).

    pyobjc's ``Quartz`` package loads AppKit, which must happen on the main
    thread — so it is imported here, at construction, not lazily inside the
    worker thread that later calls it.
    """

    def __init__(self) -> None:
        import Quartz  # pyobjc-framework-Quartz, installed on macOS only

        self._copy = Quartz.CGWindowListCopyWindowInfo
        self._options = (
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        )
        self._null_window = Quartz.kCGNullWindowID

    def __call__(self) -> list[dict[str, Any]]:
        infos = self._copy(self._options, self._null_window) or []
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


def is_main_executable(executable: str) -> bool:
    """``…/Spotify.app/Contents/MacOS/Spotify`` yes; helpers inside the bundle no."""
    marker = executable.find(".app/")
    if marker == -1:
        return False
    rest = executable[marker + 5 :]
    return rest.startswith("Contents/MacOS/") and "/" not in rest[len("Contents/MacOS/") :]


def _main_pids(processes: frozenset[str]) -> list[int]:
    pids = []
    for proc in psutil.process_iter(["pid", "exe"]):
        executable = proc.info.get("exe") or ""
        if bundle_key(executable) in processes and is_main_executable(executable):
            pids.append(int(proc.info["pid"]))
    return pids


class AppControl(Protocol):
    def hide(self, pid: int) -> bool: ...
    def terminate(self, pid: int) -> bool: ...


class AppKitControl:
    """``NSRunningApplication`` — the Dock's own way to hide and quit apps.
    Needs no privacy permission (unlike AppleScript or Accessibility)."""

    def __init__(self) -> None:
        from AppKit import NSRunningApplication  # part of pyobjc (Quartz depends on it)

        self._lookup = NSRunningApplication.runningApplicationWithProcessIdentifier_

    def hide(self, pid: int) -> bool:
        app = self._lookup(pid)
        return bool(app is not None and app.hide())

    def terminate(self, pid: int) -> bool:
        app = self._lookup(pid)
        return bool(app is not None and app.terminate())


def _appkit_control() -> AppControl | None:
    try:
        return AppKitControl()
    except Exception:  # pyobjc without AppKit: everything else still works
        log.warning("AppKit unavailable — hiding and quitting apps is disabled", exc_info=True)
        return None


class MacOSSystemBackend(SystemBackend):
    name = "macos"
    simulated = False

    def __init__(
        self,
        *,
        app_index: AppIndex | None = None,
        window_source: WindowSource | None = None,
        opener: str = "/usr/bin/open",
        app_control: AppControl | None = None,
        main_pids: Callable[[frozenset[str]], list[int]] = _main_pids,
        audio: MacAudio | None = None,
    ) -> None:
        self._index = app_index or AppIndex()
        self._window_source = window_source or QuartzWindowSource()
        self._opener = opener
        # AppKit must load on the main thread, like Quartz — so here, not lazily.
        self._control = app_control if app_control is not None else _appkit_control()
        self._main_pids = main_pids
        self._audio = audio or MacAudio()

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

    async def hide_app(self, processes: frozenset[str]) -> int:
        if self._control is None:
            raise unsupported("Hiding applications")
        return await asyncio.to_thread(self._each_app, processes, self._control.hide, "hide")

    async def quit_app(self, processes: frozenset[str]) -> int:
        if self._control is None:
            raise unsupported("Quitting applications")
        return await asyncio.to_thread(self._each_app, processes, self._control.terminate, "quit")

    def _each_app(self, processes: frozenset[str], action: Callable[[int], bool], verb: str) -> int:
        pids = self._main_pids(processes)
        if not pids:
            raise ActionError("not_running", "That application isn't running.")
        accepted = sum(1 for pid in pids if action(pid))
        if accepted == 0:
            raise ActionError("refused", f"macOS refused to {verb} the application.")
        return accepted

    async def volume(self) -> VolumeState:
        return await self._audio.volume()

    async def set_volume(self, *, level: int | None = None, muted: bool | None = None) -> None:
        await self._audio.set_volume(level=level, muted=muted)

    async def player(self) -> PlayerState | None:
        running = await asyncio.to_thread(
            lambda: {key for key in PLAYERS if self._main_pids(frozenset({key}))}
        )
        return await self._audio.player(running)

    async def media(self, command: MediaCommand, player: PlayerState | None) -> None:
        if player is None:
            raise ActionError("no_player", "No music app is running.")
        await self._audio.media(command, player)

    async def open_location(self, location: str) -> None:
        # `open <url|path>` = LaunchServices default handler, like a click in Finder.
        try:
            process = await asyncio.create_subprocess_exec(
                self._opener,
                location,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ActionError("open_failed", "macOS couldn't open it.", str(exc)) from exc
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise ActionError(
                "open_failed",
                "macOS couldn't open it — no application handles this type.",
                stderr.decode(errors="replace").strip(),
            )
