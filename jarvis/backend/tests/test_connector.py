"""Connecting the brain from the dashboard: verify → OS keystore → route (D-030)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from jarvis.api.app import create_app
from jarvis.core.connector import normalize_key
from jarvis.events.types import EventType
from jarvis.llm.base import ModelError
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


PAID = {"monthly_budget_usd": 20.0, "per_mission_cap_usd": 5.0}


async def approve(rt: Runtime) -> None:
    await rt.ai.approve_paid(PAID, confirm=True)
    await rt.refresh_brain()


async def test_connect_verifies_stores_in_the_keystore_and_needs_approval(
    settings: Settings,
) -> None:
    env = settings.env_file
    env.write_text("# mine\nJARVIS_LOG_LEVEL=DEBUG\n")
    verifier = FakeVerifier()
    rt = await started(settings, verifier)
    try:
        assert not rt.brain.available
        events = Recorder(rt.bus)

        status = await rt.connector.connect(f"ANTHROPIC_API_KEY={KEY}")

        assert verifier.calls == [(KEY, ["claude-sonnet-5-5", "claude-opus-5-5"])]
        # Stored in the keystore, never in a file; the .env file is untouched.
        assert rt.ai_keys.source() == "keystore" and rt.ai_keys.get() == KEY
        assert env.read_text() == "# mine\nJARVIS_LOG_LEVEL=DEBUG\n"
        # A saved key alone never spends money: paid fallback needs the owner's approval.
        assert not status.available and status.key_hint == "AbCd"
        assert "isn't approved" in (status.reason or "")

        await approve(rt)
        assert rt.brain.available
        assert rt.brain.status.fast_model == "claude-sonnet-5-5"
        changed = events.of(EventType.BRAIN_CHANGED)
        assert changed and all(KEY not in e.model_dump_json() for e in events.events)
    finally:
        await rt.stop()


async def test_rejected_key_changes_nothing(settings: Settings) -> None:
    rt = await started(settings, FakeVerifier(REJECTED))
    try:
        with pytest.raises(ModelError) as info:
            await rt.connector.connect(KEY)
        assert info.value.code == "authentication"
        assert not settings.env_file.exists()
        assert rt.ai_keys.get() is None
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


def with_env_key(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path)
    return settings.model_copy(
        update={"models": settings.models.model_copy(update={"anthropic_api_key": SecretStr(KEY)})}
    )


async def test_startup_check_takes_a_rejected_key_offline(tmp_path: Path) -> None:
    verifier = FakeVerifier()
    rt = Runtime(with_env_key(tmp_path), key_verifier=verifier)
    await rt.start()
    try:
        await approve(rt)
        assert rt.brain.available
        events = Recorder(rt.bus)
        verifier.error = REJECTED
        await rt.connector.check()
        status = rt.brain.status
        assert not status.available
        assert status.key_hint == "AbCd"
        assert "rejected" in (status.reason or "")
        assert events.of(EventType.BRAIN_CHANGED)
    finally:
        await rt.stop()


async def test_startup_check_keeps_the_brain_when_offline(tmp_path: Path) -> None:
    verifier = FakeVerifier()
    rt = Runtime(with_env_key(tmp_path), key_verifier=verifier)
    await rt.start()
    try:
        await approve(rt)
        verifier.error = ModelError(
            "connection", "I can't reach the model provider.", retryable=True
        )
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
        assert body["key_hint"] == "AbCd" and body["available"] is False  # not approved yet
        assert KEY not in ok.text
        # Paid fallback needs confirm: true — and only the owner's UI sends it.
        assert client.post("/ai/paid", json=PAID).status_code == 422
        approved = client.post("/ai/paid", json={**PAID, "confirm": True})
        assert approved.status_code == 200 and KEY not in approved.text
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
