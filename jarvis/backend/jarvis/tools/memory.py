"""remember / forget: JARVIS's long-term memory, used by the brain.

Both are local and low-risk, so they run without asking — except right after
the request read files or research notes: then a memory could be planted by
that text, and the user approves it first (``stores_instructions``).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from jarvis.core.trace import TraceContext
from jarvis.memory.store import MemoryRefused, MemoryStore
from jarvis.permissions.models import ActionDescriptor, PermissionLevel
from jarvis.tools.base import Tool, ToolError, ToolResult
from jarvis.tools.registry import ToolRegistry

_REF = re.compile(r"^[Mm]?(\d+)$")


def _number(ref: str) -> int | None:
    match = _REF.match(ref.strip())
    return int(match.group(1)) if match else None


class RememberArgs(BaseModel):
    text: str = Field(
        description="One short sentence about the user, in the third person "
        "(e.g. 'Prefers to be addressed informally (du).')",
        max_length=300,
    )
    kind: Literal["preference", "fact", "routine", "correction"] = "fact"
    replaces: str | None = Field(None, description="e.g. M4, when this updates a memory")


class ForgetArgs(BaseModel):
    memory: str = Field(description="e.g. M4")


class RememberTool(Tool[RememberArgs]):
    name = "remember"
    description = (
        "Keep something about the user in long-term memory: a preference, a fact about "
        "them or their work, a routine, or a correction of how you behave. Only what the "
        "user said themselves; never secrets such as passwords or keys."
    )
    permission_level = PermissionLevel.SAFE_ACTION
    input_model = RememberArgs
    stores_instructions = True

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def describe_action(self, args: RememberArgs) -> ActionDescriptor:
        return ActionDescriptor(
            title="Remember",
            target=args.text,
            summary=f"Remember: {args.text}",
            effects=["Used in every later conversation until it is forgotten"],
            level=self.permission_level,
            category="memory",
        )

    async def execute(self, args: RememberArgs, ctx: TraceContext) -> ToolResult:
        replaces = _number(args.replaces) if args.replaces else None
        if args.replaces and replaces is None:
            return ToolResult.failure(
                self.name, ToolError(code="invalid", message=f"{args.replaces} isn't a memory.")
            )
        try:
            memory = await self._store.remember(args.text, args.kind, replaces=replaces)
        except MemoryRefused as exc:
            return ToolResult.failure(self.name, ToolError(code="refused", message=str(exc)))
        summary = f"Remembered as M{memory.number}"
        return ToolResult(
            success=True,
            tool=self.name,
            action="remember",
            target=f"M{memory.number}",
            summary=summary,
            observed_result=summary,
            data={"memory": f"M{memory.number}", "kind": memory.kind, "text": memory.text},
        )


class ForgetTool(Tool[ForgetArgs]):
    name = "forget"
    description = "Remove a memory (e.g. M4) when the user asks you to forget it or it's wrong."
    permission_level = PermissionLevel.SAFE_ACTION
    input_model = ForgetArgs

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def execute(self, args: ForgetArgs, ctx: TraceContext) -> ToolResult:
        number = _number(args.memory)
        if number is None:
            return ToolResult.failure(
                self.name, ToolError(code="invalid", message=f"{args.memory} isn't a memory.")
            )
        try:
            memory = await self._store.forget(number)
        except MemoryRefused as exc:
            return ToolResult.failure(self.name, ToolError(code="not_found", message=str(exc)))
        summary = f"Forgot M{number}: {memory.text}"
        return ToolResult(
            success=True,
            tool=self.name,
            action="forget",
            target=f"M{number}",
            summary=summary,
            observed_result=summary,
        )


def register_memory_tools(registry: ToolRegistry, store: MemoryStore) -> None:
    registry.register(RememberTool(store))
    registry.register(ForgetTool(store))
