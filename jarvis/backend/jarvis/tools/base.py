"""Generic tool interface and structured results.

Every tool follows observe → act → observe again. ``execute`` returns what was
observed, never just "the call was issued". ``verify`` performs an independent
re-observation used by Sentinel.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import ActionDescriptor, PermissionLevel


class ToolError(BaseModel):
    """User-facing error. ``detail`` is for developers and never shown in the main UI."""

    code: str
    message: str
    suggestion: str | None = None
    detail: str | None = None


class Artifact(BaseModel):
    kind: str
    label: str
    uri: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    success: bool
    tool: str
    action: str
    target: str | None = None
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)
    observed_result: str | None = None
    error: ToolError | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    simulated: bool = False
    duration_ms: int = 0

    @classmethod
    def failure(
        cls,
        tool: str,
        error: ToolError,
        *,
        action: str | None = None,
        target: str | None = None,
        observations: list[str] | None = None,
        simulated: bool = False,
    ) -> ToolResult:
        return cls(
            success=False,
            tool=tool,
            action=action or tool,
            target=target,
            summary=error.message,
            error=error,
            observations=observations or [],
            simulated=simulated,
        )


VerificationStatus = Literal["verified", "failed", "unverifiable"]


class Verification(BaseModel):
    status: VerificationStatus
    method: str
    summary: str
    evidence: list[str] = Field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.status == "verified"


class ToolSpec(BaseModel):
    name: str
    description: str
    permission_level: PermissionLevel
    side_effects: bool
    input_schema: dict[str, Any]


class Tool[Args: BaseModel](ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    permission_level: ClassVar[PermissionLevel]
    input_model: ClassVar[type[BaseModel]]
    side_effects: ClassVar[bool] = False
    # Returns private data (file contents) — marks the request as exposed.
    reads_private_data: ClassVar[bool] = False
    # Returns text that others wrote (notes from web research) — marks the request
    # as exposed, like private data: what follows may have been planted.
    returns_untrusted_text: ClassVar[bool] = False
    # Can carry data off the computer (a URL) — needs approval once exposed.
    sends_data_out: ClassVar[bool] = False
    # Stores something that shapes future requests (a memory) — needs approval
    # once exposed, so read content can't plant lasting instructions.
    stores_instructions: ClassVar[bool] = False

    def describe_action(self, args: Args) -> ActionDescriptor:
        """Describe exactly what will happen. Override for target-dependent risk."""
        return ActionDescriptor(
            title=self.name.replace("_", " ").capitalize(),
            summary=self.description,
            level=self.permission_level,
        )

    def step_titles(self, args: Args) -> tuple[str, str]:
        """Titles for the act and verify steps when this runs inside a mission."""
        action = self.describe_action(args)
        title = action.summary.rstrip(".") or action.title
        return title, f"Verify: {title}"

    async def precheck(self, args: Args, ctx: TraceContext) -> ToolError | None:
        """Fail fast — before any approval is requested — if the action is impossible
        or forbidden (unknown target, denylisted, invalid input)."""
        return None

    @abstractmethod
    async def execute(self, args: Args, ctx: TraceContext) -> ToolResult: ...

    async def verify(
        self, args: Args, result: ToolResult, ctx: TraceContext
    ) -> Verification | None:
        """Independently re-observe the environment. ``None`` = no verifier."""
        return None

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            permission_level=self.permission_level,
            side_effects=self.side_effects,
            input_schema=self.input_model.model_json_schema(),
        )
