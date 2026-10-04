"""Wake-word engine with the real openWakeWord models (downloaded once, cached).

The port was checked against the upstream openwakeword package: identical
scores (max difference 0.0) on the same audio. These tests guard the
plumbing; they skip when the models can't be fetched (offline).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from jarvis.voice.wakeword import FRAME, MODEL_FILES, download_models, missing_models

CACHE = Path.home() / ".cache" / "jarvis-test-wakeword"


@pytest.fixture(scope="module")
def models() -> Path:
    pytest.importorskip("onnxruntime")
    try:
        download_models(CACHE)
    except Exception as exc:  # offline: nothing to test against
        pytest.skip(f"wake-word models unavailable: {exc}")
    return CACHE


def test_missing_models_lists_what_to_download(tmp_path: Path) -> None:
    assert sorted(missing_models(tmp_path)) == sorted(MODEL_FILES.values())
    for name in MODEL_FILES.values():
        (tmp_path / name).write_bytes(b"x")
    assert missing_models(tmp_path) == []


def test_scores_stay_low_for_noise_and_frames_are_checked(models: Path) -> None:
    from jarvis.voice.wakeword import OpenWakeWord

    detector = OpenWakeWord(models, seed=1)
    rng = np.random.default_rng(3)
    scores = [
        detector.process(rng.normal(0, 600, FRAME).clip(-32768, 32767).astype(np.int16))
        for _ in range(40)
    ]
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert max(scores) < 0.2
    with pytest.raises(ValueError):
        detector.process(np.zeros(100, dtype=np.int16))
    detector.reset()
    assert detector.process(np.zeros(FRAME, dtype=np.int16)) < 0.2
