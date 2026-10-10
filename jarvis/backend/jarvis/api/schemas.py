"""Typed request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from jarvis.agents.base import AgentState
from jarvis.core.context import EnvironmentContext
from jarvis.core.state import StateSnapshot
from jarvis.events.types import Event
from jarvis.missions.models import Mission
from jarvis.permissions.models import ApprovalPolicy, PermissionRequest


class Health(BaseModel):
    status: str = "ok"
    version: str
    build: str
    uptime_seconds: float
    pid: int
    system_backend: str


class SystemStatus(BaseModel):
    state: StateSnapshot
    version: str
    uptime_seconds: float
    system_backend: str
    simulated: bool
    clients: int
    pending_approvals: int
    active_missions: int


class BrainView(BaseModel):
    available: bool
    fast_model: str | None
    reasoning_model: str | None
    reason: str | None
    key_hint: str | None = None


class VoiceView(BaseModel):
    state: str
    configured: bool
    wake_word: bool
    wake_word_active: bool
    speak_replies: bool
    voice_name: str
    key_hint: str | None
    reason: str | None


class LearningView(BaseModel):
    state: str
    enabled: bool
    detail: str | None
    progress: float | None
    model: str
    budget_usd: float
    spent_today_usd: float
    stall_rounds: int
    stall_limit: int
    next_round_at: str | None
    round: int | None
    web_search: bool
    focus: str
    counts: dict[str, int]
    data: dict[str, dict[str, Any]]


class MemoryView(BaseModel):
    number: int
    kind: str
    text: str
    created_at: str
    updated_at: str
    source: str


class MemoryCreate(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    kind: Literal["preference", "fact", "routine", "correction"] = "fact"


class BriefingView(BaseModel):
    enabled: bool
    time: str
    weekdays: list[int]
    catch_up_until: str
    next_at: str | None
    last_sent: str | None
    last_error: str | None


class TrainingView(BaseModel):
    state: str
    enabled: bool
    detail: str | None
    progress: float | None
    next_check_at: str | None
    model: dict[str, Any] | None
    report: dict[str, Any] | None
    report_run: int | None
    history: list[dict[str, Any]]


class BotLabView(BaseModel):
    state: str
    detail: str | None
    ready: bool
    user_terminal_running: bool
    checks: list[dict[str, Any]]
    experts: list[dict[str, Any]]
    improving: str | None
    model: str
    budget_usd: float
    spent_today_usd: float
    next_round_at: str | None
    running: list[dict[str, Any]]


class BotSettingsUpdate(BaseModel):
    symbol: str | None = Field(None, max_length=30)
    period: str | None = Field(None, max_length=4)
    model: str | None = Field(None, max_length=20)
    deposit: float | None = Field(None, gt=0, le=100_000_000)
    leverage: int | None = Field(None, gt=0, le=5000)


class BotBacktestRequest(BaseModel):
    version: int = Field(0, ge=0)
    inputs: dict[str, str] = Field(default_factory=dict)


class QuantLabSpecRequest(BaseModel):
    spec: dict[str, Any]


class QuantLabDatasetImport(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1)
    symbol: str = Field(min_length=1, max_length=40)
    exchange: str = Field(min_length=1, max_length=40)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    asset_class: str = "cash_equity"
    timezone: str | None = Field(None, max_length=64)
    frequency: Literal["1m", "5m", "1h", "1d"] | None = None
    provider: str = Field("user_supplied", max_length=80)
    license: str = Field("unverified (user's responsibility)", max_length=120)
    adjustment: Literal["adjusted", "unadjusted", "unknown"] = "unknown"
    columns: dict[str, str] = Field(default_factory=dict)


class QuantLabFixtureRequest(BaseModel):
    name: str


class QuantLabExperimentRequest(BaseModel):
    strategy_version_id: str
    dataset_id: str


class UltronMissionCreate(BaseModel):
    goal: str = Field(min_length=8, max_length=4000)
    project: Literal["sandbox", "jarvis"] = "sandbox"
    budget_usd: float | None = Field(None, gt=0, le=500)


class UltronAnswer(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class UltronBudget(BaseModel):
    usd: float = Field(gt=0, le=500)


class UltronConfigUpdate(BaseModel):
    budget_usd_per_mission: float | None = None
    max_parallel_workers: int | None = None
    max_attempts: int | None = None


class TrainingPreferences(BaseModel):
    enabled: bool


class BriefingPreferences(BaseModel):
    enabled: bool | None = None
    time: str | None = Field(None, max_length=5)


class LearningFocus(BaseModel):
    text: str = Field(max_length=4000)


class VoicePreferences(BaseModel):
    wake_word: bool | None = None
    speak_replies: bool | None = None


class ApiKeyRequest(BaseModel):
    api_key: str = Field(max_length=500)


class Snapshot(BaseModel):
    version: str
    build: str
    brain: BrainView
    voice: VoiceView
    learning: LearningView
    training: TrainingView
    bots: BotLabView
    memories: list[MemoryView]
    state: StateSnapshot
    system_backend: str
    simulated: bool
    context: EnvironmentContext | None
    agents: list[AgentState]
    missions: list[Mission]
    approvals: list[PermissionRequest]
    activity: list[Event]


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ChatAccepted(BaseModel):
    accepted: bool = True
    trace_id: str


class MissionCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)


class ApproveRequest(BaseModel):
    strong_confirmation: bool = False


class RejectRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class LevelPolicy(BaseModel):
    level: int
    label: str
    policy: ApprovalPolicy


class SettingsView(BaseModel):
    """Read-only, secret-free view of the active configuration."""

    version: str
    system_backend: str
    simulated: bool
    personality_name: str
    personality_traits: list[str]
    permission_levels: list[LevelPolicy]
    tool_overrides: dict[str, ApprovalPolicy]
    disabled_categories: list[str]
    approval_timeout_seconds: float
    models: dict[str, str]
    brain: BrainView
    known_apps: list[str]
    file_roots: list[str]
    config_dir: str
    database_path: str
