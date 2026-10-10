"""Speech recognition — local only, runs inside the worker process.

Providers:

* ``moonshine`` — Moonshine base (English), MIT-licensed weights shipped inside
  the ``moonshine-cpp`` wheel: works offline from the first run.
* ``whisper`` — faster-whisper (multilingual). Its weights are not bundled; the
  service downloads them once into JARVIS's data folder when the user asks.

Moonshine returns text without timestamps, so the audio is split at pauses into
chunks of a few seconds and each chunk is transcribed on its own: the chunk's
start and end are the segment's time range. Times are where it was said in the
source — never market time.
"""

from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from jarvis.quantlab.intake.media import SAMPLE_RATE

FRAME = 320  # 20 ms at 16 kHz
MAX_CHUNK_S = 20.0
MIN_CHUNK_S = 3.0
PAD_S = 0.15


@dataclass(frozen=True)
class SpeechSegment:
    start_ms: int
    end_ms: int
    text: str
    quality: Literal["ok", "low", "unintelligible"]
    note: str = ""


def voiced_regions(audio: np.ndarray) -> list[tuple[int, int]]:
    """Sample ranges with speech energy, split at pauses of ≥ 300 ms."""
    n = audio.size // FRAME
    if n == 0:
        return []
    frames = audio[: n * FRAME].reshape(n, FRAME)
    energy = np.sqrt(np.mean(frames * frames, axis=1))
    threshold = max(0.004, 0.08 * float(np.percentile(energy, 90)))
    voiced = energy > threshold
    regions: list[tuple[int, int]] = []
    start: int | None = None
    silence = 0
    for i, v in enumerate(voiced.tolist()):
        if v:
            start = i if start is None else start
            silence = 0
        elif start is not None:
            silence += 1
            if silence >= 15:
                regions.append((start * FRAME, (i - silence + 1) * FRAME))
                start, silence = None, 0
    if start is not None:
        regions.append((start * FRAME, n * FRAME))
    return [(a, b) for a, b in regions if b - a >= 0.12 * SAMPLE_RATE]


def chunks(regions: list[tuple[int, int]], total: int) -> list[tuple[int, int]]:
    """Group pause-separated regions into chunks of 3–20 s, splitting longer runs."""
    out: list[tuple[int, int]] = []
    max_len, min_len = int(MAX_CHUNK_S * SAMPLE_RATE), int(MIN_CHUNK_S * SAMPLE_RATE)
    for a, b in regions:
        while b - a > max_len:  # music or non-stop talk: hard split
            out.append((a, a + max_len))
            a += max_len
        if out and (b - out[-1][0] <= max_len) and (out[-1][1] - out[-1][0] < min_len):
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    if len(out) > 1 and out[-1][1] - out[-1][0] < min_len and out[-1][1] - out[-2][0] <= max_len:
        last = out.pop()
        out[-1] = (out[-1][0], last[1])
    pad = int(PAD_S * SAMPLE_RATE)
    return [(max(0, a - pad), min(total, b + pad)) for a, b in out]


_REPEAT = re.compile(r"\b(\w+)(?:[\s,.]+\1\b){3,}", re.IGNORECASE)


def judge(text: str, seconds: float) -> tuple[Literal["ok", "low", "unintelligible"], str]:
    clean = text.strip()
    if not clean:
        return "unintelligible", "speech energy but no recognisable words"
    if _REPEAT.search(clean):
        return "low", "repeated words — likely music or noise misread as speech"
    if seconds > 0 and len(clean) / seconds > 40:
        return "low", "more text than can be spoken in this time — likely a misread"
    return "ok", ""


class MoonshineASR:
    name = "moonshine-base-en"
    languages = ("en",)

    def __init__(self) -> None:
        from moonshine_cpp import moonshine_cpp as mc  # loads its bundled onnxruntime

        self._mc = mc
        self._model = mc.MoonshineModel()  # English weights ship inside the wheel

    def _text(self, audio: np.ndarray) -> str:
        data = np.ascontiguousarray(audio, dtype=np.float32)
        out = ctypes.c_char_p()
        code = self._mc.moonshine_lib.moonshine_transcribe(
            self._model.model_handle,
            data.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            data.size,
            ctypes.byref(out),
        )
        if code != 0:
            raise RuntimeError(f"moonshine error {code}")
        text = (out.value or b"").decode("utf-8", "replace")
        if out.value is not None:
            self._mc.moonshine_lib.moonshine_free_text(out)
        return " ".join(text.split())

    def transcribe(self, audio: np.ndarray, language: str) -> list[SpeechSegment]:
        segments: list[SpeechSegment] = []
        for a, b in chunks(voiced_regions(audio), audio.size):
            text = self._text(audio[a:b])
            quality, note = judge(text, (b - a) / SAMPLE_RATE)
            segments.append(
                SpeechSegment(
                    int(a / SAMPLE_RATE * 1000), int(b / SAMPLE_RATE * 1000), text, quality, note
                )
            )
        return segments


class WhisperASR:
    name = "faster-whisper"
    languages = ("*",)

    def __init__(self, model_dir: str) -> None:
        from faster_whisper import WhisperModel

        self._model: Any = WhisperModel(model_dir, device="cpu", compute_type="int8")
        self.name = f"faster-whisper ({model_dir.rsplit('/', 1)[-1]})"

    def transcribe(self, audio: np.ndarray, language: str) -> list[SpeechSegment]:
        segments, _info = self._model.transcribe(
            audio, language=None if language == "auto" else language, vad_filter=True
        )
        out: list[SpeechSegment] = []
        for seg in segments:
            text = " ".join(str(seg.text).split())
            quality, note = judge(text, float(seg.end - seg.start))
            if getattr(seg, "no_speech_prob", 0.0) > 0.6:
                quality, note = "low", "the recogniser itself doubts this is speech"
            out.append(
                SpeechSegment(int(seg.start * 1000), int(seg.end * 1000), text, quality, note)
            )
        return out
