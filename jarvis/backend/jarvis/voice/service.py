"""The voice loop: “Hey JARVIS” → listen → transcribe → act → speak the reply.

- Wake word (local) or the microphone button starts a request; a pause ends it.
- The request is transcribed by the speech provider and handed to JARVIS like
  a typed command (``source="voice"``).
- Replies to spoken requests are spoken (typed ones too, if the user wants).
- While JARVIS speaks it doesn't listen for the wake word, so it can't wake
  itself up.

The microphone stays closed unless the wake word is on or a request is being
recorded. Audio leaves the computer only for transcription of a request.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from pydantic import SecretStr

from jarvis.core.connector import normalize_key
from jarvis.core.state import JarvisState, StateService
from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType, Severity
from jarvis.settings import VoiceSettings, write_dotenv_value
from jarvis.storage.preferences import Preferences
from jarvis.voice.audio import AudioDevice, AudioUnavailable, chime
from jarvis.voice.elevenlabs import Transcript, VoiceError
from jarvis.voice.endpoint import Outcome, UtteranceRecorder, level_db
from jarvis.voice.wakeword import WakeDetector, download_models, missing_models

log = logging.getLogger("jarvis.voice")

SPEECH_RATE = 24_000  # Hz of the provider's PCM speech
_WAKE_PHRASE = re.compile(
    r"^\s*(?:(?:hey|hi|hallo|okay|ok)\s*[,.!]?\s*)?jarvis\b[\s,.!?:;-]*", re.I
)
_MARKDOWN = re.compile(r"[*_`#>]+")
ENV_NAME = "ELEVENLABS_API_KEY"


class SpeechProvider(Protocol):
    voice_id: str

    async def verify(self) -> None: ...
    async def transcribe(self, pcm16k: bytes) -> Transcript: ...
    async def synthesize(self, text: str) -> bytes: ...
    async def close(self) -> None: ...


ProviderFactory = Callable[[str, VoiceSettings], SpeechProvider]


def elevenlabs_provider(api_key: str, settings: VoiceSettings) -> SpeechProvider:
    from jarvis.voice.elevenlabs import ElevenLabs

    return ElevenLabs(
        api_key,
        voice_id=settings.voice_id,
        tts_model=settings.tts_model,
        stt_model=settings.stt_model,
    )


class VoiceState(StrEnum):
    OFF = "off"  # no key
    UNAVAILABLE = "unavailable"  # no microphone / audio system
    READY = "ready"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class VoiceStatus:
    state: VoiceState
    configured: bool
    wake_word: bool  # the user wants "Hey JARVIS"
    wake_word_active: bool  # … and it is actually listening
    speak_replies: bool  # speak replies to typed commands as well
    voice_name: str
    key_hint: str | None
    reason: str | None


def strip_wake_phrase(text: str) -> str:
    return _WAKE_PHRASE.sub("", text, count=1).strip()


def spoken_text(text: str, limit: int) -> str:
    clean = " ".join(_MARKDOWN.sub("", text).split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rsplit(". ", 1)[0]
    return cut if cut.endswith(".") else f"{cut}."


class VoiceService:
    def __init__(
        self,
        *,
        settings: VoiceSettings,
        bus: EventBus,
        state: StateService,
        submit: Callable[[str], Any],
        preferences: Preferences,
        env_file: Path,
        models_dir: Path,
        audio_factory: Callable[[], AudioDevice],
        detector_factory: Callable[[Path], WakeDetector],
        provider_factory: ProviderFactory = elevenlabs_provider,
        download: Callable[[Path], None] = download_models,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._state = state
        self._submit = submit
        self._prefs = preferences
        self._env_file = env_file
        self._models_dir = models_dir
        self._audio_factory = audio_factory
        self._detector_factory = detector_factory
        self._provider_factory = provider_factory
        self._download = download

        self._provider: SpeechProvider | None = None
        self._audio: AudioDevice | None = None
        self._detector: WakeDetector | None = None
        self._phase = VoiceState.OFF
        self._reason: str | None = None
        self._wake_active = False
        self._arming = False
        self._mode = "idle"  # idle | wake | capture
        self._recorder: UtteranceRecorder | None = None
        self._release_mic_after = False
        self._noise_db = -60.0
        self._frames: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=200)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._consumer: asyncio.Task[None] | None = None
        self._voice_traces: set[str] = set()
        self._speech = asyncio.Lock()
        self._speaking = False
        self._lock = asyncio.Lock()
        self._unsubscribe = bus.subscribe(EventType.JARVIS_MESSAGE, self._on_reply)

    # Status -----------------------------------------------------------------------

    @property
    def status(self) -> VoiceStatus:
        key = self._settings.elevenlabs_api_key
        return VoiceStatus(
            state=self._phase,
            configured=key is not None,
            wake_word=bool(self._prefs.get("voice.wake_word", True)),
            wake_word_active=self._wake_active,
            speak_replies=bool(self._prefs.get("voice.speak_replies", False)),
            voice_name=self._settings.voice_name,
            key_hint=key.get_secret_value()[-4:] if key else None,
            reason=self._reason,
        )

    async def _changed(self, message: str = "", severity: Severity = Severity.DEBUG) -> None:
        status = self.status
        await self._bus.emit(
            EventType.VOICE_CHANGED,
            message=message or f"Voice {status.state}",
            source="voice",
            severity=severity,
            payload={"voice": asdict(status)},
        )

    async def _set_phase(self, phase: VoiceState, reason: str | None = None) -> None:
        if (phase, reason) == (self._phase, self._reason):
            return
        self._phase, self._reason = phase, reason
        await self._changed()

    # Lifecycle --------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        async with self._lock:
            await self._start()

    async def _start(self) -> None:
        key = self._settings.elevenlabs_api_key
        if key is None:
            await self._set_phase(
                VoiceState.OFF, "Add an ElevenLabs key under Settings → Voice to talk to JARVIS."
            )
            return
        if self._provider is None:
            self._provider = self._provider_factory(key.get_secret_value(), self._settings)
        if not self._settings.microphone_allowed:
            await self._set_phase(
                VoiceState.UNAVAILABLE,
                "The JARVIS app needs a quick reinstall before it may use the microphone.",
            )
            return
        if self._audio is None:
            try:
                self._audio = self._audio_factory()
            except AudioUnavailable as exc:
                await self._set_phase(VoiceState.UNAVAILABLE, str(exc))
                return
        if self._consumer is None:
            self._consumer = asyncio.create_task(self._consume(), name="voice-frames")
        await self._set_phase(VoiceState.READY)
        if self.status.wake_word:
            self._spawn(self._arm())

    async def stop(self) -> None:
        self._unsubscribe()
        self._disarm()
        if self._audio is not None:
            self._audio.stop_playback()
        for task in [*self._tasks, *([self._consumer] if self._consumer else [])]:
            task.cancel()
        for task in [*self._tasks, *([self._consumer] if self._consumer else [])]:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._provider is not None:
            await self._provider.close()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # Wake word --------------------------------------------------------------------

    async def _arm(self) -> None:
        """Load the wake-word model (downloading it once) and keep the mic open."""
        if self._audio is None or self._wake_active or self._arming:
            return
        self._arming = True
        try:
            if missing_models(self._models_dir):
                await self._changed("Preparing “Hey JARVIS” (one-time download, ~4 MB)")
                await asyncio.to_thread(self._download, self._models_dir)
            if self._detector is None:
                self._detector = await asyncio.to_thread(self._detector_factory, self._models_dir)
            if not self.status.wake_word:
                return  # switched off while the model was loading
            self._open_mic()
        except AudioUnavailable as exc:
            await self._set_phase(VoiceState.UNAVAILABLE, str(exc))
            return
        except Exception as exc:
            log.warning("wake word unavailable: %s", exc)
            self._reason = "“Hey JARVIS” couldn't start; the microphone button still works."
            await self._changed("Wake word unavailable", Severity.WARNING)
            return
        finally:
            self._arming = False
        self._wake_active = True
        self._mode = "wake" if self._mode == "idle" else self._mode
        self._reason = None
        await self._changed("Listening for “Hey JARVIS”", Severity.INFO)

    def _disarm(self) -> None:
        self._wake_active = False
        if self._mode == "wake":
            self._mode = "idle"
        if self._mode == "idle" and self._audio is not None:
            self._audio.stop_input()

    def _open_mic(self) -> None:
        assert self._audio is not None
        self._audio.start_input(self._on_frame)

    def _on_frame(self, frame: np.ndarray) -> None:
        """Audio thread → event loop. Drops frames rather than block the device."""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._enqueue, frame)

    def _enqueue(self, frame: np.ndarray) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self._frames.put_nowait(frame)

    async def _consume(self) -> None:
        while True:
            frame = await self._frames.get()
            try:
                await self._handle_frame(frame)
            except Exception:
                log.exception("voice frame handling failed")

    async def _handle_frame(self, frame: np.ndarray) -> None:
        if self._mode == "capture" and self._recorder is not None:
            outcome = self._recorder.feed(frame)
            if outcome is Outcome.DONE:
                await self._finish_capture()
            elif outcome is Outcome.NOTHING:
                await self._end_capture()
            return
        if self._mode != "wake" or self._speaking or self._detector is None:
            return
        score = await asyncio.to_thread(self._detector.process, frame)
        if score < 0.1:
            self._noise_db = 0.95 * self._noise_db + 0.05 * level_db(frame)
        if score >= self._settings.wake_threshold:
            log.info("wake word detected (score %.2f)", score)
            await self._begin_capture(from_wake_word=True)

    # Requests ---------------------------------------------------------------------

    async def listen(self) -> VoiceStatus:
        """Push-to-talk: record one request now."""
        if self._phase in (VoiceState.OFF, VoiceState.UNAVAILABLE) or self._audio is None:
            raise VoiceError("unavailable", self._reason or "Voice isn't set up.")
        if self._speaking:
            self._audio.stop_playback()
        if self._mode != "capture":
            if not self._wake_active:
                try:
                    self._open_mic()
                except AudioUnavailable as exc:
                    raise VoiceError("no_microphone", str(exc)) from exc
                self._release_mic_after = True
            await self._begin_capture(from_wake_word=False)
        return self.status

    async def _begin_capture(self, *, from_wake_word: bool) -> None:
        known_noise = from_wake_word or self._wake_active
        self._recorder = UtteranceRecorder(
            noise_db=self._noise_db,
            calibration_frames=0 if known_noise else 3,
            end_silence=self._settings.end_silence_seconds,
            max_seconds=self._settings.max_request_seconds,
        )
        self._mode = "capture"
        await self._set_phase(VoiceState.LISTENING)
        await self._state.set(JarvisState.LISTENING, detail="Listening…")
        if from_wake_word and self._audio is not None:
            self._spawn(self._audio.play(chime(), SPEECH_RATE))

    async def _end_capture(self) -> None:
        self._recorder = None
        self._mode = "wake" if self._wake_active else "idle"
        if self._release_mic_after and not self._wake_active and self._audio is not None:
            self._audio.stop_input()
        self._release_mic_after = False
        if self._detector is not None:
            self._detector.reset()
        await self._set_phase(VoiceState.READY)
        if self._state.snapshot().state is JarvisState.LISTENING:
            await self._state.set(JarvisState.DORMANT)

    async def _finish_capture(self) -> None:
        recorder = self._recorder
        await self._end_capture()
        if recorder is None or recorder.seconds < 0.3:
            return
        self._spawn(self._transcribe(recorder.audio()))

    async def _transcribe(self, pcm: bytes) -> None:
        assert self._provider is not None
        await self._set_phase(VoiceState.TRANSCRIBING)
        await self._state.set(JarvisState.UNDERSTANDING, detail="Transcribing…")
        try:
            transcript = await self._provider.transcribe(pcm)
        except VoiceError as exc:
            await self._set_phase(VoiceState.READY)
            await self._report(exc)
            return
        await self._set_phase(VoiceState.READY)
        text = strip_wake_phrase(transcript.text)
        if not text:
            await self._state.set(JarvisState.DORMANT)
            await self._changed("I didn't catch anything after “Hey JARVIS”.", Severity.INFO)
            return
        ctx = self._submit(text)
        self._voice_traces.add(ctx.trace_id)

    # Speaking ---------------------------------------------------------------------

    async def _on_reply(self, event: Event) -> None:
        if self._provider is None or self._audio is None:
            return
        spoken = event.trace_id in self._voice_traces
        if spoken:
            self._voice_traces.discard(event.trace_id or "")
        if spoken or self.status.speak_replies:
            # Long messages (the morning briefing) bring a short version to say.
            text = str(event.payload.get("speech") or event.payload.get("text") or "")
            if text:
                self._spawn(self.say(text))

    async def say(self, text: str) -> None:
        if self._provider is None or self._audio is None:
            raise VoiceError("unavailable", self._reason or "Voice isn't set up.")
        async with self._speech:
            try:
                pcm = await self._provider.synthesize(
                    spoken_text(text, self._settings.max_spoken_chars)
                )
            except VoiceError as exc:
                await self._report(exc)
                return
            self._speaking = True
            await self._set_phase(VoiceState.SPEAKING)
            await self._state.set(JarvisState.SPEAKING, detail=text)
            try:
                await self._audio.play(pcm, SPEECH_RATE)
            finally:
                self._speaking = False
                if self._detector is not None:
                    self._detector.reset()  # forget what JARVIS itself just said
                await self._set_phase(VoiceState.READY)
                if self._state.snapshot().state is JarvisState.SPEAKING:
                    await self._state.set(JarvisState.DORMANT)

    def stop_activity(self) -> None:
        """Stop speaking / cancel a recording (the mic button while busy)."""
        if self._audio is not None:
            self._audio.stop_playback()
        if self._mode == "capture":
            self._spawn(self._end_capture())

    async def _report(self, exc: VoiceError) -> None:
        await self._bus.emit(
            EventType.JARVIS_MESSAGE,
            message=exc.message,
            source="voice",
            severity=Severity.IMPORTANT,
            payload={
                "text": exc.message,
                "success": False,
                "error": {"code": exc.code, "message": exc.message, "suggestion": exc.suggestion},
            },
        )
        await self._state.set(JarvisState.FAILED, detail=exc.message)

    # Settings ---------------------------------------------------------------------

    async def set_preferences(
        self, *, wake_word: bool | None = None, speak_replies: bool | None = None
    ) -> VoiceStatus:
        if speak_replies is not None:
            self._prefs.update(**{"voice.speak_replies": speak_replies})
        if wake_word is not None:
            self._prefs.update(**{"voice.wake_word": wake_word})
            if wake_word and self._phase is not VoiceState.OFF:
                await self._arm()
            elif not wake_word:
                self._disarm()
        await self._changed()
        return self.status

    async def connect(self, raw_key: str) -> VoiceStatus:
        key = normalize_key(raw_key, ENV_NAME)
        if len(key) < 20 or any(c.isspace() for c in key):
            raise VoiceError(
                "invalid_format",
                "That doesn't look like an ElevenLabs API key.",
                suggestion="Copy it again from elevenlabs.io → API keys.",
            )
        provider = self._provider_factory(key, self._settings)
        try:
            await provider.verify()
        except VoiceError:
            await provider.close()
            raise
        try:
            write_dotenv_value(self._env_file, ENV_NAME, key)
        except OSError as exc:
            await provider.close()
            raise VoiceError(
                "save_failed",
                f"The key works, but I couldn't save it to {self._env_file}.",
                detail=str(exc),
            ) from exc
        async with self._lock:
            if self._provider is not None:
                await self._provider.close()
            self._provider = provider
            self._settings = self._settings.model_copy(
                update={"elevenlabs_api_key": SecretStr(key)}
            )
            await self._start()
        await self._changed(f"Voice connected — {self._settings.voice_name}", Severity.IMPORTANT)
        return self.status
