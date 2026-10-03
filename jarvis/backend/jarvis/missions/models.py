"""Mission data model."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from jarvis.tools.base import Artifact, ToolError, ToolResult, Verification
from jarvis.util import new_id, utcnow


class MissionStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETE = "complete"
    FAILED = "failed"
    STOPPED = "stopped"


TERMINAL_STATUSES = {MissionStatus.COMPLETE, MissionStatus.FAILED, MissionStatus.STOPPED}


class StepStatus(StrEnum):
    WAITING = "waiting"
    ACTIVE = "active"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETE = "complete"
    FAILED = "failed"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class StepKind(StrEnum):
    ACTION = "action"
    VERIFY = "verify"


class MissionStep(BaseModel):
    id: str = Field(default_factory=new_id)
    index: int
    title: str
    kind: StepKind
    agent: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: int | None = None
    status: StepStatus = StepStatus.WAITING
    summary: str | None = None
    error: ToolError | None = None
    result: ToolResult | None = None
    verification: Verification | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Mission(BaseModel):
    id: str = Field(default_factory=new_id)
    number: int = 0
    title: str
    goal: str
    status: MissionStatus = MissionStatus.PENDING
    priority: Literal["low", "normal", "high"] = "normal"
    current_step: int | None = None
    steps: list[MissionStep] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    result: str | None = None
    errors: list[ToolError] = Field(default_factory=list)
    verification: Verification | None = None
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def touch(self) -> None:
        self.updated_at = utcnow()
