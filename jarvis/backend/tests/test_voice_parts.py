"""Voice building blocks: request endpointing and the ElevenLabs client."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2
import numpy as np
import pytest

from jarvis.voice.elevenlabs import ElevenLabs, VoiceError
from jarvis.voice.endpoint import Outcome, UtteranceRecorder, level_db
from jarvis.voice.wakeword import FRAME


def tone(db: float) -> np.ndarray:
    """One 80 ms frame at roughly ``db`` dBFS."""
    amplitude = 32768 * 10 ** (db / 20) * np.sqrt(2)
    t = np.arange(FRAME) / 16000
    samples: np.ndarray = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    return samples


def feed(recorder: UtteranceRecorder, frames: list[np.ndarray]) -> list[Outcome]:
    return [recorder.feed(f) for f in frames]


def test_level() -> None:
    assert level_db(tone(-20)) == pytest.approx(-20, abs=0.5)
    assert level_db(np.zeros(FRAME, dtype=np.int16)) < -90


def test_a_request_is_captured_from_first_word_to_pause() -> None:
    recorder = UtteranceRecorder(noise_db=-62, calibration_frames=0)
    quiet, speech = [tone(-62)] * 10, [tone(-25)] * 15
    outcomes = feed(recorder, quiet + speech + [tone(-62)] * 20)
    assert Outcome.DONE in outcomes
    done_at = outcomes.index(Outcome.DONE)
    assert done_at == 10 + 15 + 11  # 0.9 s pause = 12 frames after the last loud one, 0-based
    audio = np.frombuffer(recorder.audio(), dtype="<i2")
    frames = len(audio) // FRAME
    assert frames == 4 + 15 + 2  # pre-roll + speech + a little tail


def test_nobody_speaking_ends_the_wait() -> None:
    recorder = UtteranceRecorder(start_timeout=1.0)
    outcomes = feed(recorder, [tone(-62)] * 20)
    assert outcomes[-1] is Outcome.NOTHING
    assert outcomes.index(Outcome.NOTHING) == 12  # 13 frames ≥ 1.0 s


def test_long_requests_are_cut_and_loud_rooms_raise_the_bar() -> None:
    recorder = UtteranceRecorder(max_seconds=2.0)
    outcomes = feed(recorder, [tone(-20)] * 40)
    assert outcomes.index(Outcome.DONE) == 24

    noisy = UtteranceRecorder(start_timeout=10)
    feed(noisy, [tone(-40)] * 30)  # loud room: the floor follows it
    assert noisy.threshold_db > -30
    assert noisy.feed(tone(-35)) is Outcome.LISTENING and noisy.seconds == 0


# --- ElevenLabs ---------------------------------------------------------------


class Server:
    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def client(server: Callable[[httpx2.Request], httpx2.Response]) -> ElevenLabs:
    return ElevenLabs(
        "xi-test-key",
        voice_id="onwK4e9ZLuTAKqWW03F9",
        tts_model="eleven_multilingual_v2",
        stt_model="scribe_v2",
        transport=httpx2.MockTransport(server),
    )


def error(status: int, detail: Any) -> httpx2.Response:
    return httpx2.Response(status, json={"detail": detail})


async def test_transcribe_sends_raw_pcm_to_scribe() -> None:
    server = Server(httpx2.Response(200, json={"text": " Öffne Safari. ", "language_code": "de"}))
    result = await client(server).transcribe(b"\x01\x00" * 1600)
    assert result.text == "Öffne Safari." and result.language == "de"
    request = server.requests[0]
    assert request.url.path == "/v1/speech-to-text"
    assert request.headers["xi-api-key"] == "xi-test-key"
    body = request.content.decode("latin-1")
    assert 'name="model_id"\r\n\r\nscribe_v2' in body
    assert 'name="file_format"\r\n\r\npcm_s16le_16' in body
    assert 'name="file"; filename="speech.pcm"' in body


async def test_synthesize_asks_for_raw_pcm() -> None:
    server = Server(httpx2.Response(200, content=b"\x00\x01" * 2400))
    audio = await client(server).synthesize("Safari ist offen.")
    assert len(audio) == 4800
    request = server.requests[0]
    assert request.url.path == "/v1/text-to-speech/onwK4e9ZLuTAKqWW03F9"
    assert request.url.params["output_format"] == "pcm_24000"
    assert json.loads(request.content) == {
        "text": "Safari ist offen.",
        "model_id": "eleven_multilingual_v2",
    }


async def test_key_check_accepts_restricted_keys() -> None:
    await client(Server(httpx2.Response(200, json={"subscription": {}}))).verify()
    restricted = error(401, {"status": "missing_permissions", "message": "user_read"})
    await client(Server(restricted)).verify()


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (error(401, {"status": "invalid_api_key", "message": "bad"}), "invalid_key"),
        (error(401, {"status": "quota_exceeded", "message": "0 credits"}), "quota_exceeded"),
        (error(401, {"status": "missing_permissions", "message": "tts"}), "missing_permissions"),
        (error(402, "payment"), "payment_required"),
        (error(429, {"status": "too_many_concurrent_requests"}), "rate_limited"),
        (error(500, "boom"), "service_error"),
    ],
)
async def test_errors_are_explained(response: httpx2.Response, code: str) -> None:
    with pytest.raises(VoiceError) as caught:
        await client(Server(response)).synthesize("hi")
    assert caught.value.code == code
    assert caught.value.message and "xi-test-key" not in caught.value.message


async def test_network_errors() -> None:
    def offline(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(VoiceError) as caught:
        await client(offline).transcribe(b"")
    assert caught.value.code == "connection"
