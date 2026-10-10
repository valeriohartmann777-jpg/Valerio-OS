"""Claude via the official Anthropic SDK (async).

- The frozen system prompt and tool list come first so prompt caching applies
  (top-level automatic ``cache_control``).
- Assistant responses are replayed verbatim (``response.content``), which
  keeps thinking blocks valid on models that bind them to the conversation.
- Server-side refusal fallbacks are on by default (``fallbacks: "default"``).
- SDK exceptions become ``ModelError`` with a user-facing reason.
"""

from __future__ import annotations

from typing import Any

import anthropic

from jarvis.llm.base import (
    ModelError,
    ModelReply,
    StopReason,
    ToolCall,
    ToolDefinition,
    ToolOutcome,
    Usage,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_KEY_HINT = "Paste a valid key under Settings → Brain (console.anthropic.com → API keys)."


class AnthropicChatModel:
    def __init__(
        self,
        *,
        client: anthropic.AsyncAnthropic,
        model: str,
        max_tokens: int = 16000,
        effort: str | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        refusal_fallback: bool = True,
    ) -> None:
        self._client = client.with_options(timeout=timeout_seconds, max_retries=max_retries)
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._refusal_fallback = refusal_fallback

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def label(self) -> str:
        return _label(self._model)

    # Conversation building (Anthropic wire format) ----------------------------

    def user_message(self, blocks: list[str]) -> dict[str, Any]:
        return {"role": "user", "content": [{"type": "text", "text": b} for b in blocks]}

    def assistant_text(self, text: str) -> dict[str, Any]:
        return {"role": "assistant", "content": [{"type": "text", "text": text}]}

    def tool_results(self, outcomes: list[ToolOutcome]) -> dict[str, Any]:
        # All results of one assistant turn go back in a single user message.
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": o.call_id,
                    "content": o.content,
                    "is_error": o.is_error,
                }
                for o in outcomes
            ],
        }

    # Request ---------------------------------------------------------------------

    async def complete(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[ToolDefinition],
        server_tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        """``server_tools`` are tools Anthropic runs itself (e.g. web search);
        their results come back inside the reply, never as ``tool_calls``."""
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": messages,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ]
            + list(server_tools or []),
            "cache_control": {"type": "ephemeral"},
        }
        if self._effort:
            params["output_config"] = {"effort": self._effort}
        if self._refusal_fallback:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        try:
            response = await self._client.beta.messages.create(**params)
        except anthropic.APIError as exc:
            raise translate_error(exc, self._model) from exc
        return self._reply(response)

    def _reply(self, response: Any) -> ModelReply:
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        calls = [
            ToolCall(id=block.id, name=block.name, input=dict(block.input or {}))
            for block in response.content
            if block.type == "tool_use"
        ]
        usage = response.usage
        server_use = getattr(usage, "server_tool_use", None)
        searches = getattr(server_use, "web_search_requests", 0) if server_use else 0
        return ModelReply(
            text=text,
            tool_calls=calls,
            stop_reason=_stop_reason(response.stop_reason),
            # Replay everything verbatim: thinking, fallback and tool_use blocks.
            assistant_message={"role": "assistant", "content": response.content},
            model=str(response.model),
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
                web_searches=int(searches or 0),
            ),
        )


async def check_models(client: anthropic.AsyncAnthropic, models: list[str]) -> None:
    """Prove that the client's key works and can use each model.

    Uses the models endpoint: free, no tokens, but it still authenticates the
    key and resolves the model for it. Raises ``ModelError``.
    """
    for model in dict.fromkeys(models):
        try:
            await client.models.retrieve(model)
        except anthropic.APIError as exc:
            raise translate_error(exc, model) from exc


async def verify_key(api_key: str, models: list[str], *, timeout_seconds: float = 15.0) -> None:
    async with anthropic.AsyncAnthropic(
        api_key=api_key, timeout=timeout_seconds, max_retries=1
    ) as client:
        await check_models(client, models)


def translate_error(exc: anthropic.APIError, model: str) -> ModelError:
    """SDK exception → ``ModelError`` with a reason the user can act on."""
    detail = str(exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return ModelError(
            "authentication", "The API key was rejected.", suggestion=_KEY_HINT, detail=detail
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ModelError(
            "permission",
            "This API key isn't allowed to use the model.",
            suggestion="Check the key's workspace and model access in the Claude Console.",
            detail=detail,
        )
    if isinstance(exc, anthropic.NotFoundError):
        return ModelError(
            "model_not_found",
            f"The model {model} isn't available to this API key.",
            suggestion="Choose another model in config/models.yaml.",
            detail=detail,
        )
    if isinstance(exc, anthropic.RateLimitError):
        if "usage limit" in detail.lower() or "spend limit" in detail.lower():
            return ModelError(
                "billing",
                "The API organization reached its spend limit.",
                suggestion="Raise the limit in the Claude Console (Limits) or wait a month.",
                detail=detail,
            )
        return ModelError(
            "rate_limited",
            "I'm being rate-limited by the model provider.",
            suggestion="Wait a moment and try again.",
            retryable=True,
            detail=detail,
            retry_after=_retry_after(exc),
        )
    if isinstance(exc, anthropic.OverloadedError):
        return ModelError(
            "overloaded",
            "The model is temporarily overloaded.",
            suggestion="Try again in a minute.",
            retryable=True,
            detail=detail,
        )
    if isinstance(exc, anthropic.BadRequestError):
        if "usage limits" in detail.lower() or "spend limit" in detail.lower():
            return ModelError(
                "billing",
                "The API organization reached its spend limit.",
                suggestion="Raise the limit in the Claude Console (Limits) or wait a month.",
                detail=detail,
            )
        if "credit balance" in detail.lower():
            return ModelError(
                "billing",
                "The Anthropic account has no credit left.",
                suggestion="Add credits under Billing in the Claude Console.",
                detail=detail,
            )
        return ModelError(
            "bad_request",
            "The model rejected my request.",
            suggestion="Details are in the developer log.",
            detail=detail,
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return ModelError(
            "timeout",
            "The model took too long to answer.",
            suggestion="Try again, or ask a shorter question.",
            retryable=True,
            detail=detail,
        )
    if isinstance(exc, anthropic.APIConnectionError):
        return ModelError(
            "connection",
            "I can't reach the model provider.",
            suggestion="Check the internet connection.",
            retryable=True,
            detail=detail,
        )
    status = exc.status_code if isinstance(exc, anthropic.APIStatusError) else 0
    if status == 402:
        return ModelError(
            "billing",
            "The model provider reports a billing problem.",
            suggestion="Check billing in the Claude Console.",
            detail=detail,
        )
    return ModelError(
        "server_error",
        f"The model provider returned an error ({status or 'unknown'}).",
        suggestion="Try again shortly.",
        retryable=status >= 500,
        detail=detail,
    )


def _retry_after(exc: anthropic.APIStatusError) -> float | None:
    try:
        value = exc.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except (AttributeError, ValueError):
        return None


def _stop_reason(value: str | None) -> StopReason:
    if value in ("end_turn", "tool_use", "max_tokens", "refusal", "pause_turn"):
        return value  # type: ignore[return-value]
    return "other"


def _label(model: str) -> str:
    """claude-sonnet-5-5 → Claude Sonnet 5.5"""
    parts = model.split("-")
    if len(parts) >= 3 and parts[0] == "claude":
        version = ".".join(p for p in parts[2:] if p.isdigit() and len(p) < 4)
        return f"Claude {parts[1].capitalize()} {version}".strip()
    return model
