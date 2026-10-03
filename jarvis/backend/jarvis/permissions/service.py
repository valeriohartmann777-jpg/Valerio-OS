"""Permission gate: decides, asks, and waits for the user's decision."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.permissions.models import (
    LEVEL_LABELS,
    ActionDescriptor,
    ApprovalPolicy,
    Decision,
    PermissionRequest,
    RequestStatus,
)
from jarvis.settings import PermissionSettings
from jarvis.util import utcnow

log = logging.getLogger("jarvis.permissions")


class ApprovalError(Exception):
    """Raised for invalid approval operations (unknown id, missing confirmation)."""


class StrongConfirmationRequired(ApprovalError):
    pass


class PermissionService:
    def __init__(self, settings: PermissionSettings, bus: EventBus) -> None:
        self._settings = settings
        self._bus = bus
        self._pending: dict[str, tuple[PermissionRequest, asyncio.Future[bool]]] = {}

    @property
    def settings(self) -> PermissionSettings:
        return self._settings

    def policy_for(self, tool: str, action: ActionDescriptor) -> ApprovalPolicy:
        if action.category and action.category in self._settings.disabled_categories:
            return ApprovalPolicy.DENY
        if tool in self._settings.tool_overrides:
            return self._settings.tool_overrides[tool]
        return self._settings.levels.get(action.level, ApprovalPolicy.STRONG_CONFIRM)

    def pending(self) -> list[PermissionRequest]:
        return [request for request, _ in self._pending.values()]

    async def authorize(
        self, tool: str, action: ActionDescriptor, *, reason: str, ctx: TraceContext
    ) -> Decision:
        policy = self.policy_for(tool, action)
        if policy is ApprovalPolicy.AUTO:
            return Decision(approved=True, policy=policy)
        if policy is ApprovalPolicy.DENY:
            note = (
                f"{action.category.capitalize()} actions are disabled."
                if action.category
                else "This action is disabled by policy."
            )
            return Decision(approved=False, policy=policy, note=note)
        return await self._ask(tool, action, policy, reason=reason, ctx=ctx)

    async def _ask(
        self,
        tool: str,
        action: ActionDescriptor,
        policy: ApprovalPolicy,
        *,
        reason: str,
        ctx: TraceContext,
    ) -> Decision:
        timeout = self._settings.approval_timeout_seconds
        request = PermissionRequest(
            tool=tool,
            action=action,
            reason=reason,
            policy=policy,
            agent=ctx.agent,
            trace_id=ctx.trace_id,
            mission_id=ctx.mission_id,
            expires_at=utcnow() + timedelta(seconds=timeout),
        )
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request.id] = (request, future)
        target = f" — {action.target}" if action.target else ""
        await self._bus.emit(
            EventType.PERMISSION_REQUESTED,
            message=f"Approval required: {action.title}{target} ({LEVEL_LABELS[action.level]})",
            source="sentinel",
            severity=Severity.WARNING,
            ctx=ctx,
            payload={"request": request.model_dump(mode="json")},
        )
        try:
            approved = await asyncio.wait_for(asyncio.shield(future), timeout)
        except TimeoutError:
            approved = False
            await self._finish(request, RequestStatus.EXPIRED, "No response before timeout.")
        finally:
            self._pending.pop(request.id, None)
        return Decision(
            approved=approved,
            policy=policy,
            request_id=request.id,
            note=request.decision_note or "",
        )

    async def approve(
        self, request_id: str, *, strong_confirmation: bool = False
    ) -> PermissionRequest:
        request, future = self._lookup(request_id)
        if request.policy is ApprovalPolicy.STRONG_CONFIRM and not strong_confirmation:
            raise StrongConfirmationRequired(
                "This is a high-risk action and needs explicit strong confirmation."
            )
        await self._finish(request, RequestStatus.APPROVED, "Approved by user.")
        if not future.done():
            future.set_result(True)
        return request

    async def reject(
        self, request_id: str, *, note: str = "Rejected by user."
    ) -> PermissionRequest:
        request, future = self._lookup(request_id)
        await self._finish(request, RequestStatus.REJECTED, note)
        if not future.done():
            future.set_result(False)
        return request

    async def reject_for_mission(self, mission_id: str, note: str) -> None:
        for request in self.pending():
            if request.mission_id == mission_id:
                await self.reject(request.id, note=note)

    def _lookup(self, request_id: str) -> tuple[PermissionRequest, asyncio.Future[bool]]:
        entry = self._pending.get(request_id)
        if entry is None or entry[0].status is not RequestStatus.PENDING:
            raise ApprovalError(f"No pending approval with id {request_id}")
        return entry

    async def _finish(self, request: PermissionRequest, status: RequestStatus, note: str) -> None:
        request.status = status
        request.decided_at = utcnow()
        request.decision_note = note
        event_type, severity, verb = {
            RequestStatus.APPROVED: (EventType.PERMISSION_APPROVED, Severity.IMPORTANT, "approved"),
            RequestStatus.REJECTED: (EventType.PERMISSION_REJECTED, Severity.IMPORTANT, "rejected"),
            RequestStatus.EXPIRED: (EventType.PERMISSION_EXPIRED, Severity.WARNING, "expired"),
        }[status]
        log.info(
            "permission %s",
            verb,
            extra={
                "trace_id": request.trace_id,
                "mission_id": request.mission_id,
                "tool": request.tool,
            },
        )
        await self._bus.emit(
            event_type,
            message=f"{request.action.title} {verb}",
            source="sentinel",
            severity=severity,
            ctx=TraceContext(trace_id=request.trace_id) if request.trace_id else None,
            mission_id=request.mission_id,
            payload={"request": request.model_dump(mode="json")},
        )
