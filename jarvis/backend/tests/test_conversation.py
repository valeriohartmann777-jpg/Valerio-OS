"""The chat history survives restarts and pages backwards."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.runtime import Runtime
from tests.conftest import make_settings


def test_conversation_is_kept_across_restarts_and_pages_back(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(runtime=Runtime(settings))) as client:
        for count, text in enumerate(("hello", "system status", "what's the active window")):
            client.post("/chat", json={"text": text})
            for _ in range(500):  # one exchange at a time
                if len(client.get("/conversation").json()) == 2 * (count + 1):
                    break

    with TestClient(create_app(runtime=Runtime(settings))) as client:  # a restart
        history = client.get("/conversation").json()
        kinds = [(e["type"], e["payload"].get("text", "")[:6]) for e in history]
        assert [k for k, _ in kinds] == ["command.received", "jarvis.message"] * 3
        assert history[0]["payload"]["text"] == "hello"
        assert history[1]["payload"]["text"] == "Online. Everything is nominal."
        assert history[0]["payload"]["source"] == "text"

        last_two = client.get("/conversation", params={"limit": 2}).json()
        assert [e["id"] for e in last_two] == [e["id"] for e in history[-2:]]
        earlier = client.get(
            "/conversation", params={"limit": 10, "before": last_two[0]["timestamp"]}
        ).json()
        assert [e["id"] for e in earlier] == [e["id"] for e in history[:-2]]
        assert client.get("/conversation", params={"before": "yesterday"}).status_code == 422
