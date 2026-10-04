"""Typed request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any

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
