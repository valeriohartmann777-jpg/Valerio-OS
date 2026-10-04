"""MOCK — simulated desktop environment.

This backend does NOT control a real computer. It models a Windows desktop in
memory (windows, processes, foreground, launch latency) so the complete
command → tool → observe → verify → UI chain can be developed and tested on
non-Windows hosts. Every result produced through it carries
``simulated: true`` and the dashboard shows a SIMULATED badge.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from pathlib import PureWindowsPath
from urllib.parse import urlsplit

from jarvis.settings import AppCatalogSettings
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
)

_ENV_VAR = re.compile(r"%([^%]+)%")
_SIMULATED_ENV = {
    "LOCALAPPDATA": r"C:\Users\user\AppData\Local",
    "APPDATA": r"C:\Users\user\AppData\Roaming",
    "ProgramFiles": r"C:\Program Files",
}


# Which simulated app shows a document, by extension (folders open in Explorer).
_VIEWERS = {
    ".txt": "notepad.exe",
    ".md": "notepad.exe",
    ".csv": "notepad.exe",
    ".log": "notepad.exe",
    ".json": "notepad.exe",
    ".pdf": "msedge.exe",
    ".jpg": "photos.exe",
    ".jpeg": "photos.exe",
    ".png": "photos.exe",
}


def _expand(candidate: str) -> str:
    return _ENV_VAR.sub(lambda m: _SIMULATED_ENV.get(m.group(1), m.group(0)), candidate)


class SimulatedSystemBackend(SystemBackend):
    name = "simulated"
    simulated = True

    def __init__(self, catalog: AppCatalogSettings, *, launch_latency_ms: int = 700) -> None:
        self._latency = launch_latency_ms / 1000
        self._installed: dict[str, tuple[str, str]] = {}
        for entry in catalog.applications.values():
            for candidate in entry.launch:
                self._installed[_expand(candidate).lower()] = (
                    entry.processes[0].lower(),
                    entry.window_title or entry.name,
                )
        self._next_pid = 4000
        self._next_handle = 0x10000
        self._processes: dict[int, str] = {4: "system", 812: "explorer.exe", 1024: "jarvis.exe"}
        self._windows: list[WindowInfo] = [
            WindowInfo(
                handle=0x1000,
                title="JARVIS",
                pid=1024,
                process_name="jarvis.exe",
                is_foreground=True,
            )
        ]
        self._pending: set[asyncio.Task[None]] = set()
        self._hidden: list[WindowInfo] = []
        # Test hook: apps that keep running when asked to quit ("save changes?").
        self.refuse_quit: set[str] = set()
        self._volume = VolumeState(level=40, muted=False)
        self._playlist = [("Midnight City", "M83"), ("Intro", "The xx"), ("Nightcall", "Kavinsky")]
        self._track = 0
        self._playing = True

    async def snapshot(self) -> EnvironmentSnapshot:
        processes = tuple(ProcessInfo(pid=pid, name=name) for pid, name in self._processes.items())
        return EnvironmentSnapshot(windows=tuple(self._windows), processes=processes)

    async def active_window(self) -> WindowInfo | None:
        return next((w for w in self._windows if w.is_foreground), None)

    async def resolve(self, candidate: str) -> str | None:
        expanded = _expand(candidate)
        return expanded if expanded.lower() in self._installed else None

    async def launch(self, target: str) -> None:
        installed = self._installed.get(target.lower())
        if installed is None:
            raise LaunchError("app_not_found", "The application isn't installed (simulated).")
        task = asyncio.create_task(self._start(*installed))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def open_location(self, location: str) -> None:
        if "://" in location:
            host = urlsplit(location).hostname or location
            target = ("msedge.exe", f"{host} - Microsoft Edge")
        else:
            path = PureWindowsPath(location)
            app = _VIEWERS.get(path.suffix.lower(), "explorer.exe")
            target = (app, f"{path.name} - {app.removesuffix('.exe').capitalize()}")
        task = asyncio.create_task(self._start(*target))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def hide_app(self, processes: frozenset[str]) -> int:
        hidden = [w for w in self._windows if w.process_name in processes]
        if not hidden:
            raise ActionError("not_running", "That application has no open windows (simulated).")
        self._hidden.extend(hidden)
        self._windows = [w for w in self._windows if w.process_name not in processes]
        self._focus_last()
        return len(hidden)

    async def quit_app(self, processes: frozenset[str]) -> int:
        pids = [pid for pid, name in self._processes.items() if name in processes]
        if not pids:
            raise ActionError("not_running", "That application isn't running (simulated).")
        if self.refuse_quit & processes:  # models an app asking to save first
            return len(pids)
        self._windows = [w for w in self._windows if w.process_name not in processes]
        self._hidden = [w for w in self._hidden if w.process_name not in processes]
        self._processes = {p: n for p, n in self._processes.items() if n not in processes}
        self._focus_last()
        return len(pids)

    async def volume(self) -> VolumeState:
        return self._volume

    async def set_volume(self, *, level: int | None = None, muted: bool | None = None) -> None:
        self._volume = VolumeState(
            level=self._volume.level if level is None else max(0, min(100, level)),
            muted=self._volume.muted if muted is None else muted,
        )

    async def player(self) -> PlayerState | None:
        if "spotify.exe" not in self._processes.values():
            return None
        track, artist = self._playlist[self._track]
        return PlayerState(
            "Spotify", "spotify.exe", "playing" if self._playing else "paused", track, artist
        )

    async def media(self, command: MediaCommand, player: PlayerState | None) -> None:
        if player is None:
            raise ActionError("no_player", "No music app is running (simulated).")
        if command in ("next", "previous"):
            step = 1 if command == "next" else -1
            self._track = (self._track + step) % len(self._playlist)
            self._playing = True
        else:
            self._playing = {"play": True, "pause": False}.get(command, not self._playing)

    def _focus_last(self) -> None:
        if self._windows and not any(w.is_foreground for w in self._windows):
            self._windows[-1] = replace(self._windows[-1], is_foreground=True)

    async def _start(self, process_name: str, title: str) -> None:
        await asyncio.sleep(self._latency)
        self._next_pid += 4
        self._next_handle += 0x10
        self._processes[self._next_pid] = process_name
        self._windows = [replace(w, is_foreground=False) for w in self._windows]
        self._windows.append(
            WindowInfo(
                handle=self._next_handle,
                title=title,
                pid=self._next_pid,
                process_name=process_name,
                is_foreground=True,
            )
        )

    # Test helpers ---------------------------------------------------------

    def close_all(self, process_name: str) -> None:
        """Simulate the user closing every window of ``process_name``."""
        process_name = process_name.lower()
        self._windows = [w for w in self._windows if w.process_name != process_name]
        self._processes = {p: n for p, n in self._processes.items() if n != process_name}
