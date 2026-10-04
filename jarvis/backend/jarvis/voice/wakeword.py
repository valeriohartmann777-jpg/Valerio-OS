"""“Hey JARVIS” — local wake-word detection.

A port of openWakeWord's streaming inference (Apache-2.0, David Scripka):
16 kHz audio → melspectrogram model → Google speech-embedding model → the
pre-trained ``hey_jarvis`` model, fed in 80 ms frames. Only ``onnxruntime`` and
``numpy`` are needed (the upstream package also pulls scipy, scikit-learn and,
on Linux, an unavailable TFLite runtime). Audio never leaves the computer here.

The three ONNX models (~1 MB each) are downloaded once from the openWakeWord
release into ``data/models``. The pre-trained models are licensed CC BY-NC-SA
4.0 — fine for personal use.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from pathlib import Path
from typing import Any, Protocol

import numpy as np

log = logging.getLogger("jarvis.voice")

FRAME = 1280  # samples per 80 ms frame at 16 kHz
SAMPLE_RATE = 16_000
RELEASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
MODEL_FILES = {
    "melspectrogram": "melspectrogram.onnx",
    "embedding": "embedding_model.onnx",
    "hey_jarvis": "hey_jarvis_v0.1.onnx",
}
_MEL_CONTEXT = 160 * 3  # extra samples the melspectrogram needs before each frame
_MEL_WINDOW = 76  # mel frames per embedding
_MEL_MAX = 10 * 97  # ~10 s of mel frames
_FEATURES_MAX = 120  # ~10 s of embeddings


class WakeDetector(Protocol):
    def process(self, frame: np.ndarray) -> float:
        """Score (0..1) after one 80 ms frame of int16 audio."""
        ...

    def reset(self) -> None: ...


def missing_models(directory: Path) -> list[str]:
    return [name for name in MODEL_FILES.values() if not (directory / name).is_file()]


def download_models(directory: Path, base_url: str = RELEASE) -> None:
    """Fetch the ONNX models that aren't there yet (atomic per file)."""
    import httpx2  # certifi-backed TLS, unlike urllib on some Python builds

    directory.mkdir(parents=True, exist_ok=True)
    for name in missing_models(directory):
        target = directory / name
        partial = target.with_suffix(".part")
        log.info("downloading wake-word model %s", name)
        response = httpx2.get(f"{base_url}/{name}", follow_redirects=True, timeout=60)
        response.raise_for_status()
        partial.write_bytes(response.content)
        os.replace(partial, target)


class OpenWakeWord:
    """Streaming “hey jarvis” scores from 80 ms frames of 16-bit PCM."""

    def __init__(self, directory: Path, *, seed: int | None = None) -> None:
        import onnxruntime as ort  # imported lazily: optional, ~15 MB

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1

        def session(name: str) -> Any:
            return ort.InferenceSession(
                str(directory / MODEL_FILES[name]),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )

        self._mel = session("melspectrogram")
        self._embedding = session("embedding")
        self._wake = session("hey_jarvis")
        self._wake_input = self._wake.get_inputs()[0].name
        self._wake_frames = int(self._wake.get_inputs()[0].shape[1])  # 16 embeddings
        self._rng = np.random.default_rng(seed)
        self.reset()

    def reset(self) -> None:
        self._raw: deque[int] = deque(maxlen=SAMPLE_RATE * 10)
        self._mel_buffer = np.ones((_MEL_WINDOW, 32), dtype=np.float32)
        # Upstream primes the embedding history with random low-level noise.
        noise = self._rng.integers(-1000, 1000, SAMPLE_RATE * 4).astype(np.int16)
        self._features = self._embeddings(noise)
        # Until a full model window holds real audio, scores mix in that priming
        # noise and can spike (~0.4 on plain noise) — report 0 meanwhile.
        self._warmup = self._wake_frames

    def _melspectrogram(self, samples: np.ndarray) -> np.ndarray:
        spec = np.asarray(self._mel.run(None, {"input": samples.astype(np.float32)[None, :]})[0])
        result: np.ndarray = np.squeeze(spec) / 10 + 2  # Google speech_embedding front end
        return result

    def _embed(self, windows: np.ndarray) -> np.ndarray:
        result = self._embedding.run(None, {"input_1": windows.astype(np.float32)})[0]
        return np.asarray(result).reshape(windows.shape[0], -1)

    def _embeddings(self, samples: np.ndarray) -> np.ndarray:
        spec = self._melspectrogram(samples)
        windows = [
            spec[i : i + _MEL_WINDOW]
            for i in range(0, spec.shape[0], 8)
            if spec[i : i + _MEL_WINDOW].shape[0] == _MEL_WINDOW
        ]
        return self._embed(np.array(windows)[..., None])

    def process(self, frame: np.ndarray) -> float:
        if frame.dtype != np.int16 or frame.shape != (FRAME,):
            raise ValueError("expected one 80 ms frame: 1280 int16 samples")
        self._raw.extend(frame.tolist())
        recent = np.fromiter(self._raw, dtype=np.int16)[-(FRAME + _MEL_CONTEXT) :]
        self._mel_buffer = np.vstack((self._mel_buffer, self._melspectrogram(recent)))[-_MEL_MAX:]
        window = self._mel_buffer[-_MEL_WINDOW:][None, :, :, None]
        self._features = np.vstack((self._features, self._embed(window)))[-_FEATURES_MAX:]
        features = self._features[-self._wake_frames :][None, :, :].astype(np.float32)
        score = float(np.asarray(self._wake.run(None, {self._wake_input: features})[0]).ravel()[0])
        if self._warmup > 0:
            self._warmup -= 1
            return 0.0
        return score
