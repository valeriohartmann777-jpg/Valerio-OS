"""Central configuration.

All tunables live in ``config/*.yaml``. A small set of keys can be overridden
through environment variables so launchers and tests do not need to edit files.
Nothing else in the codebase reads environment variables or YAML directly.
"""

from __future__ import annotations

import os
import sys
from datetime import date
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
    # PID of the JARVIS window's process (set by the Electron supervisor), so
    # JARVIS can recognise its own window instead of calling it "Electron".
    ui_pid: int | None = None


class StorageSettings(BaseModel):
    database_path: Path = Path("data/jarvis.db")
    log_dir: Path = Path("data/logs")
    # Activity older than this is deleted; the conversation is kept.
    event_retention_days: int = 30


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


class WebSettings(BaseModel):
    default_search: str = "web"
    search_engines: dict[str, str] = Field(
        default_factory=lambda: {"web": "https://www.google.com/search?q={query}"}
    )


class FileSettings(BaseModel):
    roots: list[str] = Field(default_factory=lambda: ["~/Desktop", "~/Documents", "~/Downloads"])
    blocked: list[str] = Field(default_factory=list)
    max_read_chars: int = 30000


class VoiceSettings(BaseModel):
    provider: str = "elevenlabs"
    voice_id: str = "onwK4e9ZLuTAKqWW03F9"
    voice_name: str = "Daniel"
    tts_model: str = "eleven_multilingual_v2"
    stt_model: str = "scribe_v2"
    wake_threshold: float = 0.5
    end_silence_seconds: float = 0.9
    max_request_seconds: float = 15.0
    max_spoken_chars: int = 800
    # The Mac app sets JARVIS_MIC=0 while its bundle lacks the microphone
    # usage text: opening the mic then would get JARVIS killed by macOS.
    microphone_allowed: bool = True
    # Secret from the environment / .env only.
    elevenlabs_api_key: SecretStr | None = None


