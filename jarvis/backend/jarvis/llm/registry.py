"""Builds the configured models (fast brain, reasoning brain) from settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jarvis.llm.base import ChatModel
from jarvis.settings import ModelRoleSettings, ModelSettings

Mode = Literal["fast", "think"]


@dataclass(frozen=True)
class ModelSet:
    fast: ChatModel | None
    reasoning: ChatModel | None
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.fast is not None or self.reasoning is not None

    def for_mode(self, mode: Mode) -> ChatModel | None:
        if mode == "think":
            return self.reasoning or self.fast
        return self.fast or self.reasoning


NO_KEY = "No API key is configured. Add ANTHROPIC_API_KEY=… to jarvis/.env and restart JARVIS."


def build_models(settings: ModelSettings) -> ModelSet:
    roles = {"fast": settings.fast, "reasoning": settings.reasoning}
    providers = {role.provider for role in roles.values() if role.provider != "none"}
    if not providers:
        return ModelSet(None, None, "No model provider is configured in config/models.yaml.")
    unsupported = providers - {"anthropic"}
    if unsupported:
        return ModelSet(
            None, None, f"Unsupported model provider: {', '.join(sorted(unsupported))}."
        )
    if settings.anthropic_api_key is None:
        return ModelSet(None, None, NO_KEY)

    import anthropic

    from jarvis.llm.anthropic_provider import AnthropicChatModel

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())

    def build(role: ModelRoleSettings) -> ChatModel | None:
        if role.provider != "anthropic" or not role.model:
            return None
        return AnthropicChatModel(
            client=client,
            model=role.model,
            max_tokens=role.max_tokens,
            effort=role.effort,
            timeout_seconds=role.timeout_seconds,
            max_retries=role.retries,
            refusal_fallback=settings.refusal_fallback,
        )

    return ModelSet(fast=build(settings.fast), reasoning=build(settings.reasoning))
