"""Agent specifications and the registry that tracks their live status.

Agents are internal workers. The user only ever talks to JARVIS; the agent
matrix in the dashboard exists for transparency.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.permissions.models import PermissionLevel
from jarvis.util import utcnow


class AgentStatus(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    WAITING = "waiting"
    STANDBY = "standby"  # declared, not yet available in this phase


class AgentSpec(BaseModel):
    id: str
    name: str
    role: str
    description: str
    instructions: str
    available_tools: list[str] = Field(default_factory=list)
    max_permission_level: PermissionLevel = PermissionLevel.READ
    available: bool = True
    available_in_phase: int | None = None


class AgentState(BaseModel):
    id: str
    name: str
    role: str
    description: str
    available: bool
    available_in_phase: int | None
    tools: list[str]
    max_permission_level: PermissionLevel
    status: AgentStatus
    current_task: str | None = None
    detail: str | None = None
    mission_id: str | None = None
    last_outcome: Literal["success", "failure"] | None = None
    last_summary: str | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class AgentUnavailable(Exception):
    pass


class AgentRegistry:
    def __init__(self, bus: EventBus, specs: list[AgentSpec]) -> None:
        self._bus = bus
        self._specs = {spec.id: spec for spec in specs}
        self._states = {
            spec.id: AgentState(
                id=spec.id,
                name=spec.name,
                role=spec.role,
                description=spec.description,
                available=spec.available,
                available_in_phase=spec.available_in_phase,
                tools=spec.available_tools,
                max_permission_level=spec.max_permission_level,
                status=AgentStatus.IDLE if spec.available else AgentStatus.STANDBY,
            )
            for spec in specs
        }

    def spec(self, agent_id: str) -> AgentSpec:
        return self._specs[agent_id]

    def state(self, agent_id: str) -> AgentState:
        return self._states[agent_id]

    def list(self) -> list[AgentState]:
        return list(self._states.values())

    async def start(self, agent_id: str, task: str, ctx: TraceContext) -> None:
        if not self._specs[agent_id].available:
            raise AgentUnavailable(f"{self._specs[agent_id].name} is not available yet")
        state = self._update(
            agent_id,
            status=AgentStatus.ACTIVE,
            current_task=task,
            detail=None,
            mission_id=ctx.mission_id,
        )
        await self._emit(EventType.AGENT_STARTED, state, f"{state.name} assigned — {task}", ctx)

    async def wait(self, agent_id: str, detail: str, ctx: TraceContext) -> None:
        state = self._update(agent_id, status=AgentStatus.WAITING, detail=detail)
        await self._emit(
            EventType.AGENT_UPDATED, state, f"{state.name} waiting — {detail}", ctx, Severity.DEBUG
        )

    async def resume(self, agent_id: str, ctx: TraceContext) -> None:
        state = self._update(agent_id, status=AgentStatus.ACTIVE, detail=None)
        await self._emit(
            EventType.AGENT_UPDATED, state, f"{state.name} resumed", ctx, Severity.DEBUG
        )

    async def complete(self, agent_id: str, summary: str, ctx: TraceContext) -> None:
        state = self._finish(agent_id, "success", summary)
        await self._emit(
            EventType.AGENT_COMPLETED,
            state,
            f"{state.name} finished — {summary}",
            ctx,
            Severity.DEBUG,
        )

    async def fail(self, agent_id: str, summary: str, ctx: TraceContext) -> None:
        state = self._finish(agent_id, "failure", summary)
        # The tool/verification event already carries the error; keep the stream quiet.
        await self._emit(
            EventType.AGENT_FAILED, state, f"{state.name} failed — {summary}", ctx, Severity.DEBUG
        )

    def _finish(
        self, agent_id: str, outcome: Literal["success", "failure"], summary: str
    ) -> AgentState:
        return self._update(
            agent_id,
            status=AgentStatus.IDLE,
            current_task=None,
            detail=None,
            mission_id=None,
            last_outcome=outcome,
            last_summary=summary,
        )

    def _update(self, agent_id: str, **changes: object) -> AgentState:
        state = self._states[agent_id].model_copy(update={**changes, "updated_at": utcnow()})
        self._states[agent_id] = state
        return state

    async def _emit(
        self,
        event_type: EventType,
        state: AgentState,
        message: str,
        ctx: TraceContext,
        severity: Severity = Severity.INFO,
    ) -> None:
        await self._bus.emit(
            event_type,
            message=message,
            source=state.id,
            severity=severity,
            ctx=ctx,
            payload={"agent": state.model_dump(mode="json")},
        )
