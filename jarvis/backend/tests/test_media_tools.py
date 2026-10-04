"""Volume and music tools: simulated desktop, macOS script layer, step-only volume."""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.core.trace import TraceContext
from jarvis.runtime import Runtime
from jarvis.settings import AppCatalogSettings
from jarvis.tools.base import ToolResult
from jarvis.tools.media import MediaControlArgs, MediaControlTool, SetVolumeArgs, SetVolumeTool
from jarvis.tools.system.backend import ActionError, PlayerState, VolumeState, unsupported
from jarvis.tools.system.macos_audio import MacAudio, parse_player, parse_volume_settings
from jarvis.tools.system.simulated import SimulatedSystemBackend


@pytest.fixture(autouse=True)
def fast_players(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MediaControlTool, "settle_seconds", 0.0)


async def run(rt: Runtime, tool: str, args: dict[str, Any] | None = None) -> ToolResult:
    return await rt.executor.run(tool, args or {}, ctx=TraceContext.new(), reason="test")


async def verify(rt: Runtime, tool: str, args: Any, result: ToolResult) -> str:
    found = rt.tools.get(tool)
    assert found is not None
    verification = await found.verify(args, result, TraceContext.new())
    assert verification is not None
    return verification.status


async def test_volume_is_set_read_back_and_verified(runtime: Runtime) -> None:
    assert (await run(runtime, "get_volume")).data == {"level": 40, "muted": False}

    up = await run(runtime, "set_volume", {"level": 70})
    assert up.success and up.observed_result == "Volume 40% → 70%"
    assert await verify(runtime, "set_volume", SetVolumeArgs(level=70), up) == "verified"

    down = await run(runtime, "set_volume", {"change": -100})
    assert down.data["after"] == {"level": 0, "muted": False}

    muted = await run(runtime, "set_volume", {"mute": True})
    assert muted.data["after"]["muted"] is True
    louder = await run(runtime, "set_volume", {"change": 10})
    assert louder.data["after"] == {"level": 10, "muted": False}  # louder while muted unmutes

    nothing = await run(runtime, "set_volume", {})
    assert nothing.error is not None and nothing.error.code == "invalid_arguments"


async def test_music_needs_a_running_player(runtime: Runtime) -> None:
    result = await run(runtime, "media_control", {"action": "pause"})
    assert result.error is not None and result.error.code == "no_player"
    assert (await run(runtime, "now_playing")).summary == "No music app is running"


async def test_music_control_is_observed(runtime: Runtime) -> None:
    assert (await run(runtime, "open_application", {"name": "spotify"})).success
    playing = await run(runtime, "now_playing")
    assert playing.summary == "Spotify is playing — Midnight City by M83"

    paused = await run(runtime, "media_control", {"action": "pause"})
    assert paused.success and paused.observed_result == "Spotify is paused — Midnight City by M83"
    args = MediaControlArgs(action="pause")
    assert await verify(runtime, "media_control", args, paused) == "verified"

    skipped = await run(runtime, "media_control", {"action": "next"})
    assert skipped.data["after"]["track"] == "Intro"
    args = MediaControlArgs(action="next")
    assert await verify(runtime, "media_control", args, skipped) == "verified"


# --- macOS script layer ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "state"),
    [
        ("output volume:50, input volume:75, alert volume:100, output muted:false\n", (50, False)),
        (
            "output volume:missing value, input volume:missing value, output muted:true",
            (None, True),
        ),
    ],
)
def test_parse_volume_settings(text: str, state: tuple[int | None, bool]) -> None:
    assert parse_volume_settings(text) == VolumeState(*state)


def test_parse_player() -> None:
    assert parse_player("Spotify", "spotify.app", "playing\nSong\nBand\n") == PlayerState(
        "Spotify", "spotify.app", "playing", "Song", "Band"
    )
    assert parse_player("Music", "music.app", "stopped\n\n\n").track is None
    assert parse_player("Music", "music.app", "fast forwarding\n").status == "unknown"


class FakeOsa:
    def __init__(self, replies: dict[str, tuple[int, str, str]]) -> None:
        self.replies = replies
        self.scripts: list[str] = []

    async def __call__(self, script: str) -> tuple[int, str, str]:
        self.scripts.append(script)
        for marker, reply in self.replies.items():
            if marker in script:
                return reply
        return 0, "", ""


async def test_mac_audio_prefers_the_playing_player_and_maps_errors() -> None:
    osa = FakeOsa(
        {
            '"Spotify"\n  set s': (0, "paused\nA\nX\n", ""),
            '"Music"\n  set s': (0, "playing\nB\nY\n", ""),
        }
    )
    audio = MacAudio(osa)
    player = await audio.player({"spotify.app", "music.app"})
    assert player is not None and player.app == "Music" and player.track == "B"
    assert await audio.player(set()) is None  # nothing running → nothing addressed (or launched)

    await audio.media("next", player)
    assert osa.scripts[-1] == 'tell application "Music" to next track'
    await audio.set_volume(level=130, muted=False)
    assert osa.scripts[-1] == "set volume output volume 100\nset volume output muted false"

    denied = MacAudio(
        FakeOsa({"": (1, "", "execution error: Not authorized to send Apple events (-1743)")})
    )
    with pytest.raises(ActionError) as caught:
        await denied.media("toggle", player)
    assert caught.value.code == "automation_denied"
    assert "Privacy & Security → Automation" in (caught.value.suggestion or "")


# --- platforms that can only step the volume (Windows) --------------------------------


class StepOnly(SimulatedSystemBackend):
    def __init__(self) -> None:
        super().__init__(AppCatalogSettings(), launch_latency_ms=0)
        self.steps: list[int] = []

    async def volume(self) -> VolumeState:
        raise unsupported("Reading the volume")

    async def step_volume(self, steps: int) -> None:
        self.steps.append(steps)


async def test_step_only_volume_is_honest() -> None:
    backend = StepOnly()
    tool = SetVolumeTool(backend)
    ctx = TraceContext.new()
    louder = await tool.execute(SetVolumeArgs(change=10), ctx)
    assert louder.success and backend.steps == [5]
    assert "can't be read" in (louder.observed_result or "")
    assert (await tool.verify(SetVolumeArgs(change=10), louder, ctx)).status == "unverifiable"

    exact = await tool.execute(SetVolumeArgs(level=30), ctx)
    assert exact.error is not None and exact.error.code == "unsupported"
