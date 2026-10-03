from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.api.app import create_app
from jarvis.settings import Settings


@pytest.fixture
def client(settings: Settings) -> Any:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def receive_until(ws: Any, event_type: str, limit: int = 200) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(limit):
        message = ws.receive_json()
        if message["kind"] != "event":
            continue
        events.append(message["data"])
        if message["data"]["type"] == event_type:
            return events
    raise AssertionError(f"{event_type} not received")


def test_health_and_status(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    status = client.get("/system/status").json()
    assert status["state"]["state"] == "DORMANT"
    assert status["simulated"] is True


def test_snapshot_shape(client: TestClient) -> None:
    snapshot = client.get("/snapshot").json()
    assert {a["id"] for a in snapshot["agents"]} >= {"operator", "sentinel", "atlas", "forge"}
    assert snapshot["context"]["system_backend"] == "simulated"
    assert snapshot["activity"][0]["type"] == "system.online"


def test_websocket_streams_the_open_notepad_chain(client: TestClient) -> None:
    with client.websocket_connect("/events") as ws:
        first = ws.receive_json()
        assert first["kind"] == "snapshot"
        accepted = client.post("/chat", json={"text": "Open Notepad."})
        assert accepted.status_code == 202
        trace_id = accepted.json()["trace_id"]
        events = receive_until(ws, "jarvis.message")
    types = [e["type"] for e in events]
    for expected in (
        "command.received",
        "intent.classified",
        "mission.created",
        "tool.completed",
        "verification.completed",
    ):
        assert expected in types
    assert events[-1]["payload"]["text"] == "Notepad is open."
    assert all(e["trace_id"] == trace_id for e in events if e["type"] != "context.updated")


def test_websocket_ping(client: TestClient) -> None:
    with client.websocket_connect("/events") as ws:
        ws.receive_json()
        ws.send_json({"kind": "ping"})
        while ws.receive_json()["kind"] != "pong":
            pass


def test_approval_endpoints(client: TestClient) -> None:
    with client.websocket_connect("/events") as ws:
        ws.receive_json()
        client.post("/chat", json={"text": "open regedit"})
        requested = receive_until(ws, "permission.requested")[-1]["payload"]["request"]
        assert requested["action"]["level"] == 4
        weak = client.post(f"/permissions/{requested['id']}/approve", json={})
        assert weak.status_code == 409
        strong = client.post(
            f"/permissions/{requested['id']}/approve", json={"strong_confirmation": True}
        )
        assert strong.status_code == 200
        message = receive_until(ws, "jarvis.message")[-1]
    assert message["payload"]["text"] == "Registry Editor is open."
    assert client.post("/permissions/unknown/reject", json={}).status_code == 404


def test_mission_endpoints(client: TestClient) -> None:
    with client.websocket_connect("/events") as ws:
        ws.receive_json()
        created = client.post("/missions", json={"goal": "open paint"})
        assert created.status_code == 202
        mission_id = created.json()["id"]
        receive_until(ws, "jarvis.message")
    mission = client.get(f"/missions/{mission_id}").json()
    assert mission["status"] == "complete"
    assert client.get("/missions").json()[0]["id"] == mission_id
    assert client.post(f"/missions/{mission_id}/pause").status_code == 409
    assert client.post(f"/missions/{mission_id}/explode").status_code == 404
    assert client.get("/missions/nope").status_code == 404
    assert client.post("/missions", json={"goal": "write a poem"}).status_code == 422


def test_origin_guard(client: TestClient) -> None:
    evil = {"origin": "https://evil.example"}
    assert client.post("/chat", json={"text": "open notepad"}, headers=evil).status_code == 403
    assert client.get("/health", headers=evil).status_code == 200
    allowed = {"origin": "app://jarvis"}
    assert client.post("/chat", json={"text": "hello"}, headers=allowed).status_code == 202
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/events", headers=evil) as ws,
    ):
        ws.receive_json()


def test_settings_and_tools_are_secret_free(client: TestClient) -> None:
    settings = client.get("/settings").json()
    assert settings["permission_levels"][4]["policy"] == "strong_confirm"
    assert "financial" in settings["disabled_categories"]
    assert "Notepad" in settings["known_apps"]
    tools = {t["name"]: t for t in client.get("/tools").json()}
    assert tools["open_application"]["side_effects"] is True
    assert tools["get_system_info"]["permission_level"] == 0
