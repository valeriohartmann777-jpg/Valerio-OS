"""ElevenLabs speech-to-text (Scribe) and text-to-speech over its REST API.

- STT: ``POST /v1/speech-to-text`` with raw 16 kHz PCM (``file_format=pcm_s16le_16``,
  no encoding step, lower latency).
- TTS: ``POST /v1/text-to-speech/{voice}?output_format=pcm_24000`` — raw PCM that
  plays without a decoder.
- Key check: ``GET /v1/user``. A key restricted to fewer permissions still
  counts as valid; a missing permission is reported when it's needed.

Errors become ``VoiceError`` with a reason the user can act on. The API key is
sent only to api.elevenlabs.io and never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx2

API = "https://api.elevenlabs.io"


class VoiceError(Exception):
    def __init__(
        self, code: str, message: str, *, suggestion: str | None = None, detail: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str | None


class ElevenLabs:
    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str,
        tts_model: str,
        stt_model: str,
        timeout_seconds: float = 30.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.voice_id = voice_id
        self._tts_model = tts_model
        self._stt_model = stt_model
        self._client = httpx2.AsyncClient(
            base_url=API,
            headers={"xi-api-key": api_key},
            timeout=timeout_seconds,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def verify(self) -> None:
        response = await self._send("GET", "/v1/user", feature="your account")
        if response is not None and response.status_code >= 400:
            raise _error(response, "your account")

    async def transcribe(self, pcm16k: bytes) -> Transcript:
        response = await self._send(
            "POST",
            "/v1/speech-to-text",
            feature="Speech to Text",
            data={"model_id": self._stt_model, "file_format": "pcm_s16le_16"},
            files={"file": ("speech.pcm", pcm16k, "application/octet-stream")},
        )
        assert response is not None
        body = response.json()
        return Transcript(
            text=str(body.get("text") or "").strip(), language=body.get("language_code")
        )

    async def synthesize(self, text: str) -> bytes:
        """Speech for ``text`` as 24 kHz mono 16-bit PCM."""
        response = await self._send(
            "POST",
            f"/v1/text-to-speech/{self.voice_id}",
            feature="Text to Speech",
            params={"output_format": "pcm_24000"},
            json={"text": text, "model_id": self._tts_model},
        )
        assert response is not None
        audio: bytes = response.content
        return audio

    async def _send(self, method: str, path: str, *, feature: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx2.TimeoutException as exc:
            raise VoiceError(
                "timeout", "ElevenLabs took too long to answer.", suggestion="Try again."
            ) from exc
        except httpx2.HTTPError as exc:
            raise VoiceError(
                "connection",
                "I can't reach ElevenLabs.",
                suggestion="Check the internet connection.",
                detail=str(exc),
            ) from exc
        if response.status_code >= 400:
            error = _error(response, feature)
            if error.code == "missing_permissions" and path == "/v1/user":
                return None  # a restricted key is still a valid key
            raise error
        return response


def _detail(response: httpx2.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return "", response.text[:300]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("status") or ""), str(detail.get("message") or "")
    return "", str(detail or body)[:300]


def _error(response: httpx2.Response, feature: str) -> VoiceError:
    status, message = _detail(response)
    code = response.status_code
    if status == "quota_exceeded":
        return VoiceError(
            "quota_exceeded",
            "Your ElevenLabs credits are used up.",
            suggestion="Top up or upgrade at elevenlabs.io, or wait for the monthly reset.",
            detail=message,
        )
    if status in ("missing_permissions", "insufficient_permissions"):
        return VoiceError(
            "missing_permissions",
            f"The ElevenLabs key isn't allowed to use {feature}.",
            suggestion="In ElevenLabs → API keys, allow Text to Speech and Speech to Text.",
            detail=message,
        )
    if code == 401:
        return VoiceError(
            "invalid_key",
            "ElevenLabs rejected the API key.",
            suggestion="Copy the key again from elevenlabs.io → API keys.",
            detail=message,
        )
    if code == 402:
        return VoiceError(
            "payment_required",
            "ElevenLabs needs a paid plan for this.",
            suggestion="Check your plan at elevenlabs.io.",
            detail=message,
        )
    if code == 429:
        return VoiceError(
            "rate_limited",
            "ElevenLabs is busy or rate-limiting this key.",
            suggestion="Try again in a moment.",
            detail=message,
        )
    if code == 422 or code == 400:
        return VoiceError("bad_request", "ElevenLabs couldn't process the request.", detail=message)
    return VoiceError(
        "service_error",
        f"ElevenLabs had a problem ({code}).",
        suggestion="Try again.",
        detail=message,
    )
