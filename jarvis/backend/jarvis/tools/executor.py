"""Single entry point for running tools.

validate input → describe action → permission gate → tool.started → execute
(with timeout) → tool.completed | tool.failed → audit log.

No exception escapes as a stack trace: failures become structured
``ToolError``s with a user-facing message; details go to the JSON log.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import ValidationError

from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.permissions.models import PermissionLevel
from jarvis.permissions.service import PermissionService
from jarvis.storage.audit import AuditLog
from jarvis.tools.base import ToolError, ToolResult
from jarvis.tools.registry import ToolRegistry

log = logging.getLogger("jarvis.tools")


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permissions: PermissionService,
        bus: EventBus,
        audit: AuditLog,
        *,
        timeout_seconds: float,
    ) -> None:
        self._registry = registry
        self._permissions = permissions
        self._bus = bus
        self._audit = audit
        self._timeout = timeout_seconds

    async def run(
        self,
        tool_name: str,
        raw_args: dict[str, Any],
        *,
        ctx: TraceContext,
        reason: str,
        source: str = "jarvis",
    ) -> ToolResult:
        tool = self._registry.get(tool_name)
        if tool is None:
            return ToolResult.failure(
                tool_name,
                ToolError(code="unknown_tool", message=f"I don't have a tool called {tool_name}."),
            )
        try:
            args = tool.input_model.model_validate(raw_args)
        except ValidationError as exc:
            return ToolResult.failure(
                tool_name,
                ToolError(
                    code="invalid_arguments",
                    message="The request was missing information I need.",
                    detail=str(exc),
                ),
            )

        if error := await tool.precheck(args, ctx):
            result = ToolResult.failure(tool_name, error)
            await self._finish(
                tool_name, raw_args, tool.permission_level, "not_requested", result, ctx, source
            )
            return result

        action = tool.describe_action(args)
        decision = await self._permissions.authorize(tool_name, action, reason=reason, ctx=ctx)
        if not decision.approved:
            result = ToolResult.failure(
                tool_name,
                ToolError(
                    code="permission_denied" if decision.request_id else "disabled_by_policy",
                    message=decision.note or "The action was not approved.",
                ),
                target=action.target,
            )
            await self._finish(
                tool_name, raw_args, action.level, decision.approval_label, result, ctx, source
            )
            return result

        await self._bus.emit(
            EventType.TOOL_STARTED,
            message=action.summary.rstrip(".") or action.title,
            source=source,
            ctx=ctx,
            payload={"tool": tool_name, "target": action.target, "level": int(action.level)},
        )
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(tool.execute(args, ctx), self._timeout)
        except TimeoutError:
            result = ToolResult.failure(
                tool_name,
                ToolError(
                    code="timeout",
                    message=f"The action took longer than {self._timeout:.0f} seconds.",
                    suggestion="Try again, or check whether the application is responding.",
                ),
                target=action.target,
            )
        except Exception as exc:
            log.exception("tool crashed", extra={"tool": tool_name, **ctx.log_fields()})
            result = ToolResult.failure(
                tool_name,
                ToolError(
                    code="internal_error",
                    message="Something went wrong inside the tool.",
                    suggestion="Details are in the developer log.",
                    detail=f"{type(exc).__name__}: {exc}",
                ),
                target=action.target,
            )
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        await self._finish(
            tool_name, raw_args, action.level, decision.approval_label, result, ctx, source
        )
        return result

    async def _finish(
        self,
        tool_name: str,
        raw_args: dict[str, Any],
        level: PermissionLevel,
        approval: str,
        result: ToolResult,
        ctx: TraceContext,
        source: str,
    ) -> None:
        await self._bus.emit(
            EventType.TOOL_COMPLETED if result.success else EventType.TOOL_FAILED,
            message=result.observed_result or result.summary,
            source=source,
            severity=Severity.INFO if result.success else Severity.ERROR,
            ctx=ctx,
            payload={"tool": tool_name, "result": result.model_dump(mode="json")},
        )
        log.info(
            "tool %s",
            "completed" if result.success else "failed",
            extra={
                "tool": tool_name,
                "success": result.success,
                "duration_ms": result.duration_ms,
                "error": result.error.model_dump() if result.error else None,
                **ctx.log_fields(),
            },
        )
        await self._audit.record(
            tool=tool_name,
            arguments=raw_args,
            permission_level=int(level),
            approval=approval,
            success=result.success,
            summary=result.observed_result or result.summary,
            trace_id=ctx.trace_id,
            mission_id=ctx.mission_id,
            agent=ctx.agent,
            simulated=result.simulated,
        )
