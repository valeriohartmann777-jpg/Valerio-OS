"""Optional local model (Ollama's native chat API on this computer).

Off until the owner sets it up (Settings → AI & Billing). It only ever takes plain
conversation (capability ``chat``) — planning, coding, research and reviews are never
handed to it — and every answer it gives is labelled "Local AI".
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx2

from jarvis.ai import messages as msg
from jarvis.llm.base import ModelError, ModelReply, ToolCall, ToolDefinition, ToolOutcome, Usage

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def local_url(base_url: str) -> str:
    """Only loopback addresses: the local route never sends anything off this computer."""
    url = httpx2.URL(base_url)
    if url.scheme not in ("http", "https") or url.host not in LOCAL_HOSTS:
        raise ValueError("The local model must run on this computer (127.0.0.1 / localhost).")
    return str(url).rstrip("/")


async def health(
    base_url: str, model: str, transport: httpx2.AsyncBaseTransport | None = None
) -> tuple[bool, str]:
    """Ready when the server answers and the configured model is installed."""
    try:
        async with httpx2.AsyncClient(
            base_url=local_url(base_url), timeout=5.0, transport=transport
        ) as client:
            response = await client.get("/api/tags")
            response.raise_for_status()
            names = {str(m.get("name", "")) for m in response.json().get("models", [])}
    except (httpx2.HTTPError, ValueError, json.JSONDecodeError) as exc:
        return False, f"No local model server at {base_url} ({type(exc).__name__})."
    if not model:
        return False, "No local model chosen."
    if model not in names and f"{model}:latest" not in names:
        return False, f"The model {model} isn't installed in Ollama."
    return True, f"{model} on {base_url}"


def _to_ollama(system: str, messages: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    names: dict[str, str] = {}
    for message in messages:
        role, blocks = msg.blocks_of(message)
        texts = [str(b.get("text", "")) for b in blocks if b.get("type") == "text"]
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        results = [b for b in blocks if b.get("type") == "tool_result"]
        for call in calls:
            names[str(call.get("id"))] = str(call.get("name"))
        if role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts)}
            if calls:
                entry["tool_calls"] = [
                    {"function": {"name": c.get("name"), "arguments": c.get("input", {})}}
                    for c in calls
                ]
            out.append(entry)
            continue
        for result in results:
            content = result.get("content")
            out.append(
                {
                    "role": "tool",
                    "tool_name": names.get(str(result.get("tool_use_id")), ""),
                    "content": content if isinstance(content, str) else json.dumps(content),
                }
            )
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    return out


class LocalChatModel:
    route = "local"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 120.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = local_url(base_url)
        self._model = model
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def label(self) -> str:
        return f"{self._model} · Local AI"

    def user_message(self, blocks: list[str]) -> Any:
        return msg.user_message(blocks)

    def assistant_text(self, text: str) -> Any:
        return msg.assistant_text(text)

    def tool_results(self, outcomes: list[ToolOutcome]) -> Any:
        return msg.tool_results(outcomes)

    async def complete(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[ToolDefinition],
        server_tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        if server_tools or msg.has_images(messages):
            raise ModelError(
                "capability",
                "The local model can't search the web or read images.",
                detail="server tools / images are not supported by the local route",
            )
        body: dict[str, Any] = {
            "model": self._model,
            "messages": _to_ollama(system, messages),
            "stream": False,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
        try:
            async with httpx2.AsyncClient(
                base_url=self._base, timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post("/api/chat", json=body)
                response.raise_for_status()
                data = response.json()
        except httpx2.TimeoutException:
            raise ModelError("timeout", "The local model took too long.", retryable=True) from None
        except (httpx2.HTTPError, json.JSONDecodeError) as exc:
            raise ModelError(
                "connection",
                "The local model isn't reachable.",
                suggestion="Start Ollama, or turn the local model off in Settings → AI & Billing.",
                retryable=True,
                detail=type(exc).__name__,
            ) from None
        message = data.get("message") or {}
        text = str(message.get("content") or "").strip()
        calls = [
            ToolCall(
                id="toolu_local_" + uuid.uuid4().hex[:20],
                name=str((c.get("function") or {}).get("name", "")),
                input=dict((c.get("function") or {}).get("arguments") or {}),
            )
            for c in message.get("tool_calls") or []
        ]
        content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        content += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls
        ]
        if not content:
            content.append({"type": "text", "text": "(no answer)"})
        return ModelReply(
            text=text,
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            assistant_message={"role": "assistant", "content": content},
            model=self._model,
            usage=Usage(
                input_tokens=int(data.get("prompt_eval_count") or 0),
                output_tokens=int(data.get("eval_count") or 0),
            ),
            route="local",
            list_cost_usd=0.0,
        )
