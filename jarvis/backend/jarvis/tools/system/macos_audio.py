"""macOS volume and music control through ``osascript``.

- Volume uses AppleScript's built-in ``get/set volume`` (Standard Additions):
  no privacy permission needed.
- Music is controlled in Spotify or Apple Music via AppleScript. macOS asks
  the user once whether JARVIS (Terminal while developing) may control that
  app. Only apps that are already running are ever addressed — talking to an
  app that isn't running would launch it.

The script runner is injectable, so parsing and error handling are tested on
any OS.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from jarvis.tools.system.backend import (
    ActionError,
    MediaCommand,
    PlayerState,
    PlayerStatus,
    VolumeState,
)

Runner = Callable[[str], Awaitable[tuple[int, str, str]]]

# process key → AppleScript application name
PLAYERS = {"spotify.app": "Spotify", "music.app": "Music"}
_COMMANDS: dict[MediaCommand, str] = {
    "play": "play",
    "pause": "pause",
    "toggle": "playpause",
    "next": "next track",
    "previous": "previous track",
}
_STATE_SCRIPT = """tell application "{app}"
  set s to player state as string
  set t to ""
  set a to ""
  if s is not "stopped" then
    try
      set t to name of current track
      set a to artist of current track
    end try
  end if
  return s & linefeed & t & linefeed & a
end tell"""


async def run_osascript(script: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/osascript",
        "-e",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    code = process.returncode if process.returncode is not None else -1
    return code, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def parse_volume_settings(text: str) -> VolumeState:
    """``output volume:50, input volume:75, alert volume:100, output muted:false``."""
    values = {}
    for part in text.strip().split(","):
        key, _, value = part.partition(":")
        values[key.strip()] = value.strip()
    level = values.get("output volume", "")
    muted = values.get("output muted", "")
    return VolumeState(
        level=int(level) if level.isdigit() else None,
        muted={"true": True, "false": False}.get(muted),
    )


def parse_player(app: str, key: str, text: str) -> PlayerState:
    lines = [*text.rstrip("\n").split("\n"), "", "", ""][:3]
    state = lines[0].strip().lower()
    status: PlayerStatus = (
        "playing"
        if state == "playing"
        else "paused"
        if state == "paused"
        else "stopped"
        if state == "stopped"
        else "unknown"
    )
    return PlayerState(
        app=app,
        key=key,
        status=status,
        track=lines[1].strip() or None,
        artist=lines[2].strip() or None,
    )


class MacAudio:
    def __init__(self, runner: Runner = run_osascript) -> None:
        self._run = runner

    async def _script(self, script: str, *, app: str | None = None) -> str:
        code, stdout, stderr = await self._run(script)
        if code != 0:
            raise _error(stderr, app)
        return stdout

    async def volume(self) -> VolumeState:
        return parse_volume_settings(await self._script("get volume settings"))

    async def set_volume(self, *, level: int | None, muted: bool | None) -> None:
        lines = []
        if level is not None:
            lines.append(f"set volume output volume {max(0, min(100, int(level)))}")
        if muted is not None:
            lines.append(f"set volume output muted {'true' if muted else 'false'}")
        if lines:
            await self._script("\n".join(lines))

    async def player(self, running: set[str]) -> PlayerState | None:
        """State of the running players; one that is playing wins."""
        states = []
        for key, app in PLAYERS.items():
            if key in running:
                text = await self._script(_STATE_SCRIPT.format(app=app), app=app)
                states.append(parse_player(app, key, text))
        playing = [s for s in states if s.status == "playing"]
        candidates = playing or states
        return candidates[0] if candidates else None

    async def media(self, command: MediaCommand, player: PlayerState) -> None:
        app = PLAYERS.get(player.key)
        if app is None:
            raise ActionError("no_player", "No music app is running.")
        await self._script(f'tell application "{app}" to {_COMMANDS[command]}', app=app)


def _error(stderr: str, app: str | None) -> ActionError:
    if "-1743" in stderr or "Not authorized" in stderr:
        return ActionError(
            "automation_denied",
            f"macOS didn't allow me to control {app or 'that app'}.",
            stderr.strip(),
            suggestion="Allow it in System Settings → Privacy & Security → Automation, "
            "then try again.",
        )
    if "-600" in stderr or "isn't running" in stderr.replace("\u2019", "'"):
        return ActionError("no_player", f"{app or 'The app'} isn't running.", stderr.strip())
    return ActionError("script_failed", "macOS couldn't do that.", stderr.strip())
