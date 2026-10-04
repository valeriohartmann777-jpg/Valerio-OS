"""Where a spoken request starts and ends — energy-based, no model needed.

The noise floor adapts while nobody speaks; speech is a frame clearly above it.
Without a known floor (push-to-talk), the first frames calibrate it and only
clearly loud speech can start a request during that time.
A request ends after a pause, or when it gets too long. A little audio before
the first loud frame is kept, so the first syllable isn't cut off.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

FRAME_SECONDS = 0.08  # 1280 samples at 16 kHz


def level_db(frame: np.ndarray) -> float:
    """RMS level in dBFS (silence ≈ -96)."""
    rms = float(np.sqrt(np.mean(np.square(frame.astype(np.float64))))) if frame.size else 0.0
    return float(20 * np.log10(max(rms, 0.5) / 32768))


class Outcome(StrEnum):
    LISTENING = "listening"  # keep feeding frames
    DONE = "done"  # a request was captured
    NOTHING = "nothing"  # nobody spoke in time


@dataclass
class UtteranceRecorder:
    noise_db: float = -60.0  # starting guess; adapts before speech begins
    margin_db: float = 12.0  # speech = this much above the noise floor …
    min_speech_db: float = -48.0  # … and at least this loud
    start_timeout: float = 5.0  # seconds to start speaking
    end_silence: float = 0.9  # pause that ends a request
    max_seconds: float = 15.0
    preroll_frames: int = 4
    calibration_frames: int = 3  # 0 when noise_db was measured beforehand (wake word)
    obvious_speech_db: float = -30.0  # starts a request even while calibrating
    _preroll: deque[np.ndarray] = field(default_factory=deque, repr=False)
    _frames: list[np.ndarray] = field(default_factory=list, repr=False)
    _elapsed: float = 0.0
    _silence: float = 0.0
    _speaking: bool = False
    _calibrated: bool = False

    @property
    def threshold_db(self) -> float:
        return max(self.noise_db + self.margin_db, self.min_speech_db)

    def feed(self, frame: np.ndarray) -> Outcome:
        self._elapsed += FRAME_SECONDS
        level = level_db(frame)
        loud = level >= self.threshold_db
        if not self._speaking:
            if self.calibration_frames > 0:
                self.calibration_frames -= 1
                loud = level >= self.obvious_speech_db
                if not loud:  # measure the room, not the speaker
                    self.noise_db = level if not self._calibrated else min(self.noise_db, level)
                    self._calibrated = True
            if loud:
                self._speaking = True
                self._frames = [*self._preroll, frame]
                return Outcome.LISTENING
            self.noise_db = 0.9 * self.noise_db + 0.1 * level  # follow the room
            self._preroll.append(frame)
            while len(self._preroll) > self.preroll_frames:
                self._preroll.popleft()
            return Outcome.NOTHING if self._elapsed >= self.start_timeout else Outcome.LISTENING
        self._frames.append(frame)
        self._silence = 0.0 if loud else self._silence + FRAME_SECONDS
        if self._silence >= self.end_silence or self._elapsed >= self.max_seconds:
            return Outcome.DONE
        return Outcome.LISTENING

    def audio(self) -> bytes:
        """The captured request as 16 kHz mono 16-bit PCM, trailing pause trimmed."""
        keep = len(self._frames) - int(self._silence / FRAME_SECONDS) + 2
        frames = self._frames[: max(keep, 1)]
        return b"".join(f.astype("<i2").tobytes() for f in frames)

    @property
    def seconds(self) -> float:
        return len(self._frames) * FRAME_SECONDS
