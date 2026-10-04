"""The voice loop end to end with a fake microphone, wake word and provider:
“Hey JARVIS, open Notepad” → mission → spoken reply."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import SecretStr

from jarvis.core.state import JarvisState
from jarvis.events.types import EventType
from jarvis.runtime import Runtime
from jarvis.settings import Settings, VoiceSettings
from jarvis.voice.audio import AudioUnavailable
from jarvis.voice.elevenlabs import Transcript, VoiceError
from jarvis.voice.service import spoken_text, strip_wake_phrase
from jarvis.voice.wakeword import FRAME, MODEL_FILES
from tests.conftest import Recorder, eventually, make_settings

WAKE_MARK = 4321


def frame(db: float, *, wake: bool = False) -> np.ndarray:
    amplitude = 32768 * 10 ** (db / 20) * np.sqrt(2)
    t = np.arange(FRAME) / 16000
    samples = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    if wake:
        samples[0] = WAKE_MARK
    return samples


QUIET = [frame(-62)] * 6
SPEECH = [frame(-25)] * 10
PAUSE = [frame(-62)] * 14


class FakeAudio:
    def __init__(self) -> None:
        self.handler: Callable[[np.ndarray], None] | None = None
        self.played: list[tuple[int, int]] = []  # (bytes, rate)
        self.stopped = 0

    @property
    def mic_open(self) -> bool:
        return self.handler is not None

    def start_input(self, on_frame: Callable[[np.ndarray], None]) -> None:
        self.handler = on_frame

    def stop_input(self) -> None:
        self.handler = None

    async def play(self, pcm: bytes, rate: int) -> None:
        self.played.append((len(pcm), rate))
        await asyncio.sleep(0.01)

    def stop_playback(self) -> None:
        self.stopped += 1

    def speak_into(self, frames: list[np.ndarray]) -> None:
        assert self.handler is not None, "microphone is closed"
        for f in frames:
            self.handler(f)


class FakeDetector:
    def __init__(self) -> None:
        self.resets = 0

    def process(self, f: np.ndarray) -> float:
        return 0.93 if f[0] == WAKE_MARK else 0.01

    def reset(self) -> None:
        self.resets += 1


class FakeProvider:
    voice_id = "test-voice"

    def __init__(self, heard: str = "Hey Jarvis, open Notepad.") -> None:
        self.heard = heard
        self.spoken: list[str] = []
        self.transcribed: list[int] = []
        self.fail_transcribe: VoiceError | None = None
        self.fail_verify: VoiceError | None = None

    async def verify(self) -> None:
        if self.fail_verify:
            raise self.fail_verify

    async def transcribe(self, pcm16k: bytes) -> Transcript:
        self.transcribed.append(len(pcm16k))
        if self.fail_transcribe:
            raise self.fail_transcribe
        return Transcript(self.heard, "en")

    async def synthesize(self, text: str) -> bytes:
        self.spoken.append(text)
        return b"\x00\x00" * 2400

    async def close(self) -> None:
        pass


class Harness:
    def __init__(
        self, rt: Runtime, audio: FakeAudio, detector: FakeDetector, provider: FakeProvider
    ):
        self.rt, self.audio, self.detector, self.provider = rt, audio, detector, provider
        self.events = Recorder(rt.bus)
        self.states: list[JarvisState] = []
        rt.bus.subscribe(EventType.JARVIS_STATE_CHANGED, self._state)

    async def _state(self, event: Any) -> None:
        self.states.append(JarvisState(event.payload["state"]))


Factory = Callable[..., Any]


def voice_settings(settings: Settings, **changes: Any) -> Settings:
    voice: VoiceSettings = settings.voice.model_copy(update=changes)
    return settings.model_copy(update={"voice": voice})


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Factory]:
    runtimes: list[Runtime] = []

    async def make(
        *, key: bool = True, mic: bool = True, provider: FakeProvider | None = None
    ) -> Harness:
        settings = make_settings(tmp_path / f"rt{len(runtimes)}")
        models = settings.data_dir / "models"
        models.mkdir(parents=True, exist_ok=True)
        for name in MODEL_FILES.values():  # present already: no download in tests
            (models / name).write_bytes(b"onnx")
        settings = voice_settings(
            settings,
            elevenlabs_api_key=SecretStr("sk_test_" + "k" * 24) if key else None,
            microphone_allowed=mic,
        )
        audio, detector, fake = FakeAudio(), FakeDetector(), provider or FakeProvider()
        rt = Runtime(
            settings,
            voice_audio=lambda: audio,
            voice_detector=lambda _models: detector,
            voice_provider=lambda _key, _settings: fake,
        )
        harness = Harness(rt, audio, detector, fake)
        await rt.start()
        runtimes.append(rt)
        return harness

    yield make
    for rt in runtimes:
        await rt.stop()


async def test_hey_jarvis_open_notepad_is_heard_done_and_answered(harness: Factory) -> None:
    h = await harness()
    await eventually(lambda: h.rt.voice.status.wake_word_active)
    assert h.audio.mic_open

    h.audio.speak_into([*QUIET, frame(-30, wake=True), *SPEECH, *PAUSE])
    await eventually(lambda: h.provider.spoken)
    await h.rt.core.wait_idle()

    [command] = h.events.of(EventType.COMMAND_RECEIVED)
    assert command.payload == {"text": "open Notepad.", "source": "voice"}
    assert h.provider.spoken == ["Notepad is open."]
    assert h.audio.played[0][1] == 24_000  # the chime, then the reply
    await eventually(lambda: len(h.audio.played) == 2)
    assert JarvisState.LISTENING in h.states and JarvisState.SPEAKING in h.states
    await eventually(lambda: h.rt.voice.status.state == "ready")
    assert h.detector.resets >= 2  # after the request and after speaking
    assert h.audio.mic_open  # still listening for the next "Hey JARVIS"


async def test_typed_commands_are_spoken_only_when_asked(harness: Factory) -> None:
    h = await harness()
    await h.rt.core.handle("open notepad")
    await asyncio.sleep(0.05)
    assert h.provider.spoken == []

    await h.rt.voice.set_preferences(speak_replies=True)
    await h.rt.core.handle("hello")
    await eventually(lambda: h.provider.spoken)
    assert h.provider.spoken == ["Online. Everything is nominal."]


async def test_push_to_talk_opens_the_mic_only_while_recording(harness: Factory) -> None:
    h = await harness()
    await h.rt.voice.set_preferences(wake_word=False)
    assert not h.audio.mic_open and not h.rt.voice.status.wake_word_active

    h.provider.heard = "System status"
    await h.rt.voice.listen()
    assert h.audio.mic_open and h.rt.voice.status.state == "listening"
    h.audio.speak_into([*QUIET[:3], *SPEECH, *PAUSE])
    await eventually(lambda: h.provider.spoken)
    assert not h.audio.mic_open
    assert h.provider.spoken[0].startswith("CPU at ")
    assert h.audio.played[0][1] == 24_000 and len(h.audio.played) == 1  # no chime on a click


async def test_silence_after_the_wake_word_goes_back_to_listening(harness: Factory) -> None:
    h = await harness()
    await eventually(lambda: h.rt.voice.status.wake_word_active)
    h.audio.speak_into([*QUIET, frame(-30, wake=True), *[frame(-62)] * 70])
    await eventually(lambda: h.rt.voice.status.state == "ready" and h.detector.resets >= 1)
    assert h.provider.transcribed == []
    assert h.events.of(EventType.COMMAND_RECEIVED) == []


async def test_only_the_wake_word_is_ignored(harness: Factory) -> None:
    h = await harness(provider=FakeProvider(heard="Hey Jarvis."))
    await eventually(lambda: h.rt.voice.status.wake_word_active)
    h.audio.speak_into([*QUIET, frame(-30, wake=True), *SPEECH, *PAUSE])
    await eventually(lambda: h.provider.transcribed)
    await asyncio.sleep(0.05)
    assert h.events.of(EventType.COMMAND_RECEIVED) == []


async def test_provider_problems_are_explained(harness: Factory) -> None:
    provider = FakeProvider()
    provider.fail_transcribe = VoiceError(
        "quota_exceeded", "Your ElevenLabs credits are used up.", suggestion="Top up."
    )
    h = await harness(provider=provider)
    await h.rt.voice.listen()
    h.audio.speak_into([*QUIET[:3], *SPEECH, *PAUSE])
    await eventually(lambda: h.events.of(EventType.JARVIS_MESSAGE))
    [message] = h.events.of(EventType.JARVIS_MESSAGE)
    assert message.payload["error"]["code"] == "quota_exceeded"
    assert message.payload["error"]["suggestion"] == "Top up."


async def test_without_key_or_permission_voice_explains_itself(harness: Factory) -> None:
    off = await harness(key=False)
    status = off.rt.voice.status
    assert status.state == "off" and "ElevenLabs" in (status.reason or "")
    with pytest.raises(VoiceError):
        await off.rt.voice.listen()
    assert not off.audio.mic_open

    blocked = await harness(mic=False)
    assert blocked.rt.voice.status.state == "unavailable"
    assert not blocked.audio.mic_open


async def test_no_audio_system(tmp_path: Path) -> None:
    def no_audio() -> Any:
        raise AudioUnavailable("No audio system available (PortAudio missing).")

    settings = voice_settings(
        make_settings(tmp_path), elevenlabs_api_key=SecretStr("sk_" + "x" * 30)
    )
    rt = Runtime(settings, voice_audio=no_audio, voice_provider=lambda _k, _s: FakeProvider())
    await rt.start()
    try:
        assert rt.voice.status.state == "unavailable"
        assert "PortAudio" in (rt.voice.status.reason or "")
    finally:
        await rt.stop()


async def test_connecting_a_key(harness: Factory) -> None:
    provider = FakeProvider()
    h = await harness(key=False, provider=provider)
    with pytest.raises(VoiceError) as bad:
        await h.rt.voice.connect("nope")
    assert bad.value.code == "invalid_format"

    provider.fail_verify = VoiceError("invalid_key", "ElevenLabs rejected the API key.")
    with pytest.raises(VoiceError):
        await h.rt.voice.connect("sk_" + "a" * 40)
    assert not h.rt.settings.env_file.exists()

    provider.fail_verify = None
    status = await h.rt.voice.connect("ELEVENLABS_API_KEY=sk_" + "a" * 36 + "WXYZ")
    assert status.configured and status.key_hint == "WXYZ"
    assert "ELEVENLABS_API_KEY=sk_" in h.rt.settings.env_file.read_text()
    await eventually(lambda: h.rt.voice.status.wake_word_active)
    assert h.audio.mic_open


def test_text_helpers() -> None:
    assert strip_wake_phrase("Hey Jarvis, öffne Safari") == "öffne Safari"
    assert strip_wake_phrase("jarvis: lauter") == "lauter"
    assert strip_wake_phrase("Hey JARVIS.") == ""
    assert strip_wake_phrase("Ruf Jarvis Cocker an") == "Ruf Jarvis Cocker an"
    assert spoken_text("**Done.** Safari is `open`.", 100) == "Done. Safari is open."
    long = "First sentence. " + "x" * 200
    assert spoken_text(long, 50) == "First sentence."


def test_voice_api(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from jarvis.api.app import create_app

    provider = FakeProvider()
    runtime = Runtime(
        make_settings(tmp_path),
        voice_audio=FakeAudio,
        voice_detector=lambda _m: FakeDetector(),
        voice_provider=lambda _k, _s: provider,
    )
    with TestClient(create_app(runtime=runtime)) as client:
        assert client.get("/snapshot").json()["voice"]["state"] == "off"
        assert client.post("/voice/listen").status_code == 409
        bad = client.post("/voice/key", json={"api_key": "x"})
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_format"
        evil = {"origin": "https://evil.example"}
        assert (
            client.post("/voice/key", json={"api_key": "sk_" + "a" * 30}, headers=evil).status_code
            == 403
        )

        ok = client.post("/voice/key", json={"api_key": "sk_" + "a" * 30})
        assert ok.status_code == 200 and ok.json()["configured"] is True
        assert "sk_" + "a" * 30 not in ok.text
        prefs = client.post("/voice/preferences", json={"speak_replies": True}).json()
        assert prefs["speak_replies"] is True
        assert client.post("/voice/test").status_code == 200
        assert provider.spoken == ["Hello. This is how I sound."]