class ModelPrice(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


class InstrumentSettings(BaseModel):
    symbol: str  # Dukascopy's id (also the cache folder)
    code: str = ""  # Dukascopy's API code; looked up from its instrument list when it differs
    label: str = ""
    price_range: tuple[float, float]
    cost_points: float  # round trip: commission + spread + slippage
    min_stop_points: float
    point_value_usd: float = 1.0


class LearningGates(BaseModel):
    min_trades: dict[str, int] = Field(default_factory=lambda: {"scalping": 150, "daytrading": 60})
    min_oos_trades: dict[str, int] = Field(
        default_factory=lambda: {"scalping": 50, "daytrading": 25}
    )
    in_sample_min_avg_r: float = 0.05
    in_sample_min_profit_factor: float = 1.1
    in_sample_min_t: float = 2.0
    oos_min_profit_factor: float = 1.05
    # Family-wise error rate across all out-of-sample evaluations (Bonferroni).
    oos_alpha: float = 0.05


class LearningSettings(BaseModel):
    model: str = "claude-opus-5-5"
    effort: str | None = "medium"
    max_tokens: int = 8000
    timeout_seconds: float = 180.0
    daily_budget_usd: float = 3.0
    round_interval_minutes: float = 15.0
    stall_limit: int = 20
    tests_per_round: int = 3
    studies_per_round: int = 4
    max_steps_per_round: int = 8
    # What to research; the Learning page can override it (data/preferences.json).
    focus: str = ""
    web_search: bool = True
    searches_per_round: int = 2
    web_search_usd: float = 0.01
    prices: dict[str, ModelPrice] = Field(default_factory=dict)
    data_start: date = date(2021, 1, 1)
    oos_start: date = date(2024, 1, 1)
    holdout_start: date = date(2025, 4, 1)
    instruments: dict[str, InstrumentSettings] = Field(default_factory=dict)
    gates: LearningGates = Field(default_factory=LearningGates)


class BriefingInstrumentSettings(BaseModel):
    round_step: float
    major_step: float
    merge_within: float
    regular_session: bool = False
    decimals: int = 2


class BriefingSettings(BaseModel):
    enabled: bool = True
    time: str = "08:00"  # local time of this computer
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # 0 = Monday
    # Started later than ``time``? The briefing still comes until then.
    catch_up_until: str = "11:00"
    levels_each_side: int = 4
    history_days: int = 21
    instruments: dict[str, BriefingInstrumentSettings] = Field(default_factory=dict)


class TrainingSessionSettings(BaseModel):
    start: str  # New York time; key levels are taken at the start
    end: str


class TrainingSettings(BaseModel):
    enabled: bool = True
    timeframe_minutes: int = 5
    horizon_minutes: int = 60
    # Touch zone, "held" and "broken" distances in ATR(14) of the bars.
    tolerance_atr: float = 0.1
    hold_atr: float = 1.0
    breach_atr: float = 0.5
    history_days: int = 16  # minute data before the session for the key levels
    round_steps_each_side: int = 4  # round numbers above and below the price
    check_minutes: float = 30.0  # how often it looks for a new trading day
    threads: int = 2  # CPU threads for training (keeps the Mac responsive)
    min_holdout_touches: int = 300
    sessions: dict[str, TrainingSessionSettings] = Field(default_factory=dict)


class Mt5LocationSettings(BaseModel):
    """Found automatically on a Mac; set only if JARVIS can't find MetaTrader."""

    app: str = ""
    wine: str = ""
    prefix: str = ""
    data_dir: str = ""  # the MQL5 folder with your EAs
    env: dict[str, str] = Field(default_factory=dict)  # extra environment for Wine


class BotResearchSettings(BaseModel):
    model: str = "claude-opus-5-5"
    effort: str | None = "medium"
    max_tokens: int = 16000
    timeout_seconds: float = 300.0
    daily_budget_usd: float = 3.0
    round_interval_minutes: float = 20.0
    stall_limit: int = 12  # rounds without a new validated version
    max_steps_per_round: int = 12
    max_backtests_per_round: int = 4
    oos_alpha: float = 0.05


class BotGates(BaseModel):
    min_trades_in_sample: int = 60
    min_trades_out_of_sample: int = 20
    min_profit_factor: float = 1.1


class PropFirmSettings(BaseModel):
    account: float = 100000.0
    daily_loss_pct: float = 5.0
    max_loss_pct: float = 10.0


class BotsSettings(BaseModel):
    enabled: bool = True
    symbol: str = "XAUUSD"  # the broker's name for gold
    period: str = "M15"
    model: str = "real_ticks"  # real_ticks | every_tick | ohlc_1m | open_prices
    deposit: float = 10000.0
    currency: str = "USD"
    leverage: int = 100
    data_start: date = date(2021, 1, 1)
    oos_start: date = date(2024, 7, 1)
    holdout_start: date = date(2025, 4, 1)
    test_timeout_minutes: float = 90.0
    target_monthly_usd: float = 10000.0
    own_drawdown_limit_pct: float = 20.0
    mt5: Mt5LocationSettings = Field(default_factory=Mt5LocationSettings)
    research: BotResearchSettings = Field(default_factory=BotResearchSettings)
    gates: BotGates = Field(default_factory=BotGates)
    prop_firm: PropFirmSettings = Field(default_factory=PropFirmSettings)


class UltronAgentModel(BaseModel):
    model: str
    effort: str | None = None
    max_tokens: int = 16000


def _ultron_agents() -> dict[str, UltronAgentModel]:
    strong = UltronAgentModel(model="claude-opus-5-5", effort="high")
    return {
        "jarvis": strong,
        "axiom": strong,
        "forge": UltronAgentModel(model="claude-opus-5-5", effort="medium"),
        "sentinel": UltronAgentModel(model="claude-sonnet-5-5", effort="medium"),
    }


class UltronSettings(BaseModel):
    enabled: bool = True
    budget_usd_per_mission: float = Field(3.0, gt=0, le=500)
    max_parallel_workers: int = Field(3, ge=1, le=6)
    max_attempts: int = Field(3, ge=1, le=5)
    max_rounds_per_run: int = Field(40, ge=4, le=200)
    command_timeout_seconds: float = Field(180.0, gt=0, le=3600)
    model_timeout_seconds: float = 300.0
    export_dir: str = ""
    agents: dict[str, UltronAgentModel] = Field(default_factory=_ultron_agents)
    prices: dict[str, ModelPrice] = Field(default_factory=dict)
    # Tests and the E2E only: a JSON file of scripted agent replies (shown as such in the UI).
    scripted_model: str = ""


class QuantLabSettings(BaseModel):
    # Tests and the E2E only, both labelled in the UI: "fixture" swaps Databento for the
    # offline fixture provider; "memory" swaps the OS keystore for process memory.
    provider: Literal["databento", "fixture"] = "databento"
    keystore: Literal["os", "memory"] = "os"
    # The Strategy Architect (natural language → draft spec); uses the brain's API key.
    architect_model: str = "claude-opus-5-5"
    architect_effort: str | None = "medium"
    architect_max_tokens: int = 8000
    architect_timeout_seconds: float = 120.0
    # Tests and the E2E only: a JSON file of scripted architect replies (labelled in the UI).
    architect_script: str = ""


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
    # After a restart, exchanges from this many hours ago are still context.
    history_restore_hours: float = 24.0
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
    web: WebSettings = Field(default_factory=WebSettings)
    files: FileSettings = Field(default_factory=FileSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    learning: LearningSettings = Field(default_factory=LearningSettings)
    briefing: BriefingSettings = Field(default_factory=BriefingSettings)
    training: TrainingSettings = Field(default_factory=TrainingSettings)
    bots: BotsSettings = Field(default_factory=BotsSettings)
    ultron: UltronSettings = Field(default_factory=UltronSettings)
    quantlab: QuantLabSettings = Field(default_factory=QuantLabSettings)

    @property
    def env_file(self) -> Path:
        """Local secrets and overrides (git-ignored)."""
        return self.root_dir / ".env"

    @property
    def data_dir(self) -> Path:
        return self.database_path.parent

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
    data["web"] = _read_yaml(config_dir / "web.yaml")
    data["files"] = _read_yaml(config_dir / "files.yaml")
    data["voice"] = _read_yaml(config_dir / "voice.yaml")
    data["learning"] = _read_yaml(config_dir / "learning.yaml")
    data["briefing"] = _read_yaml(config_dir / "briefing.yaml")
    data["training"] = _read_yaml(config_dir / "training.yaml")
    data["bots"] = _read_yaml(config_dir / "bots.yaml")
    data["ultron"] = _read_yaml(config_dir / "ultron.yaml")

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
    if value := env.get("JARVIS_UI_PID", "").strip():
        if value.isdigit():
            runtime["ui_pid"] = int(value)
    if value := env.get("JARVIS_SYSTEM_BACKEND"):
        runtime["system_backend"] = value
    if value := env.get("JARVIS_DATA_DIR"):
        storage["database_path"] = str(Path(value) / "jarvis.db")
        storage["log_dir"] = str(Path(value) / "logs")
    if value := env.get("JARVIS_LOG_LEVEL"):
        logging_["level"] = value
    if value := env.get("ANTHROPIC_API_KEY", "").strip():
        data.setdefault("models", {})["anthropic_api_key"] = value
    voice = data.setdefault("voice", {})
    if value := env.get("ELEVENLABS_API_KEY", "").strip():
        voice["elevenlabs_api_key"] = value
    if env.get("JARVIS_MIC", "").strip() == "0":
        voice["microphone_allowed"] = False
    if env.get("JARVIS_BRIEFING", "").strip().lower() == "off":  # tests, E2E
        data.setdefault("briefing", {})["enabled"] = False
    if env.get("JARVIS_TRAINING", "").strip().lower() == "off":  # tests, E2E
        data.setdefault("training", {})["enabled"] = False
    ultron = data.setdefault("ultron", {})
    if value := env.get("JARVIS_ULTRON_SCRIPT", "").strip():  # tests, E2E
        ultron["scripted_model"] = value
    if value := env.get("JARVIS_ULTRON_EXPORT_DIR", "").strip():
        ultron["export_dir"] = value
    quantlab = data.setdefault("quantlab", {})
    if value := env.get("JARVIS_QUANTLAB_PROVIDER", "").strip():  # tests, E2E
        quantlab["provider"] = value
    if value := env.get("JARVIS_QUANTLAB_KEYSTORE", "").strip():  # tests, E2E
        quantlab["keystore"] = value
    if value := env.get("JARVIS_QUANTLAB_ARCHITECT_SCRIPT", "").strip():  # tests, E2E
        quantlab["architect_script"] = value


_QUOTES = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019"}


def read_dotenv(path: Path) -> dict[str, str]:
    """Minimal ``KEY=VALUE`` parser (comments, blank lines, ``export``, quotes).

    Tolerates what editors add: a byte-order mark, CRLF line endings, spaces
    around ``=`` and typographic quotes (TextEdit's smart quotes).
    """
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and _QUOTES.get(value[0]) == value[-1]:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key.strip():
            values[key.strip()] = value
    return values


def write_dotenv_value(path: Path, key: str, value: str) -> None:
    """Set ``key`` in a ``.env`` file, keeping every other line.

    Earlier definitions of ``key`` are replaced in place (the first one) or
    dropped (the rest). The file is written atomically and readable only by
    the owner, because it holds secrets.
    """
    if not value or any(c.isspace() or c in "#\"'" for c in value):
        raise ValueError(f"{key} must be a single token without spaces, quotes or '#'")
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    entry = f"{key}={value}"
    out: list[str] = []
    placed = False
    for line in lines:
        name = line.strip().removeprefix("export ").split("=", 1)[0].strip()
        if "=" in line and name == key and not line.lstrip().startswith("#"):
            if not placed:
                out.append(entry)
                placed = True
            continue
        out.append(line)
    if not placed:
        out.append(entry)
    tmp = path.with_name(f"{path.name}.tmp")
    # Created owner-only from the start: the key is never world-readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)  # in case the file already existed with wider permissions
    os.replace(tmp, path)
