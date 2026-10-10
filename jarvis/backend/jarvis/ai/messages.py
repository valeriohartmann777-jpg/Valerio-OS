"""One conversation format for every route.

All providers build and read conversations in the Claude API wire format (role + content
blocks), so a run can move between the Claude plan and the Claude API at a call boundary
without translating its history, and a checkpoint can be stored as JSON and replayed.
"""

from __future__ import annotations

import json
from typing import Any

from jarvis.llm.base import ToolOutcome


def user_message(blocks: list[str]) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": b} for b in blocks]}


def assistant_text(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def tool_results(outcomes: list[ToolOutcome]) -> dict[str, Any]:
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


def jsonable(value: Any) -> Any:
    """SDK objects (pydantic) → plain JSON values, losslessly enough to replay them."""
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump(mode="json", exclude_none=True))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items() if v is not None}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def blocks_of(message: Any) -> tuple[str, list[dict[str, Any]]]:
    data = jsonable(message)
    role = str(data.get("role", "user"))
    content = data.get("content", [])
    if isinstance(content, str):
        return role, [{"type": "text", "text": content}]
    return role, [b for b in content if isinstance(b, dict)]


def has_images(messages: list[Any]) -> bool:
    return any(b.get("type") in ("image", "document") for m in messages for b in blocks_of(m)[1])


def _guard(text: str) -> str:
    # The transcript uses XML-like turn markers; text from tools or sources can't close them.
    for tag in ("user", "assistant", "conversation", "tool_call", "tool_result"):
        text = text.replace(f"</{tag}>", f"<\\/{tag}>")
    return text


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
    return json.dumps(content, ensure_ascii=False, default=str)


def render_transcript(messages: list[Any]) -> str:
    """The conversation as tagged text for a route that takes a single prompt."""
    lines = ["<conversation>"]
    for message in messages:
        role, blocks = blocks_of(message)
        lines.append(f"<{role}>")
        for block in blocks:
            kind = block.get("type")
            if kind == "text":
                lines.append(_guard(str(block.get("text", ""))))
            elif kind == "tool_use":
                payload = json.dumps(block.get("input", {}), ensure_ascii=False)
                lines.append(
                    f'<tool_call id="{block.get("id")}" name="{block.get("name")}">'
                    f"{_guard(payload)}</tool_call>"
                )
            elif kind == "tool_result":
                error = "true" if block.get("is_error") else "false"
                lines.append(
                    f'<tool_result id="{block.get("tool_use_id")}" is_error="{error}">'
                    f"{_guard(_result_text(block.get('content')))}</tool_result>"
                )
            elif kind in ("server_tool_use", "web_search_tool_result", "web_fetch_tool_result"):
                lines.append("[a web search ran here; its results are not available]")
            # thinking / redacted_thinking / fallback blocks carry nothing for another model
        lines.append(f"</{role}>")
    lines.append("</conversation>")
    return "\n".join(lines)
