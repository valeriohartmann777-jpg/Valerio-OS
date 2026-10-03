"""Connecting the brain from the dashboard: verify → store in .env → switch on."""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from jarvis.api.app import create_app
from jarvis.core.connector import normalize_key
from jarvis.events.types import EventType, Severity
from jarvis.llm.base import ModelError
from jarvis.llm.registry import ModelSet, build_models
from jarvis.runtime import Runtime
from jarvis.settings import Settings, read_dotenv, write_dotenv_value
from tests.conftest import Recorder, make_settings

KEY = "sk-ant-api03-" + "x" * 40 + "AbCd"


class FakeVerifier:
    """Stands in for the provider check; rejects with ``error`` if set."""

    def __init__(self, error: ModelError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, list[str]]] = []

    async def __call__(self, key: str, models: list[str]) -> None:
        self.calls.append((key, models))
        if self.error is not None:
            raise self.error


REJECTED = ModelError("authentication", "The API key was rejected.", suggestion="Paste a new one.")


async def started(settings: Settings, verifier: FakeVerifier, **kwargs: Any) -> Runtime:
    rt = Runtime(settings, key_verifier=verifier, **kwargs)
    await rt.start()
    return rt


@pytest.mark.parametrize(
    "raw",
    [
        KEY,
        f"  {KEY}\n",
        f"ANTHROPIC_API_KEY={KEY}",
        f"export ANTHROPIC_API_KEY='{KEY}'",
        f"“{KEY}”",
    ],
)
def test_normalize_accepts_what_people_paste(raw: str) -> None:
    assert normalize_key(raw) == KEY


async def test_connect_verifies_stores_and_switches_the_brain_on(settings: Settings) -> None:
    env = settings.env_file
    env.write_text("# mine\nJARVIS_LOG_LEVEL=DEBUG\nANTHROPIC_API_KEY=sk-ant-old\n")
    verifier = FakeVerifier()
    rt = await started(settings, verifier)
    try:
        assert not rt.brain.available
        events = Recorder(rt.bus)

        status = await rt.connector.connect(f"ANTHROPIC_API_KEY={KEY}")

        assert verifier.calls == [(KEY, ["claude-sonnet-5-5", "claude-opus-5-5"])]
        assert status.available and status.fast_model == "claude-sonnet-5-5"
        assert status.key_hint == "AbCd"
        assert rt.brain.available
        # Old definition replaced in place, everything else kept.
        assert env.read_text() == f"# mine\nJARVIS_LOG_LEVEL=DEBUG\nANTHROPIC_API_KEY={KEY}\n"
        assert read_dotenv(env)["ANTHROPIC_API_KEY"] == KEY
        if sys.platform != "win32":
            assert stat.S_IMODE(env.stat().st_mode) == 0o600

        [event] = events.of(EventType.BRAIN_CHANGED)
        assert event.message == "Brain connected — Claude Sonnet 5.5"
        assert event.payload["brain"]["available"] is True
        assert KEY not in event.model_dump_json()
    finally:
        await rt.stop()


async def test_rejected_key_changes_nothing(settings: Settings) -> None:
    rt = await started(settings, FakeVerifier(REJECTED))
    try:
        with pytest.raises(ModelError) as info:
            await rt.connector.connect(KEY)
        assert info.value.code == "authentication"
        assert not settings.env_file.exists()
        assert not rt.brain.available
    finally:
        await rt.stop()


@pytest.mark.parametrize("raw", ["", "   ", "hello", "sk-ant-short", f"{KEY} trailing words"])
async def test_malformed_keys_are_refused_before_any_network_call(
    settings: Settings, raw: str
) -> None:
    verifier = FakeVerifier()
    rt = await started(settings, verifier)
    try:
        with pytest.raises(ModelError) as info:
            await rt.connector.connect(raw)
        assert info.value.code == "invalid_format"
        assert verifier.calls == []
    finally:
        await rt.stop()


def keyed_models(settings: Settings) -> ModelSet:
    return build_models(settings.models.model_copy(update={"anthropic_api_key": SecretStr(KEY)}))


async def test_startup_check_takes_a_rejected_key_offline(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={"models": settings.models.model_copy(update={"anthropic_api_key": SecretStr(KEY)})}
    )
    rt = Runtime(settings, key_verifier=FakeVerifier(REJECTED))
    events = Recorder(rt.bus)
    assert rt.brain.available
    await rt.start()
    try:
        await rt.connector.check()
        status = rt.brain.status
        assert not status.available
        assert status.key_hint == "AbCd"
        assert status.reason == "The API key was rejected. Paste a new one."
        [event] = events.of(EventType.BRAIN_CHANGED)
        assert event.severity == Severity.WARNING
    finally:
        await rt.stop()


async def test_startup_check_keeps_the_brain_when_offline(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={"models": settings.models.model_copy(update={"anthropic_api_key": SecretStr(KEY)})}
    )
    no_network = ModelError("connection", "I can't reach the model provider.", retryable=True)
    rt = Runtime(settings, key_verifier=FakeVerifier(no_network))
    await rt.start()
    try:
        await rt.connector.check()
        assert rt.brain.available
    finally:
        await rt.stop()


def test_api_connects_and_explains_failures(settings: Settings) -> None:
    verifier = FakeVerifier(REJECTED)
    runtime = Runtime(settings, key_verifier=verifier)
    with TestClient(create_app(runtime=runtime)) as client:
        assert client.get("/snapshot").json()["brain"]["available"] is False

        bad = client.post("/brain/key", json={"api_key": "nope"})
        assert bad.status_code == 422
        assert bad.json()["detail"]["code"] == "invalid_format"

        rejected = client.post("/brain/key", json={"api_key": KEY})
        assert rejected.status_code == 422
        assert rejected.json()["detail"] == {
            "code": "authentication",
            "message": "The API key was rejected.",
            "suggestion": "Paste a new one.",
        }

        evil = {"origin": "https://evil.example"}
        assert client.post("/brain/key", json={"api_key": KEY}, headers=evil).status_code == 403

        verifier.error = None
        ok = client.post("/brain/key", json={"api_key": KEY})
        assert ok.status_code == 200
        body = ok.json()
        assert body["available"] is True and body["key_hint"] == "AbCd"
        assert KEY not in ok.text
        assert client.get("/snapshot").json()["brain"]["available"] is True


def test_dotenv_writer_refuses_values_that_would_break_the_file(tmp_path: Path) -> None:
    for value in ("", "a b", "a\nB=evil", "a#b", 'a"b'):
        with pytest.raises(ValueError):
            write_dotenv_value(tmp_path / ".env", "K", value)
    write_dotenv_value(tmp_path / ".env", "K", "v")
    assert (tmp_path / ".env").read_text() == "K=v\n"


def test_dotenv_reader_tolerates_editor_artifacts(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_bytes("﻿ANTHROPIC_API_KEY = “sk-ant-x”\r\nexport B = 2\r\n".encode())
    assert read_dotenv(env) == {"ANTHROPIC_API_KEY": "sk-ant-x", "B": "2"}
