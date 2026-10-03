from __future__ import annotations

from pathlib import Path

from jarvis.permissions.models import ApprovalPolicy, PermissionLevel
from jarvis.settings import load_settings


def test_repository_config_loads() -> None:
    settings = load_settings(environ={})
    assert settings.server.host == "127.0.0.1"
    assert settings.permissions.levels[PermissionLevel.HIGH_RISK] is ApprovalPolicy.STRONG_CONFIRM
    assert "notepad" in settings.apps.applications
    assert settings.personality.responses["app_opened"] == "{app} is open."
    assert settings.models.fast.provider == "none"


def test_environment_overrides(tmp_path: Path) -> None:
    settings = load_settings(
        environ={
            "JARVIS_PORT": "9999",
            "JARVIS_SYSTEM_BACKEND": "simulated",
            "JARVIS_DATA_DIR": str(tmp_path),
            "JARVIS_EXTRA_ORIGINS": "http://127.0.0.1:5174, http://127.0.0.1:5173",
        }
    )
    assert settings.server.allowed_origins.count("http://127.0.0.1:5173") == 1
    assert "http://127.0.0.1:5174" in settings.server.allowed_origins
    assert settings.server.port == 9999
    assert settings.runtime.system_backend == "simulated"
    assert settings.database_path == tmp_path / "jarvis.db"
