"""Typed request/response models for the HTTP API."""

from __future__ import annotations

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
    uptime_seconds: float


class SystemStatus(BaseModel):
    state: StateSnapshot
    version: str
    uptime_seconds: float
    system_backend: str
    simulated: bool
    clients: int
    pending_approvals: int
    active_missions: int


class Snapshot(BaseModel):
    version: str
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
    known_apps: list[str]
    config_dir: str
    database_path: str
