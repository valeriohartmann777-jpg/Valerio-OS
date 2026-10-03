"""Central configuration.

All tunables live in ``config/*.yaml``. A small set of keys can be overridden
through environment variables so launchers and tests do not need to edit files.
Nothing else in the codebase reads environment variables or YAML directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr

from jarvis.permissions.models import ApprovalPolicy, PermissionLevel

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SystemBackendName = Literal["auto", "windows", "macos", "simulated"]
CatalogPlatform = Literal["windows", "macos"]


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    allowed_origins: list[str] = Field(default_factory=lambda: ["app://jarvis"])


class RuntimeSettings(BaseModel):
    system_backend: SystemBackendName = "auto"
    context_poll_seconds: float = 2.0
    settle_seconds: float = 3.0
    tool_timeout_seconds: float = 20.0
    launch_verify_timeout_seconds: float = 10.0
    simulated_launch_latency_ms: int = 700


class StorageSettings(BaseModel):
    database_path: Path = Path("data/jarvis.db")
    log_dir: Path = Path("data/logs")


class LoggingSettings(BaseModel):
    level: str = "INFO"


class PermissionSettings(BaseModel):
    levels: dict[PermissionLevel, ApprovalPolicy] = Field(
        default_factory=lambda: {
            PermissionLevel.READ: ApprovalPolicy.AUTO,
            PermissionLevel.SAFE_ACTION: ApprovalPolicy.AUTO,
            PermissionLevel.MODIFICATION: ApprovalPolicy.CONFIRM,
            PermissionLevel.EXTERNAL_EFFECT: ApprovalPolicy.CONFIRM,
            PermissionLevel.HIGH_RISK: ApprovalPolicy.STRONG_CONFIRM,
        }
    )
    tool_overrides: dict[str, ApprovalPolicy] = Field(default_factory=dict)
    disabled_categories: list[str] = Field(default_factory=lambda: ["financial"])
    approval_timeout_seconds: float = 300.0


class PersonalitySettings(BaseModel):
    name: str = "JARVIS"
    traits: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    address_user_as: str = ""
    responses: dict[str, str] = Field(default_factory=dict)


class AppEntry(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    launch: list[str]
    processes: list[str]
    level: PermissionLevel = PermissionLevel.SAFE_ACTION
    effects: list[str] = Field(default_factory=list)
    window_title: str | None = None


class AppCatalogSettings(BaseModel):
    # Which catalog this is: decides how unknown application names are handled.
    platform: CatalogPlatform = "windows"
    applications: dict[str, AppEntry] = Field(default_factory=dict)
    denylist: list[str] = Field(default_factory=list)


class ModelRoleSettings(BaseModel):
    provider: str = "none"
    model: str = ""
    effort: str | None = None
    max_tokens: int = 16000
    timeout_seconds: float = 60.0
    retries: int = 2


class ModelSettings(BaseModel):
    fast: ModelRoleSettings = Field(default_factory=ModelRoleSettings)
    reasoning: ModelRoleSettings = Field(default_factory=ModelRoleSettings)
    vision: ModelRoleSettings = Field(default_factory=ModelRoleSettings)
    embedding: ModelRoleSettings = Field(default_factory=ModelRoleSettings)
    speech: dict[str, str] = Field(default_factory=dict)
    refusal_fallback: bool = True
    history_turns: int = 12
    max_tool_rounds: int = 8
    # Secrets come from the environment / .env only, never from YAML.
    anthropic_api_key: SecretStr | None = None


class Settings(BaseModel):
    root_dir: Path = PROJECT_ROOT
    server: ServerSettings = Field(default_factory=ServerSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    personality: PersonalitySettings = Field(default_factory=PersonalitySettings)
    apps: AppCatalogSettings = Field(default_factory=AppCatalogSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)

    @property
    def database_path(self) -> Path:
        return self._resolve(self.storage.database_path)

    @property
    def log_dir(self) -> Path:
        return self._resolve(self.storage.log_dir)

    def _resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.root_dir / path


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def load_settings(
    config_dir: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
    **overrides: Any,
) -> Settings:
    """Load settings from ``config_dir`` and apply env + keyword overrides.

    With ``environ=None`` the process environment is used, completed by the
    project's ``.env`` file (real environment variables win). An explicit
    ``environ`` (tests) is used as-is.
    """
    if environ is None:
        process_root = Path(os.environ.get("JARVIS_ROOT", PROJECT_ROOT))
        env = {**read_dotenv(process_root / ".env"), **os.environ}
    else:
        env = environ
    root = Path(env.get("JARVIS_ROOT", PROJECT_ROOT))
    config_dir = config_dir or Path(env.get("JARVIS_CONFIG_DIR", root / "config"))

    data: dict[str, Any] = _read_yaml(config_dir / "jarvis.yaml")
    data["root_dir"] = root
    data["permissions"] = _read_yaml(config_dir / "permissions.yaml")
    data["personality"] = _read_yaml(config_dir / "personality.yaml")
    data["models"] = _read_yaml(config_dir / "models.yaml")

    _apply_env(data, env)
    platform = catalog_platform(data.get("runtime", {}).get("system_backend", "auto"))
    data["apps"] = {**_read_yaml(config_dir / f"apps.{platform}.yaml"), "platform": platform}
    for key, value in overrides.items():
        data[key] = value
    return Settings.model_validate(data)


def resolve_backend(choice: str, host_platform: str = sys.platform) -> str:
    """``auto`` → the real backend for this OS, or the simulation elsewhere."""
    if choice != "auto":
        return choice
    return {"win32": "windows", "darwin": "macos"}.get(host_platform, "simulated")


def catalog_platform(choice: str, host_platform: str = sys.platform) -> CatalogPlatform:
    """The simulation models a Windows desktop, so it uses the Windows catalog."""
    return "macos" if resolve_backend(choice, host_platform) == "macos" else "windows"


def _apply_env(data: dict[str, Any], env: dict[str, str]) -> None:
    server = data.setdefault("server", {})
    runtime = data.setdefault("runtime", {})
    storage = data.setdefault("storage", {})
    logging_ = data.setdefault("logging", {})
    if value := env.get("JARVIS_HOST"):
        server["host"] = value
    if value := env.get("JARVIS_PORT"):
        server["port"] = int(value)
    if value := env.get("JARVIS_EXTRA_ORIGINS"):
        origins = server.setdefault("allowed_origins", ["app://jarvis"])
        for origin in (o.strip() for o in value.split(",")):
            if origin and origin not in origins:
                origins.append(origin)
    if value := env.get("JARVIS_SYSTEM_BACKEND"):
        runtime["system_backend"] = value
    if value := env.get("JARVIS_DATA_DIR"):
        storage["database_path"] = str(Path(value) / "jarvis.db")
        storage["log_dir"] = str(Path(value) / "logs")
    if value := env.get("JARVIS_LOG_LEVEL"):
        logging_["level"] = value
    if value := env.get("ANTHROPIC_API_KEY", "").strip():
        data.setdefault("models", {})["anthropic_api_key"] = value


def read_dotenv(path: Path) -> dict[str, str]:
    """Minimal ``KEY=VALUE`` parser (comments, blank lines, ``export``, quotes)."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key.strip():
            values[key.strip()] = value
    return values
