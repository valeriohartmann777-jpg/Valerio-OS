"""Volume and music: get_volume, set_volume, media_control, now_playing.

Every change is read back afterwards. Where the platform can't read the state
(Windows volume level, Windows media keys) the result says so instead of
claiming success it couldn't observe.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import ActionDescriptor, PermissionLevel
from jarvis.tools.base import Tool, ToolError, ToolResult, Verification
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system.backend import (
    ActionError,
    MediaCommand,
    PlayerState,
    SystemBackend,
    VolumeState,
)


def _error(tool: str, exc: ActionError, simulated: bool) -> ToolResult:
    return ToolResult.failure(
        tool,
        ToolError(code=exc.code, message=exc.message, suggestion=exc.suggestion, detail=exc.detail),
        simulated=simulated,
    )


def _volume_text(state: VolumeState) -> str:
    if state.muted:
        return "muted" + (f" (level {state.level}%)" if state.level is not None else "")
    return f"{state.level}%" if state.level is not None else "unknown"


# --- get_volume -------------------------------------------------------------------


class NoArgs(BaseModel):
    pass


class GetVolumeTool(Tool[NoArgs]):
    name = "get_volume"
    description = "Read the computer's output volume (0-100) and whether it is muted."
    permission_level = PermissionLevel.READ
    input_model = NoArgs

    def __init__(self, backend: SystemBackend) -> None:
        self._backend = backend

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        try:
            state = await self._backend.volume()
        except ActionError as exc:
            return _error(self.name, exc, self._backend.simulated)
        summary = f"Volume {_volume_text(state)}"
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=summary,
            observed_result=summary,
            data={"level": state.level, "muted": state.muted},
            simulated=self._backend.simulated,
        )


# --- set_volume -------------------------------------------------------------------


class SetVolumeArgs(BaseModel):
    level: int | None = Field(default=None, ge=0, le=100, description="Absolute volume, 0-100.")
    change: int | None = Field(
        default=None, ge=-100, le=100, description="Relative change, e.g. +10 louder, -20 quieter."
    )
    mute: bool | None = Field(default=None, description="true = mute, false = unmute.")

    @model_validator(mode="after")
    def _one_change(self) -> SetVolumeArgs:
        if self.level is None and self.change is None and self.mute is None:
            raise ValueError("give level, change or mute")
        if self.level is not None and self.change is not None:
            raise ValueError("give either level or change, not both")
        return self


class SetVolumeTool(Tool[SetVolumeArgs]):
    name = "set_volume"
    description = (
        "Change the output volume: an absolute level (0-100), a relative change (+/-), or "
        "mute / unmute. Raising the volume while muted unmutes."
    )
    permission_level = PermissionLevel.SAFE_ACTION
    input_model = SetVolumeArgs
    side_effects = True

    def __init__(self, backend: SystemBackend) -> None:
        self._backend = backend

    def _phrase(self, args: SetVolumeArgs) -> str:
        if args.level is not None:
            text = f"Set the volume to {args.level}%"
        elif args.change is not None:
            text = f"Turn the volume {'up' if args.change > 0 else 'down'} by {abs(args.change)}%"
        else:
            text = ""
        if args.mute is not None:
            mute = "Mute" if args.mute else "Unmute"
            text = f"{text} and {mute.lower()}" if text else mute
        return text

    def describe_action(self, args: SetVolumeArgs) -> ActionDescriptor:
        phrase = self._phrase(args)
        return ActionDescriptor(
            title="Volume",
            target="volume",
            summary=f"{phrase}.",
            effects=["Changes the computer's output volume"],
            level=self.permission_level,
            category="media",
        )

    def step_titles(self, args: SetVolumeArgs) -> tuple[str, str]:
        return self._phrase(args), "Verify the volume"

    async def execute(self, args: SetVolumeArgs, ctx: TraceContext) -> ToolResult:
        try:
            before = await self._backend.volume()
        except ActionError as exc:
            if exc.code != "unsupported":
                return _error(self.name, exc, self._backend.simulated)
            return await self._step_only(args)
        target = args.level
        if args.change is not None:
            target = max(0, min(100, (before.level or 0) + args.change))
        muted = args.mute
        if muted is None and before.muted and target is not None and target > (before.level or 0):
            muted = False  # "louder" while muted means: I want to hear something
        try:
            await self._backend.set_volume(level=target, muted=muted)
            after = await self._backend.volume()
        except ActionError as exc:
            return _error(self.name, exc, self._backend.simulated)
        observed = f"Volume {_volume_text(before)} → {_volume_text(after)}"
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            target="volume",
            summary=observed,
            observed_result=observed,
            data={
                "before": {"level": before.level, "muted": before.muted},
                "after": {"level": after.level, "muted": after.muted},
                "expected": {"level": target, "muted": muted},
            },
            simulated=self._backend.simulated,
        )

    async def _step_only(self, args: SetVolumeArgs) -> ToolResult:
        """Platforms that can't read the level (Windows): relative steps only."""
        if args.change is None or args.mute is not None:
            return _error(
                self.name,
                ActionError(
                    "unsupported",
                    "On this system I can only turn the volume up or down, not set or read it.",
                    suggestion="Ask me to make it louder or quieter.",
                ),
                self._backend.simulated,
            )
        try:
            await self._backend.step_volume(round(args.change / 2))
        except ActionError as exc:
            return _error(self.name, exc, self._backend.simulated)
        observed = (
            f"Volume turned {'up' if args.change > 0 else 'down'} by about {abs(args.change)}%"
        )
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            target="volume",
            summary=observed,
            observed_result=observed + " (the level can't be read here)",
            data={"expected": None},
            simulated=self._backend.simulated,
        )

    async def verify(
        self, args: SetVolumeArgs, result: ToolResult, ctx: TraceContext
    ) -> Verification:
        if not result.success:
            return Verification(status="failed", method="result", summary=result.summary)
        expected = result.data.get("expected")
        if not expected:
            return Verification(
                status="unverifiable", method="volume", summary="The volume can't be read here."
            )
        state = await self._backend.volume()
        level_ok = expected["level"] is None or (
            state.level is not None and abs(state.level - expected["level"]) <= 1
        )
        mute_ok = expected["muted"] is None or state.muted == expected["muted"]
        return Verification(
            status="verified" if level_ok and mute_ok else "failed",
            method="volume",
            summary=f"Volume is {_volume_text(state)}",
        )


