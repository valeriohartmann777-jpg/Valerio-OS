"""Provider-neutral model interface.

The rest of JARVIS never imports a vendor SDK. A provider turns these neutral
types into its own wire format; conversation messages stay provider-native
(opaque ``Any``) so a provider can replay its responses byte-for-byte, which
some models require (e.g. thinking blocks bound to the conversation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# pause_turn: a long server-side tool turn (e.g. web search) wants to be continued.
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "pause_turn", "other"]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.web_searches + other.web_searches,
        )


@dataclass(frozen=True)
class ModelReply:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    assistant_message: Any  # provider-native, appended to the conversation verbatim
    model: str
    usage: Usage = field(default_factory=Usage)
    # Which route served the call: "api" (billed per token), "plan" (Claude subscription via
    # Claude Code), "local" (a model on this computer) or "test" (scripted).
    route: str = "api"
    # What the call would cost at API list prices (informational; only "api" is billed).
    list_cost_usd: float | None = None


ModelErrorCode = Literal[
    "not_configured",
    "invalid_format",
    "save_failed",
    "authentication",
    "permission",
    "billing",
    "model_not_found",
    "bad_request",
    "rate_limited",
    "overloaded",
    "server_error",
    "timeout",
    "connection",
    # AI routing (jarvis.ai): subscription limit, sign-in, capability and pause reasons.
    "plan_limit",
    "not_logged_in",
    "policy",
    "capability",
    "budget",
    "ai_paused",
]


class ModelError(Exception):
    """A failed model call, already translated into a user-facing reason."""

    def __init__(
        self,
        code: ModelErrorCode,
        message: str,
        *,
        suggestion: str | None = None,
        retryable: bool = False,
        detail: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.retryable = retryable
        self.detail = detail
        self.retry_after = retry_after  # seconds, when the provider said so (Retry-After)


class ChatModel(Protocol):
    """One configured model (e.g. the fast brain or the reasoning brain)."""

    @property
    def model_id(self) -> str: ...

    @property
    def label(self) -> str: ...

    async def complete(
        self, *, system: str, messages: list[Any], tools: list[ToolDefinition]
    ) -> ModelReply: ...

    def user_message(self, blocks: list[str]) -> Any: ...

    def assistant_text(self, text: str) -> Any: ...

    def tool_results(self, outcomes: list[ToolOutcome]) -> Any: ...
