"""Decoding untrusted media with PyAV (bundled FFmpeg) — runs only inside the worker process.

Nothing here touches the network or the database. Every function bounds what it
reads: duration, resolution, frame rate and the number of samples it keeps.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import av
import numpy as np
from PIL import Image

SAMPLE_RATE = 16_000
MAX_SIDE = 4096
MAX_FPS = 240.0


class MediaError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Probe:
    container: str
    duration_ms: int
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    audio_rate: int | None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _open(path: str) -> Any:
    try:
        return av.open(path, mode="r")
    except av.FFmpegError as exc:
        raise MediaError(
            "MEDIA_UNREADABLE", f"The file can't be decoded ({type(exc).__name__})."
        ) from exc


def probe(path: str, max_ms: int) -> Probe:
    with _open(path) as container:
        video = container.streams.video[0] if container.streams.video else None
        audio = container.streams.audio[0] if container.streams.audio else None
        if video is None and audio is None:
            raise MediaError("NO_MEDIA_STREAMS", "The file has no audio or video stream.")
        duration_ms = int(container.duration / 1000) if container.duration else 0
        if not duration_ms:
            for stream in (video, audio):
                if stream is not None and stream.duration and stream.time_base:
                    duration_ms = max(duration_ms, int(stream.duration * stream.time_base * 1000))
        if duration_ms <= 0:
            raise MediaError("NO_DURATION", "The file's duration can't be determined.")
        if duration_ms > max_ms:
            raise MediaError(
                "TOO_LONG",
                f"The video is {duration_ms / 60000:.1f} minutes long; the limit is "
                f"{max_ms / 60000:.0f} minutes.",
            )
        width = height = None
        fps: float | None = None
        if video is not None:
            width, height = video.codec_context.width, video.codec_context.height
            if not width or not height or width > MAX_SIDE or height > MAX_SIDE:
                raise MediaError("BAD_RESOLUTION", f"Unsupported resolution {width}×{height}.")
            rate = video.average_rate or video.guessed_rate
            fps = float(rate) if rate else None
            if fps is not None and fps > MAX_FPS:
                raise MediaError("BAD_FRAME_RATE", f"Unsupported frame rate {fps:.0f} fps.")
        return Probe(
            container=str(container.format.name),
            duration_ms=duration_ms,
            has_video=video is not None,
            has_audio=audio is not None,
            width=width,
            height=height,
            fps=round(fps, 3) if fps else None,
            video_codec=video.codec_context.name if video is not None else None,
            audio_codec=audio.codec_context.name if audio is not None else None,
            audio_rate=audio.codec_context.sample_rate if audio is not None else None,
        )


def decode_audio(path: str, max_ms: int) -> np.ndarray:
    """The first audio stream as 16 kHz mono float32, at most ``max_ms`` long."""
    limit = int(max_ms / 1000 * SAMPLE_RATE)
    parts: list[np.ndarray] = []
    total = 0
    with _open(path) as container:
        if not container.streams.audio:
            return np.zeros(0, dtype=np.float32)
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        try:
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    data = out.to_ndarray().reshape(-1)
                    parts.append(data)
                    total += data.size
                if total >= limit:
                    break
            for out in resampler.resample(None):
                parts.append(out.to_ndarray().reshape(-1))
        except av.FFmpegError as exc:
            if not parts:
                raise MediaError(
                    "AUDIO_UNREADABLE", f"The audio can't be decoded ({type(exc).__name__})."
                ) from exc
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.ascontiguousarray(np.concatenate(parts)[:limit], dtype=np.float32)


def sample_frames(
    path: str, interval_ms: int, max_samples: int
) -> Iterator[tuple[int, Image.Image]]:
    """One decoded frame at (or just after) every ``interval_ms``, in order."""
    with _open(path) as container:
        if not container.streams.video:
            return
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        next_ms = 0
        taken = 0
        try:
            for frame in container.decode(stream):
                if frame.time is None:
                    continue
                t_ms = int(frame.time * 1000)
                if t_ms < next_ms:
                    continue
                yield t_ms, frame.to_image()
                taken += 1
                if taken >= max_samples:
                    return
                next_ms = t_ms + interval_ms
        except av.FFmpegError as exc:
            if taken == 0:
                raise MediaError(
                    "VIDEO_UNREADABLE", f"The video frames can't be decoded ({type(exc).__name__})."
                ) from exc


def signature(image: Image.Image) -> np.ndarray:
    """A tiny grayscale thumbnail used to notice scene and overlay changes."""
    small = image.convert("L").resize((32, 56))
    return np.asarray(small, dtype=np.float32) / 255.0


def change(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))