# --- media_control ----------------------------------------------------------------

_EXPECTED_STATUS = {"play": "playing", "pause": "paused"}


class MediaControlArgs(BaseModel):
    action: Literal["play", "pause", "toggle", "next", "previous"] = Field(
        description="play, pause, toggle (play/pause), next or previous track."
    )


def _player_text(player: PlayerState) -> str:
    track = f" — {player.track}" + (f" by {player.artist}" if player.artist else "")
    if player.status in ("playing", "paused"):
        return f"{player.app} is {player.status}" + (track if player.track else "")
    return f"{player.app}: {player.status}"


class MediaControlTool(Tool[MediaControlArgs]):
    name = "media_control"
    description = (
        "Control music playback in the running player (Spotify or Apple Music on macOS; media "
        "keys on Windows): play, pause, toggle, next, previous."
    )
    permission_level = PermissionLevel.SAFE_ACTION
    input_model = MediaControlArgs
    side_effects = True
    settle_seconds = 0.8  # players need a moment before they report the new state

    def __init__(self, backend: SystemBackend) -> None:
        self._backend = backend

    def describe_action(self, args: MediaControlArgs) -> ActionDescriptor:
        phrase = {
            "play": "Play music",
            "pause": "Pause music",
            "toggle": "Play / pause music",
            "next": "Skip to the next track",
            "previous": "Go back to the previous track",
        }[args.action]
        return ActionDescriptor(
            title="Music",
            target=args.action,
            summary=f"{phrase}.",
            level=self.permission_level,
            category="media",
        )

    def step_titles(self, args: MediaControlArgs) -> tuple[str, str]:
        return self.describe_action(args).summary.rstrip("."), "Verify playback"

    async def precheck(self, args: MediaControlArgs, ctx: TraceContext) -> ToolError | None:
        try:
            player = await self._backend.player()
        except ActionError as exc:
            return ToolError(code=exc.code, message=exc.message, suggestion=exc.suggestion)
        if player is None:
            return ToolError(
                code="no_player",
                message="No music app is running.",
                suggestion="Open Spotify or Music first.",
            )
        return None

    async def execute(self, args: MediaControlArgs, ctx: TraceContext) -> ToolResult:
        command: MediaCommand = args.action
        try:
            before = await self._backend.player()
            await self._backend.media(command, before)
            await asyncio.sleep(self.settle_seconds)
            after = await self._backend.player()
        except ActionError as exc:
            return _error(self.name, exc, self._backend.simulated)
        data: dict[str, Any] = {
            "player": after.app if after else None,
            "before": _state(before),
            "after": _state(after),
        }
        observed = (
            _player_text(after)
            if after and after.status != "unknown"
            else (
                f"Sent “{args.action}” to {after.app if after else 'the player'} "
                "(its state can't be read here)"
            )
        )
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            target=args.action,
            summary=observed,
            observed_result=observed,
            data=data,
            simulated=self._backend.simulated,
        )

    async def verify(
        self, args: MediaControlArgs, result: ToolResult, ctx: TraceContext
    ) -> Verification:
        if not result.success:
            return Verification(status="failed", method="result", summary=result.summary)
        player = await self._backend.player()
        if player is None or player.status == "unknown":
            return Verification(
                status="unverifiable", method="player", summary="The player's state can't be read."
            )
        before = result.data.get("before") or {}
        if args.action in _EXPECTED_STATUS:
            ok = player.status == _EXPECTED_STATUS[args.action]
        elif args.action == "toggle":
            ok = player.status != before.get("status")
        else:
            ok = player.track != before.get("track") or player.status == "playing"
        return Verification(
            status="verified" if ok else "failed", method="player", summary=_player_text(player)
        )


def _state(player: PlayerState | None) -> dict[str, Any] | None:
    if player is None:
        return None
    return {"status": player.status, "track": player.track, "artist": player.artist}


# --- now_playing ------------------------------------------------------------------


class NowPlayingTool(Tool[NoArgs]):
    name = "now_playing"
    description = "Which track is playing in Spotify or Apple Music, and whether it is paused."
    permission_level = PermissionLevel.READ
    input_model = NoArgs

    def __init__(self, backend: SystemBackend) -> None:
        self._backend = backend

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        try:
            player = await self._backend.player()
        except ActionError as exc:
            return _error(self.name, exc, self._backend.simulated)
        if player is None:
            summary = "No music app is running"
        elif player.status == "unknown":
            summary = "The player's state can't be read on this system"
        else:
            summary = _player_text(player)
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=summary,
            observed_result=summary,
            data={"player": player.app if player else None, "state": _state(player)},
            simulated=self._backend.simulated,
        )


def register_media_tools(registry: ToolRegistry, backend: SystemBackend) -> None:
    registry.register(GetVolumeTool(backend))
    registry.register(SetVolumeTool(backend))
    registry.register(MediaControlTool(backend))
    registry.register(NowPlayingTool(backend))
