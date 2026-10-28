"""Health that means something: live, ready (real checks) and capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from tests.conftest import make_settings


def test_live_ready_and_capabilities_answer_from_real_checks(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        live = client.get("/health/live")
        assert live.status_code == 200 and live.json()["status"] == "live"

        ready = client.get("/health/ready")
        body = ready.json()
        assert ready.status_code == 200 and body["status"] == "ready", body
        assert {c["name"] for c in body["checks"]} == {"database", "schema", "data_folder", "apex"}
        assert all(c["ok"] for c in body["checks"])
        assert "latest" in next(c for c in body["checks"] if c["name"] == "schema")["detail"]

        caps = client.get("/system/capabilities").json()
        assert caps["apex"]["orders"] is False
        assert {m["market"] for m in caps["apex"]["markets"]} == {"XAUUSD", "NQ", "MNQ"}
        assert caps["events"]["websocket"] == "/events"


def test_ready_reports_a_failing_component_by_name(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        runtime: Any = client.app.state.runtime  # type: ignore[attr-defined]

        async def broken(sql: str, params: Any = ()) -> None:
            raise OSError("disk I/O error")

        original = runtime.db.fetch_one
        runtime.db.fetch_one = broken
        try:
            ready = client.get("/health/ready")
        finally:
            runtime.db.fetch_one = original
        body = ready.json()
        assert ready.status_code == 503 and body["status"] == "degraded"
        failed = {c["name"]: c["detail"] for c in body["checks"] if not c["ok"]}
        assert set(failed) == {"database", "schema"} and "disk I/O error" in failed["database"]
        # the process itself is still live
        assert client.get("/health/live").status_code == 200
