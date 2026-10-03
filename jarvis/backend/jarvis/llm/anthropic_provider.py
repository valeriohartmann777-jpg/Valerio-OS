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
_KEY_HINT = "Check ANTHROPIC_API_KEY in jarvis/.env and restart JARVIS."


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
        self, *, system: str, messages: list[Any], tools: list[ToolDefinition]
    ) -> ModelReply:
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": messages,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
            "cache_control": {"type": "ephemeral"},
        }
        if self._effort:
            params["output_config"] = {"effort": self._effort}
        if self._refusal_fallback:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        try:
            response = await self._client.beta.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise ModelError(
                "authentication", "My API key was rejected.", suggestion=_KEY_HINT, detail=str(exc)
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ModelError(
                "permission",
                "This API key isn't allowed to use the model.",
                suggestion="Check the key's workspace and model access in the Claude Console.",
                detail=str(exc),
            ) from exc
        except anthropic.NotFoundError as exc:
            raise ModelError(
                "model_not_found",
                f"The model {self._model} isn't available to this API key.",
                suggestion="Choose another model in config/models.yaml.",
                detail=str(exc),
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ModelError(
                "rate_limited",
                "I'm being rate-limited by the model provider.",
                suggestion="Wait a moment and try again.",
                retryable=True,
                detail=str(exc),
            ) from exc
        except anthropic.OverloadedError as exc:
            raise ModelError(
                "overloaded",
                "The model is temporarily overloaded.",
                suggestion="Try again in a minute.",
                retryable=True,
                detail=str(exc),
            ) from exc
        except anthropic.BadRequestError as exc:
            raise ModelError(
                "bad_request",
                "The model rejected my request.",
                suggestion="Details are in the developer log.",
                detail=str(exc),
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise ModelError(
                "timeout",
                "The model took too long to answer.",
                suggestion="Try again, or ask a shorter question.",
                retryable=True,
                detail=str(exc),
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError(
                "connection",
                "I can't reach the model provider.",
                suggestion="Check the internet connection.",
                retryable=True,
                detail=str(exc),
            ) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 402:
                raise ModelError(
                    "billing",
                    "The model provider reports a billing problem.",
                    suggestion="Check billing in the Claude Console.",
                    detail=str(exc),
                ) from exc
            raise ModelError(
                "server_error",
                f"The model provider returned an error ({exc.status_code}).",
                suggestion="Try again shortly.",
                retryable=exc.status_code >= 500,
                detail=str(exc),
            ) from exc
        return self._reply(response)

    def _reply(self, response: Any) -> ModelReply:
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        calls = [
            ToolCall(id=block.id, name=block.name, input=dict(block.input or {}))
            for block in response.content
            if block.type == "tool_use"
        ]
        usage = response.usage
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
            ),
        )


def _stop_reason(value: str | None) -> StopReason:
    if value in ("end_turn", "tool_use", "max_tokens", "refusal"):
        return value  # type: ignore[return-value]
    return "other"


def _label(model: str) -> str:
    """claude-sonnet-5-5 → Claude Sonnet 5.5"""
    parts = model.split("-")
    if len(parts) >= 3 and parts[0] == "claude":
        version = ".".join(p for p in parts[2:] if p.isdigit() and len(p) < 4)
        return f"Claude {parts[1].capitalize()} {version}".strip()
    return model
