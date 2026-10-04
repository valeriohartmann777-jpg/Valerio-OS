"""The real Anthropic SDK against a mock HTTP transport (no network, no key).

Checks what JARVIS actually puts on the wire and how responses and HTTP
errors are translated.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import SecretStr

from jarvis.llm.anthropic_provider import FALLBACK_BETA, AnthropicChatModel, check_models
from jarvis.llm.base import ModelError, ToolDefinition, ToolOutcome
from jarvis.llm.registry import NO_KEY, build_models
from jarvis.settings import ModelSettings, load_settings

TOOLS = [
    ToolDefinition(
        name="open_application",
        description="Open a desktop application.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}, "purpose": {"type": "string"}},
            "required": ["name"],
        },
    )
]


def message(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_input_tokens": 96,
            "cache_creation_input_tokens": 0,
        },
    }


def error(status: int, kind: str) -> httpx2.Response:
    return httpx2.Response(
        status, json={"type": "error", "error": {"type": kind, "message": f"{kind} (test)"}}
    )


class Server:
    """Records requests and answers with queued responses."""

    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, index: int = -1) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[index].content)
        return data


def model_with(
    server: Callable[[httpx2.Request], httpx2.Response], **kwargs: Any
) -> AnthropicChatModel:
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(server)),
    )
    return AnthropicChatModel(
        client=client, model="claude-sonnet-5-5", effort="low", max_retries=0, **kwargs
    )


async def test_request_shape_and_tool_round_trip() -> None:
    server = Server(
        httpx2.Response(
            200,
            json=message(
                [
                    {"type": "thinking", "thinking": "", "signature": "sig-abc"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "open_application",
                        "input": {"name": "safari", "purpose": "Opening Safari"},
                    },
                ],
                stop_reason="tool_use",
            ),
        ),
        httpx2.Response(200, json=message([{"type": "text", "text": "Safari is open."}])),
    )
    model = model_with(server)
    messages: list[Any] = [model.user_message(["<context>…</context>", "open my browser"])]

    reply = await model.complete(system="You are JARVIS.", messages=messages, tools=TOOLS)
    assert reply.stop_reason == "tool_use"
    assert reply.tool_calls[0].name == "open_application"
    assert reply.tool_calls[0].input == {"name": "safari", "purpose": "Opening Safari"}
    assert reply.usage.cache_read_tokens == 96

    request = server.requests[0]
    assert request.url.path == "/v1/messages"
    assert FALLBACK_BETA in request.headers["anthropic-beta"]
    assert request.headers["x-api-key"] == "sk-ant-test"
    body = server.body(0)
    assert body["model"] == "claude-sonnet-5-5"
    assert body["max_tokens"] == 16000
    assert body["system"] == "You are JARVIS."
    assert body["output_config"] == {"effort": "low"}
    assert body["fallbacks"] == "default"
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["input_schema"]["properties"]["purpose"] == {"type": "string"}
    assert "thinking" not in body  # adaptive by default; never "disabled" on these models

    messages.append(reply.assistant_message)
    messages.append(model.tool_results([ToolOutcome("toolu_1", '{"success": true}')]))
    final = await model.complete(system="You are JARVIS.", messages=messages, tools=TOOLS)
    assert final.text == "Safari is open." and final.stop_reason == "end_turn"

    sent = server.body(1)["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    replayed = sent[1]["content"]
    assert replayed[0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}  # verbatim
    assert replayed[1]["id"] == "toolu_1"
    assert sent[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": '{"success": true}',
        "is_error": False,
    }


async def test_refusal_fallback_can_be_switched_off() -> None:
    server = Server(httpx2.Response(200, json=message([{"type": "text", "text": "hi"}])))
    model = model_with(server, refusal_fallback=False)
    await model.complete(system="s", messages=[model.user_message(["hi"])], tools=TOOLS)
    assert "fallbacks" not in server.body()
    assert "anthropic-beta" not in server.requests[0].headers


async def test_refusal_and_truncation_stop_reasons() -> None:
    server = Server(
        httpx2.Response(200, json=message([], stop_reason="refusal")),
        httpx2.Response(200, json=message([{"type": "text", "text": "partial"}], "max_tokens")),
    )
    model = model_with(server)
    first = await model.complete(system="s", messages=[model.user_message(["x"])], tools=TOOLS)
    second = await model.complete(system="s", messages=[model.user_message(["x"])], tools=TOOLS)
    assert first.stop_reason == "refusal"
    assert (second.stop_reason, second.text) == ("max_tokens", "partial")


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (error(401, "authentication_error"), "authentication", False),
        (error(402, "billing_error"), "billing", False),
        (error(403, "permission_error"), "permission", False),
        (error(404, "not_found_error"), "model_not_found", False),
        (error(400, "invalid_request_error"), "bad_request", False),
        (error(429, "rate_limit_error"), "rate_limited", True),
        (error(529, "overloaded_error"), "overloaded", True),
        (error(500, "api_error"), "server_error", True),
        (httpx2.ConnectError("no route to host"), "connection", True),
    ],
)
async def test_http_errors_become_model_errors(
    response: httpx2.Response | Exception, code: str, retryable: bool
) -> None:
    model = model_with(Server(response))
    with pytest.raises(ModelError) as caught:
        await model.complete(system="s", messages=[model.user_message(["x"])], tools=TOOLS)
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert caught.value.message and "test" not in caught.value.message  # user-facing text


def test_label() -> None:
    model = model_with(Server())
    assert model.label == "Claude Sonnet 5.5"


def test_build_models_needs_a_key() -> None:
    settings = load_settings(environ={})
    unavailable = build_models(settings.models)
    assert not unavailable.available and unavailable.unavailable_reason == NO_KEY

    keyed = settings.models.model_copy(update={"anthropic_api_key": SecretStr("sk-ant-test")})
    models = build_models(keyed)
    assert models.available
    assert models.for_mode("fast") is models.fast
    assert models.for_mode("think") is models.reasoning
    assert models.fast is not None and models.fast.model_id == "claude-sonnet-5-5"
    assert models.reasoning is not None and models.reasoning.model_id == "claude-opus-5-5"

    other = ModelSettings.model_validate(
        {"fast": {"provider": "acme", "model": "x"}, "anthropic_api_key": "k"}
    )
    assert "Unsupported" in (build_models(other).unavailable_reason or "")


def client_for(server: Server) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key="sk-ant-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(server)),
    )


def model_info(model: str) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "type": "model",
            "id": model,
            "display_name": model,
            "created_at": "2026-01-01T00:00:00Z",
        },
    )


async def test_key_check_uses_the_free_models_endpoint() -> None:
    server = Server(model_info("claude-sonnet-5-5"), model_info("claude-opus-5-5"))
    await check_models(
        client_for(server), ["claude-sonnet-5-5", "claude-opus-5-5", "claude-sonnet-5-5"]
    )
    assert [r.url.path for r in server.requests] == [
        "/v1/models/claude-sonnet-5-5",
        "/v1/models/claude-opus-5-5",
    ]
    assert all(r.method == "GET" for r in server.requests)


async def test_key_check_reports_rejected_keys_and_missing_models() -> None:
    with pytest.raises(ModelError) as info:
        await check_models(client_for(Server(error(401, "authentication_error"))), ["m"])
    assert info.value.code == "authentication"
    assert "Settings" in (info.value.suggestion or "")

    server = Server(model_info("claude-sonnet-5-5"), error(404, "not_found_error"))
    with pytest.raises(ModelError) as info:
        await check_models(client_for(server), ["claude-sonnet-5-5", "claude-opus-9"])
    assert info.value.code == "model_not_found"
    assert "claude-opus-9" in info.value.message


async def test_empty_credit_balance_is_reported_as_billing() -> None:
    server = Server(
        httpx2.Response(
            400,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API.",
                },
            },
        )
    )
    with pytest.raises(ModelError) as info:
        await model_with(server).complete(system="s", messages=[], tools=TOOLS)
    assert info.value.code == "billing"
    assert "credit" in info.value.message


async def test_server_tools_are_sent_and_searches_counted() -> None:
    reply = message([{"type": "text", "text": "Read two sources."}])
    reply["usage"]["server_tool_use"] = {"web_search_requests": 2}
    server = Server(httpx2.Response(200, json=reply), httpx2.Response(200, json=message([])))
    model = model_with(server)
    search = {"type": "web_search_20250305", "name": "web_search", "max_uses": 2}
    first = await model.complete(
        system="s", messages=[model.user_message(["hi"])], tools=TOOLS, server_tools=[search]
    )
    assert server.body()["tools"][-1] == search
    assert server.body()["tools"][0]["name"] == "open_application"
    assert first.usage.web_searches == 2
    second = await model.complete(system="s", messages=[model.user_message(["hi"])], tools=TOOLS)
    assert [t["name"] for t in server.body()["tools"]] == ["open_application"]
    assert second.usage.web_searches == 0
