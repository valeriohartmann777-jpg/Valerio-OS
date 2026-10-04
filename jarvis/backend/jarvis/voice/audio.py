"""Microphone and speaker through PortAudio (``sounddevice``).

Capture delivers 80 ms frames of 16 kHz mono int16 audio — what the wake-word
model and the speech-to-text service expect. Playback takes 16-bit mono PCM.
``sounddevice`` ships PortAudio on macOS and Windows; it is imported lazily so
the rest of JARVIS runs where no audio device exists (servers, CI).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from jarvis.voice.wakeword import FRAME, SAMPLE_RATE

FrameHandler = Callable[[np.ndarray], None]


class AudioUnavailable(Exception):
    pass


class AudioDevice(Protocol):
    def start_input(self, on_frame: FrameHandler) -> None:
        """Deliver 80 ms frames (1280 int16 samples) from the microphone."""
        ...

    def stop_input(self) -> None: ...

    async def play(self, pcm: bytes, rate: int) -> None:
        """Play 16-bit mono PCM; returns when done or stopped."""
        ...

    def stop_playback(self) -> None: ...


class SoundDeviceAudio:
    def __init__(self) -> None:
        try:
            import sounddevice
        except (ImportError, OSError) as exc:  # OSError: PortAudio library missing
            raise AudioUnavailable(f"No audio system available ({exc}).") from exc
        self._sd: Any = sounddevice
        self._stream: Any = None
        self._playing = asyncio.Event()

    def start_input(self, on_frame: FrameHandler) -> None:
        if self._stream is not None:
            return

        def callback(indata: Any, frames: int, time: Any, status: Any) -> None:
            on_frame(np.array(indata[:, 0], dtype=np.int16))

        try:
            self._stream = self._sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAME,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioUnavailable(f"The microphone couldn't be opened ({exc}).") from exc

    def stop_input(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    async def play(self, pcm: bytes, rate: int) -> None:
        samples = np.frombuffer(pcm, dtype="<i2")
        if samples.size == 0:
            return
        self._sd.play(samples, samplerate=rate)
        await asyncio.to_thread(self._sd.wait)

    def stop_playback(self) -> None:
        self._sd.stop()


def chime(rate: int = 24_000) -> bytes:
    """A short rising two-tone — “I'm listening”."""
    parts = []
    for freq in (880.0, 1318.5):
        t = np.arange(int(rate * 0.07)) / rate
        tone = np.sin(2 * np.pi * freq * t) * np.hanning(t.size)
        parts.append(tone)
    signal = np.concatenate(parts) * 0.18 * 32767
    return signal.astype("<i2").tobytes()
