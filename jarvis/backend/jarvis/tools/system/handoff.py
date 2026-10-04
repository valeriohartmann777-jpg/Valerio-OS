"""Observe what happens after handing something to the OS (a URL, a document).

``open`` / ShellExecute only say the request was accepted. What counts is what
the user then sees: a browser or viewer window coming to the front.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from jarvis.tools.system.backend import EnvironmentSnapshot, SystemBackend, WindowInfo

# Process keys of common browsers (macOS bundle names, Windows executables).
BROWSERS = frozenset(
    {
        "safari.app",
        "google chrome.app",
        "google chrome beta.app",
        "chromium.app",
        "firefox.app",
        "firefox developer edition.app",
        "arc.app",
        "microsoft edge.app",
        "brave browser.app",
        "opera.app",
        "opera gx.app",
        "vivaldi.app",
        "orion.app",
        "zen.app",
        "zen browser.app",
        "duckduckgo.app",
        "msedge.exe",
        "chrome.exe",
        "firefox.exe",
        "brave.exe",
        "opera.exe",
        "vivaldi.exe",
        "arc.exe",
    }
)


def is_browser(window: WindowInfo) -> bool:
    return window.process_name in BROWSERS


@dataclass(frozen=True, slots=True)
class Handoff:
    window: WindowInfo | None  # accepted window in front afterwards, if any
    changed: bool  # it is new, or it wasn't in front before


async def observe_handoff(
    backend: SystemBackend,
    before: EnvironmentSnapshot,
    *,
    wait_seconds: float,
    accept: Callable[[WindowInfo], bool] = lambda _window: True,
    unchanged_grace: float = 1.5,
    poll_interval: float = 0.2,
) -> Handoff:
    """Wait until an accepted window is in front and something visibly changed.

    If an accepted window was already in front and stays there (e.g. a new tab
    in a browser that was already active, title hidden by macOS), that window
    is returned with ``changed=False`` after ``unchanged_grace`` seconds.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    grace = loop.time() + min(unchanged_grace, wait_seconds)
    known = {w.handle for w in before.windows}
    previous = before.foreground
    while True:
        front = await backend.active_window()
        if front is not None and accept(front):
            changed = (
                previous is None
                or front.handle not in known
                or front.handle != previous.handle
                or front.title != previous.title
            )
            if changed or loop.time() >= grace:
                return Handoff(window=front, changed=changed)
        elif loop.time() >= deadline:
            return Handoff(window=None, changed=False)
        await asyncio.sleep(poll_interval)
