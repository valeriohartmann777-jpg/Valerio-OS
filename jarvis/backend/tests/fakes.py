"""A scripted stand-in for a chat model: deterministic brain tests, no network."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from jarvis.llm.base import ModelReply, ToolCall, ToolDefinition, ToolOutcome, Usage

Step = ModelReply | Exception | Callable[[list[Any]], ModelReply]


def say(text: str, model: str = "claude-test") -> ModelReply:
    return ModelReply(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        assistant_message={"role": "assistant", "content": [{"type": "text", "text": text}]},
        model=model,
        usage=Usage(input_tokens=100, output_tokens=10),
    )


def call(name: str, args: dict[str, Any], call_id: str = "toolu_1") -> ModelReply:
    block = {"type": "tool_use", "id": call_id, "name": name, "input": args}
    return ModelReply(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, input=args)],
        stop_reason="tool_use",
        assistant_message={"role": "assistant", "content": [block]},
        model="claude-test",
        usage=Usage(input_tokens=100, output_tokens=20),
    )


class ScriptedChatModel:
    def __init__(self, script: list[Step], model_id: str = "claude-test") -> None:
        self._script = list(script)
        self._model_id = model_id
        self.calls: list[dict[str, Any]] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def label(self) -> str:
        return "Test Model"

    async def complete(
        self, *, system: str, messages: list[Any], tools: list[ToolDefinition]
    ) -> ModelReply:
        self.calls.append({"system": system, "messages": copy.deepcopy(messages), "tools": tools})
        if not self._script:
            raise AssertionError("model called more often than scripted")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(messages)
        return step

    def user_message(self, blocks: list[str]) -> dict[str, Any]:
        return {"role": "user", "content": [{"type": "text", "text": b} for b in blocks]}

    def assistant_text(self, text: str) -> dict[str, Any]:
        return {"role": "assistant", "content": [{"type": "text", "text": text}]}

    def tool_results(self, outcomes: list[ToolOutcome]) -> dict[str, Any]:
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
