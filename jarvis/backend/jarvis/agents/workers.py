"""Operator acts; Sentinel verifies. Neither touches the OS directly."""

from __future__ import annotations

import logging
from typing import Any

from jarvis.agents.base import AgentRegistry
from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.tools.base import ToolError, ToolResult, Verification
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry

log = logging.getLogger("jarvis.agents")


class OperatorAgent:
    id = "operator"

    def __init__(self, agents: AgentRegistry, executor: ToolExecutor) -> None:
        self._agents = agents
        self._executor = executor

    async def perform(
        self, *, tool: str, args: dict[str, Any], task: str, reason: str, ctx: TraceContext
    ) -> ToolResult:
        ctx = ctx.for_agent(self.id)
        if tool not in self._agents.spec(self.id).available_tools:
            return ToolResult.failure(
                tool,
                ToolError(code="not_permitted", message=f"Operator is not allowed to use {tool}."),
            )
        await self._agents.start(self.id, task, ctx)
        result = await self._executor.run(tool, args, ctx=ctx, reason=reason, source=self.id)
        summary = result.observed_result or result.summary
        if result.success:
            await self._agents.complete(self.id, summary, ctx)
        else:
            await self._agents.fail(self.id, summary, ctx)
        return result


class SentinelAgent:
    id = "sentinel"

    def __init__(self, agents: AgentRegistry, tools: ToolRegistry, bus: EventBus) -> None:
        self._agents = agents
        self._tools = tools
        self._bus = bus

    async def verify(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        result: ToolResult,
        task: str,
        ctx: TraceContext,
    ) -> Verification:
        ctx = ctx.for_agent(self.id)
        await self._agents.start(self.id, task, ctx)
        verification = await self._verify(tool, args, result, ctx)
        if verification.verified:
            message = f"Execution verified — {verification.summary}"
            severity = Severity.IMPORTANT
        elif verification.status == "unverifiable":
            message = f"Could not verify independently — {verification.summary}"
            severity = Severity.WARNING
        else:
            message = f"Verification failed — {verification.summary}"
            severity = Severity.ERROR
        await self._bus.emit(
            EventType.VERIFICATION_COMPLETED,
            message=message,
            source=self.id,
            severity=severity,
            ctx=ctx,
            payload={"tool": tool, "verification": verification.model_dump(mode="json")},
        )
        if verification.status == "failed":
            await self._agents.fail(self.id, verification.summary, ctx)
        else:
            await self._agents.complete(self.id, verification.summary, ctx)
        return verification

    async def _verify(
        self, tool_name: str, args: dict[str, Any], result: ToolResult, ctx: TraceContext
    ) -> Verification:
        if not result.success:
            return Verification(status="failed", method="result", summary=result.summary)
        tool = self._tools.get(tool_name)
        if tool is None:
            return Verification(status="unverifiable", method="none", summary="Unknown tool.")
        try:
            verification = await tool.verify(tool.input_model.model_validate(args), result, ctx)
        except Exception:
            log.exception("verifier crashed", extra={"tool": tool_name, **ctx.log_fields()})
            return Verification(
                status="unverifiable", method="error", summary="The verifier failed to run."
            )
        return verification or Verification(
            status="unverifiable", method="none", summary="This action has no independent verifier."
        )
