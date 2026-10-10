"""Brain tools for ULTRON: start a development mission, report on missions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import ActionDescriptor, PermissionLevel
from jarvis.tools.base import Tool, ToolError, ToolResult
from jarvis.tools.registry import ToolRegistry
from jarvis.ultron.service import UltronError, UltronService


class StartArgs(BaseModel):
    goal: str = Field(
        min_length=8,
        max_length=4000,
        description="The user's development goal in their words, with any constraints.",
    )
    project: Literal["sandbox", "jarvis"] = Field(
        "sandbox",
        description="'sandbox' = a new isolated project; 'jarvis' = change JARVIS itself "
        "(a reviewed branch, never merged automatically).",
    )


class StartMissionTool(Tool[StartArgs]):
    name = "ultron_start_mission"
    description = (
        "Hand a software development goal to the ULTRON team (AXIOM architecture, FORGE code, "
        "SENTINEL independent review) as a mission. It runs in the background in an isolated "
        "git workspace, capped by the mission budget; progress is on the ULTRON page. Use when "
        "the user asks you to build, implement or develop software."
    )
    permission_level = PermissionLevel.SAFE_ACTION
    input_model = StartArgs

    def __init__(self, service: Callable[[], UltronService]) -> None:
        self._service = service

    def describe_action(self, args: StartArgs) -> ActionDescriptor:
        return ActionDescriptor(
            title="Start ULTRON mission",
            target=args.goal[:120],
            summary=f"Start a development mission: {args.goal[:120]}",
            effects=["Works in an isolated workspace", "Spends up to the mission budget"],
            level=self.permission_level,
            category="ultron",
        )

    async def execute(self, args: StartArgs, ctx: TraceContext) -> ToolResult:
        try:
            mission = await self._service().create_mission(args.goal, project=args.project)
        except UltronError as exc:
            error = ToolError(code=exc.code.lower(), message=exc.message)
            return ToolResult.failure(self.name, error)
        summary = (
            f"Mission {mission['id']} started (budget ${mission['budget_usd']:.2f}); "
            "JARVIS is planning it now."
        )
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            target=mission["id"],
            summary=summary,
            observed_result=summary,
            data={"mission_id": mission["id"], "state": mission["state"]},
        )


class NoArgs(BaseModel):
    pass


class StatusTool(Tool[NoArgs]):
    name = "ultron_status"
    description = (
        "ULTRON missions: state, task progress, spend against budget, what blocks them and "
        "which approvals wait for the user. Read-only."
    )
    permission_level = PermissionLevel.READ
    input_model = NoArgs
    returns_untrusted_text = True  # mission titles and agent summaries are model-written

    def __init__(self, service: Callable[[], UltronService]) -> None:
        self._service = service

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        text = await self._service().status_text()
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary="ULTRON status",
            observed_result="ULTRON status read",
            data={"status": text},
        )


def register_ultron_tools(registry: ToolRegistry, service: Callable[[], UltronService]) -> None:
    registry.register(StartMissionTool(service))
    registry.register(StatusTool(service))
