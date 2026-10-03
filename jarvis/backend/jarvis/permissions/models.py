"""Permission levels, policies and approval requests."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum

from pydantic import BaseModel, Field

from jarvis.util import new_id, utcnow


class PermissionLevel(IntEnum):
    READ = 0
    SAFE_ACTION = 1
    MODIFICATION = 2
    EXTERNAL_EFFECT = 3
    HIGH_RISK = 4


LEVEL_LABELS: dict[PermissionLevel, str] = {
    PermissionLevel.READ: "Read",
    PermissionLevel.SAFE_ACTION: "Safe action",
    PermissionLevel.MODIFICATION: "Modification",
    PermissionLevel.EXTERNAL_EFFECT: "External effect",
    PermissionLevel.HIGH_RISK: "High risk",
}


class ApprovalPolicy(StrEnum):
    AUTO = "auto"
    CONFIRM = "confirm"
    STRONG_CONFIRM = "strong_confirm"
    DENY = "deny"


class ActionDescriptor(BaseModel):
    """Exactly what an action will do — shown verbatim on approval cards."""

    title: str
    target: str | None = None
    summary: str
    effects: list[str] = Field(default_factory=list)
    level: PermissionLevel
    category: str | None = None
    reversible: bool = True


class RequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PermissionRequest(BaseModel):
    id: str = Field(default_factory=new_id)
    tool: str
    action: ActionDescriptor
    reason: str
    policy: ApprovalPolicy
    status: RequestStatus = RequestStatus.PENDING
    agent: str | None = None
    trace_id: str | None = None
    mission_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None


class Decision(BaseModel):
    approved: bool
    policy: ApprovalPolicy
    request_id: str | None = None
    note: str = ""

    @property
    def approval_label(self) -> str:
        """Short label for the audit log."""
        if self.policy is ApprovalPolicy.AUTO:
            return "auto"
        if self.policy is ApprovalPolicy.DENY:
            return "denied_by_policy"
        return "approved" if self.approved else "rejected"
