"""Application catalog and launch-outcome analysis (platform independent)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PureWindowsPath

from jarvis.permissions.models import PermissionLevel
from jarvis.settings import AppCatalogSettings
from jarvis.tools.system.backend import EnvironmentSnapshot, ProcessInfo, WindowInfo

_UNKNOWN_EXE = re.compile(r"^[a-z0-9][a-z0-9 ._+-]{0,63}$")
# macOS bundle names may contain spaces and non-ASCII letters ("Visual Studio Code").
_UNKNOWN_BUNDLE = re.compile(r"^[\w][\w .+&'-]{0,63}$")


@dataclass(frozen=True)
class AppTarget:
    key: str | None
    name: str
    launch: tuple[str, ...]
    processes: frozenset[str]
    level: PermissionLevel
    effects: tuple[str, ...] = ()
    window_title: str | None = None

    @property
    def known(self) -> bool:
        return self.key is not None


def normalize(query: str) -> str:
    return " ".join(query.lower().strip().strip("\"'“”").split())


class AppCatalog:
    def __init__(self, settings: AppCatalogSettings) -> None:
        self._settings = settings
        self._by_alias: dict[str, AppTarget] = {}
        self._by_process: dict[str, str] = {}
        for key, entry in settings.applications.items():
            target = AppTarget(
                key=key,
                name=entry.name,
                launch=tuple(entry.launch),
                processes=frozenset(p.lower() for p in entry.processes),
                level=entry.level,
                effects=tuple(entry.effects),
                window_title=entry.window_title,
            )
            for alias in {key, entry.name, *entry.aliases}:
                self._by_alias[normalize(alias)] = target
            for process in target.processes:
                self._by_process.setdefault(process, entry.name)
        self._denied = {normalize(name) for name in settings.denylist}
        self.platform = settings.platform

    @property
    def config_file(self) -> str:
        return f"config/apps.{self.platform}.yaml"

    def known_apps(self) -> list[AppTarget]:
        seen: dict[str, AppTarget] = {}
        for target in self._by_alias.values():
            if target.key:
                seen[target.key] = target
        return list(seen.values())

    def is_denied(self, query: str) -> bool:
        name = normalize(query).removesuffix(".exe")
        return name in self._denied

    def lookup(self, query: str) -> AppTarget | None:
        return self._by_alias.get(normalize(query))

    def resolve(self, query: str) -> AppTarget | None:
        """Catalog entry, or an *unknown* target.

        Returns ``None`` when the name is not a plausible application name.
        """
        if found := self.lookup(query):
            return found
        if self.platform == "macos":
            return self._unknown_bundle(query)
        name = normalize(query).removesuffix(".exe")
        if not _UNKNOWN_EXE.match(name) or " " in name:
            return None
        return AppTarget(
            key=None,
            name=name.title() if name.isalpha() else name,
            launch=(f"{name}.exe",),
            processes=frozenset({f"{name}.exe"}),
            level=PermissionLevel.MODIFICATION,
            effects=("JARVIS doesn't know this application; Windows decides what runs",),
        )

    @staticmethod
    def _unknown_bundle(query: str) -> AppTarget | None:
        """Any installed .app bundle. Level 1: only bundles from the standard
        application folders are launched, never arbitrary executables."""
        name = normalize(query).removesuffix(".app")
        if not _UNKNOWN_BUNDLE.match(name):
            return None
        return AppTarget(
            key=None,
            name=" ".join(word[:1].upper() + word[1:] for word in name.split()),
            launch=(name,),
            processes=frozenset({f"{name}.app"}),
            level=PermissionLevel.SAFE_ACTION,
        )

    def display_name(self, process_name: str, app_name: str = "") -> str:
        """Human name for a process: catalog name, the OS's app name, or the stem."""
        process_name = process_name.lower()
        if process_name in self._by_process:
            return self._by_process[process_name]
        if app_name:
            return app_name
        stem = PureWindowsPath(process_name).stem
        return stem[:1].upper() + stem[1:] if stem else "Unknown"

    def window_label(self, window: WindowInfo) -> str:
        return self.display_name(window.process_name, window.app_name)


class LaunchOutcome(StrEnum):
    WINDOW_OPENED = "window_opened"
    BROUGHT_TO_FRONT = "brought_to_front"
    ALREADY_RUNNING = "already_running"
    PROCESS_STARTED = "process_started"
    NOT_DETECTED = "not_detected"


@dataclass(frozen=True)
class LaunchEvidence:
    outcome: LaunchOutcome
    new_windows: tuple[WindowInfo, ...] = ()
    new_processes: tuple[ProcessInfo, ...] = ()
    matching_windows: tuple[WindowInfo, ...] = ()

    @property
    def confirmed(self) -> bool:
        """Strong evidence: a new window, or the app is now in front."""
        return self.outcome in (LaunchOutcome.WINDOW_OPENED, LaunchOutcome.BROUGHT_TO_FRONT)

    @property
    def window(self) -> WindowInfo | None:
        if self.new_windows:
            return self.new_windows[0]
        foreground = next((w for w in self.matching_windows if w.is_foreground), None)
        return foreground or (self.matching_windows[0] if self.matching_windows else None)


def matching_windows(
    snapshot: EnvironmentSnapshot, processes: frozenset[str]
) -> tuple[WindowInfo, ...]:
    return tuple(w for w in snapshot.windows if w.process_name in processes)


def matching_processes(
    snapshot: EnvironmentSnapshot, processes: frozenset[str]
) -> tuple[ProcessInfo, ...]:
    return tuple(p for p in snapshot.processes if p.name in processes)


def analyze_launch(
    before: EnvironmentSnapshot, after: EnvironmentSnapshot, processes: frozenset[str]
) -> LaunchEvidence:
    """Compare snapshots taken before and after a launch request."""
    before_handles = {w.handle for w in before.windows}
    before_pids = {p.pid for p in before.processes}
    was_running = bool(matching_windows(before, processes))

    windows_now = matching_windows(after, processes)
    new_windows = tuple(w for w in windows_now if w.handle not in before_handles)
    new_processes = tuple(
        p for p in matching_processes(after, processes) if p.pid not in before_pids
    )
    foreground = after.foreground
    in_front = foreground is not None and foreground.process_name in processes

    if new_windows:
        outcome = LaunchOutcome.WINDOW_OPENED
    elif in_front and was_running:
        outcome = LaunchOutcome.BROUGHT_TO_FRONT
    elif new_processes:
        outcome = LaunchOutcome.PROCESS_STARTED
    elif windows_now and was_running:
        outcome = LaunchOutcome.ALREADY_RUNNING
    else:
        outcome = LaunchOutcome.NOT_DETECTED
    return LaunchEvidence(
        outcome=outcome,
        new_windows=new_windows,
        new_processes=new_processes,
        matching_windows=windows_now,
    )
