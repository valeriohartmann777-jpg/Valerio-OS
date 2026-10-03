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
    assert settings.models.fast.model == "claude-sonnet-5-5"
    assert settings.models.reasoning.model == "claude-opus-5-5"
    assert settings.models.anthropic_api_key is None  # tests never read jarvis/.env


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


def test_api_key_from_environment_is_secret() -> None:
    settings = load_settings(environ={"ANTHROPIC_API_KEY": "sk-ant-test-123"})
    key = settings.models.anthropic_api_key
    assert key is not None and key.get_secret_value() == "sk-ant-test-123"
    assert "sk-ant-test-123" not in repr(settings)
    assert "sk-ant-test-123" not in settings.model_dump_json()


def test_dotenv_parsing(tmp_path: Path) -> None:
    from jarvis.settings import read_dotenv

    env = tmp_path / ".env"
    env.write_text(
        "# comment\nexport ANTHROPIC_API_KEY=\"sk-ant-x\"\nA=1 # note\nB='q'\n\nBROKEN\n"
    )
    assert read_dotenv(env) == {"ANTHROPIC_API_KEY": "sk-ant-x", "A": "1", "B": "q"}
    assert read_dotenv(tmp_path / "missing") == {}
