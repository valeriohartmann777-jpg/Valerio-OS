"""Event model shared by every component.

The TypeScript mirror lives in ``packages/protocol/src/events.ts``;
``tests/test_protocol_sync.py`` keeps the two in sync.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from jarvis.util import new_id, utcnow


class EventType(StrEnum):
    SYSTEM_ONLINE = "system.online"
    JARVIS_STATE_CHANGED = "jarvis.state.changed"
    JARVIS_MESSAGE = "jarvis.message"
    JARVIS_REASONING = "jarvis.reasoning"
    COMMAND_RECEIVED = "command.received"
    INTENT_CLASSIFIED = "intent.classified"
    MISSION_CREATED = "mission.created"
    MISSION_UPDATED = "mission.updated"
    AGENT_STARTED = "agent.started"
    AGENT_UPDATED = "agent.updated"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    VERIFICATION_COMPLETED = "verification.completed"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_APPROVED = "permission.approved"
    PERMISSION_REJECTED = "permission.rejected"
    PERMISSION_EXPIRED = "permission.expired"
    MODEL_COMPLETED = "model.completed"
    BRAIN_CHANGED = "brain.changed"
    VOICE_CHANGED = "voice.changed"
    LEARNING_CHANGED = "learning.changed"
    CONTEXT_UPDATED = "context.updated"


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    IMPORTANT = "important"
    WARNING = "warning"
    ERROR = "error"


SEVERITY_RANK: dict[Severity, int] = {
    Severity.DEBUG: 0,
    Severity.INFO: 1,
    Severity.IMPORTANT: 2,
    Severity.WARNING: 3,
    Severity.ERROR: 4,
}


class Event(BaseModel):
    id: str = Field(default_factory=new_id)
    type: EventType
    timestamp: datetime = Field(default_factory=utcnow)
    severity: Severity = Severity.INFO
    source: str = "jarvis"
    message: str = ""
    trace_id: str | None = None
    mission_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def at_least(self, severity: Severity) -> bool:
        return SEVERITY_RANK[self.severity] >= SEVERITY_RANK[severity]
