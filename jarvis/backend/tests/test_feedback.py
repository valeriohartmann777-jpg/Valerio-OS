"""Training Hub feedback: the owner rates a reply; JARVIS keeps it with what was asked.

Evidence, not training — a rating changes no model, prompt or rule.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.settings import Settings
from tests.test_api import receive_until


@pytest.fixture
def client(settings: Settings) -> Any:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def ask(client: TestClient, text: str) -> dict[str, Any]:
    with client.websocket_connect("/events") as ws:
        ws.receive_json()
        assert client.post("/chat", json={"text": text}).status_code == 202
        return receive_until(ws, "jarvis.message")[-1]


def test_a_rating_keeps_the_question_the_route_and_the_note(client: TestClient) -> None:
    reply = ask(client, "Open Notepad.")
    assert client.get("/feedback").json()["items"] == []
    out = client.post(
        "/feedback", json={"event_id": reply["id"], "rating": -1, "note": "I meant TextEdit"}
    )
    assert out.status_code == 200
    item = out.json()["items"][0]
    assert item["command"] == "Open Notepad."
    assert item["route"] == "instant" and item["intent"] == "action"
    assert item["reply"] == "Notepad is open." and item["note"] == "I meant TextEdit"
    assert out.json()["ratings"] == {reply["id"]: -1}
    assert out.json()["by_route"] == [{"route": "instant", "up": 0, "down": 1}]
    # Changing one's mind replaces the rating; there is still one per reply.
    again = client.post("/feedback", json={"event_id": reply["id"], "rating": 1}).json()
    assert len(again["items"]) == 1 and again["up"] == 1 and again["down"] == 0
    assert again["items"][0]["note"] is None
    types = [e["type"] for e in client.get("/snapshot").json()["activity"]]
    assert "feedback.recorded" in types


def test_only_real_replies_and_real_ratings_are_accepted(client: TestClient) -> None:
    reply = ask(client, "Open Notepad.")
    assert client.post("/feedback", json={"event_id": reply["id"], "rating": 0}).status_code == 422
    missing = client.post("/feedback", json={"event_id": "evt-nope", "rating": 1})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "NOT_FOUND"
    # A command event isn't a reply: feedback is about what JARVIS said.
    command = client.get("/snapshot").json()["activity"]
    asked = next(e for e in command if e["type"] == "command.received")
    assert client.post("/feedback", json={"event_id": asked["id"], "rating": 1}).status_code == 404


def test_behaviour_check_runs_the_curated_cases_offline_and_stores_the_run(
    client: TestClient,
) -> None:
    before = client.get("/training/evals").json()
    assert before["last_run"] is None and len(before["cases"]) >= 10
    assert {c["source"] for c in before["cases"]} == {"curated"}
    out = client.post("/training/evals/run").json()["last_run"]
    assert out["total"] == len(before["cases"])
    failed = [r for r in out["results"] if not r["passed"]]
    assert out["passed"] == out["total"], failed  # the shipped cases describe today's router
    assert client.get("/training/evals").json()["last_run"]["at"] == out["at"]
    types = [e["type"] for e in client.get("/snapshot").json()["activity"]]
    assert "evaluation.completed" in types


def test_a_good_reply_can_be_pinned_as_a_case_and_removed(client: TestClient) -> None:
    reply = ask(client, "Open Notepad.")
    refused = client.post("/training/evals/cases", json={"event_id": reply["id"]})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "NOT_APPROVED"
    client.post("/feedback", json={"event_id": reply["id"], "rating": 1})
    pinned = client.post("/training/evals/cases", json={"event_id": reply["id"]}).json()
    [case] = pinned["pinned"]
    assert case["text"] == "Open Notepad." and pinned["pinned_events"] == [reply["id"]]
    assert case["expect"] == {"kind": "action", "tool": "open_application", "targets": ["notepad"]}
    # Pinning twice keeps one case.
    again = client.post("/training/evals/cases", json={"event_id": reply["id"]}).json()
    assert len(again["pinned"]) == 1
    run = client.post("/training/evals/run").json()["last_run"]
    mine = next(r for r in run["results"] if r["id"] == case["id"])
    assert mine["passed"] and mine["source"] == "pinned"
    assert client.delete(f"/training/evals/cases/{case['id']}").json()["pinned"] == []
    assert client.delete(f"/training/evals/cases/{case['id']}").status_code == 404


def test_a_case_fails_on_the_field_that_differs() -> None:
    from jarvis.core.router import RuleBasedRouter
    from jarvis.feedback.evals import compare, understood

    got = understood(RuleBasedRouter(), "open notepad")
    assert compare({"kind": "action", "targets": ["NotePad"]}, got) == []
    assert compare({"kind": "query", "tool": "open_application"}, got) == ["kind"]
    assert compare({"targets": ["notepad", "paint"]}, got) == ["targets"]


def test_settings_and_training_hub_data_survive_a_restart(settings: Settings) -> None:
    """Acceptance 8: what the owner set is still there after JARVIS restarts — and keys
    never travel through these settings."""
    with TestClient(create_app(settings)) as first:
        first.post("/briefing/preferences", json={"enabled": True, "time": "07:15"})
        paid = {"monthly_budget_usd": 20, "per_mission_cap_usd": 5, "confirm": True}
        assert first.post("/ai/paid", json=paid).status_code == 200
        first.post("/memory", json={"text": "Prefers short answers.", "kind": "preference"})
        reply = ask(first, "What time is it?")
        first.post("/feedback", json={"event_id": reply["id"], "rating": 1})
    with TestClient(create_app(settings)) as again:
        briefing = again.get("/briefing").json()
        assert briefing["enabled"] is True and briefing["time"] == "07:15"
        api = again.get("/ai/status").json()["providers"]["api"]
        assert api["paid"]["enabled"] is True and api["paid"]["monthly_budget_usd"] == 20
        assert [m["text"] for m in again.get("/memory").json()] == ["Prefers short answers."]
        assert again.get("/feedback").json()["ratings"] == {reply["id"]: 1}
