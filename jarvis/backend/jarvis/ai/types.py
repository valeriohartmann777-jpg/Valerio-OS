"""Shared vocabulary of the AI router: providers, capabilities, failure kinds, task scope."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from jarvis.llm.base import ModelError

Provider = Literal["plan", "api", "local"]
PROVIDERS: tuple[Provider, ...] = ("plan", "api", "local")
LABELS: dict[str, str] = {"plan": "Claude plan", "api": "Claude API", "local": "Local AI"}

# What a task needs. The local model may only take plain conversation: planning, coding,
# research and reviews are never silently degraded to a weaker model.
Capability = Literal["chat", "planning", "coding", "research", "review"]
LOCAL_CAPABILITIES: frozenset[str] = frozenset({"chat"})


class Failure(StrEnum):
    """Why a model call failed, as far as routing is concerned."""

    OVERLOADED = "overloaded"  # 529 / 5xx: temporary capacity problem
    RATE_LIMIT = "rate_limit"  # 429: too many requests; Retry-After applies
    NETWORK = "network"  # no connection, DNS, timeout on this side
    PLAN_LIMIT = "plan_limit"  # the Claude plan's session/weekly limit is reached
    AUTH = "auth"  # not signed in / sign-in expired (plan)
    KEY_INVALID = "key_invalid"  # the API key is rejected
    CREDIT = "credit"  # API credit balance too low / org spend limit reached
    POLICY = "policy"  # access disabled by the organization, account on hold, not permitted
    CAPABILITY = "capability"  # this provider can't do what the task needs
    BAD_REQUEST = "bad_request"  # our request is wrong — not a routing problem
    UNKNOWN = "unknown"


# Retried on the same provider with backoff; never a reason to switch to a paid route.
TEMPORARY: frozenset[Failure] = frozenset({Failure.OVERLOADED, Failure.RATE_LIMIT, Failure.NETWORK})
# Clear, provider-specific reasons to fail over to the next allowed provider.
FAILOVER: frozenset[Failure] = frozenset(
    {Failure.PLAN_LIMIT, Failure.AUTH, Failure.KEY_INVALID, Failure.CREDIT, Failure.POLICY}
)


@dataclass
class ProviderStatus:
    provider: Provider
    state: str
    detail: str = ""
    checked_at: str | None = None
    until: str | None = None  # a limit's reset time or a cooldown's end (ISO, UTC)
    label: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "state": self.state,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "until": self.until,
            "label": self.label or LABELS[self.provider],
            **({"extra": self.extra} if self.extra else {}),
        }


@dataclass(frozen=True)
class TaskScope:
    """Who is asking: used for per-mission caps and the usage ledger."""

    consumer: str = ""
    mission_id: str | None = None
    task_id: str | None = None
    agent: str | None = None


_SCOPE: ContextVar[TaskScope | None] = ContextVar("ai_scope", default=None)


def current_scope() -> TaskScope | None:
    return _SCOPE.get()


@contextlib.contextmanager
def ai_scope(
    consumer: str,
    *,
    mission_id: str | None = None,
    task_id: str | None = None,
    agent: str | None = None,
) -> Iterator[TaskScope]:
    scope = TaskScope(consumer, mission_id, task_id, agent)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


class AIPaused(ModelError):
    """No allowed route can serve the call right now. Work pauses and resumes by itself
    when a route becomes available (or when the owner changes the AI settings)."""

    def __init__(
        self, message: str, *, reasons: list[str] | None = None, temporary: bool = False
    ) -> None:
        super().__init__(
            "ai_paused",
            message,
            suggestion="Open Settings → AI & Billing to connect your Claude plan or approve "
            "the paid API fallback. Paused work continues automatically.",
            retryable=False,
        )
        self.reasons = reasons or []
        self.temporary = temporary
