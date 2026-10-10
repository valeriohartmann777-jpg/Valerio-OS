"""A scripted stand-in for Claude — for tests and the E2E only.

Active only when ``JARVIS_ULTRON_SCRIPT`` points at a JSON file of replies per
agent; the UI then labels every mission "scripted test model". Everything
around the model — files, git, sandboxed test runs, policy, approvals — is the
real runtime.

File format: ``{"jarvis": [step, …], "axiom": […], "forge": […], "sentinel": […]}``;
a step is ``{"tool": name, "input": {…}}``, ``{"tools": [{…}, …]}`` or ``{"text": "…"}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jarvis.llm.base import ModelReply, ToolCall, ToolDefinition, ToolOutcome, Usage

LABEL = "Scripted test model"


class ScriptedAgentModel:
    def __init__(self, agent: str, steps: list[dict[str, Any]]) -> None:
        self._agent = agent
        self._steps = steps
        self._count = 0

    @property
    def model_id(self) -> str:
        return "scripted-test"

    @property
    def label(self) -> str:
        return LABEL

    async def complete(
        self, *, system: str, messages: list[Any], tools: list[ToolDefinition]
    ) -> ModelReply:
        if not self._steps:
            text = f"(script for {self._agent} is exhausted)"
            return ModelReply(text, [], "end_turn", self.assistant_text(text), self.model_id)
        step = self._steps.pop(0)
        self._count += 1
        calls = step.get("tools") or ([step] if "tool" in step else [])
        tool_calls = [
            ToolCall(
                id=f"{self._agent}-{self._count}-{i}", name=c["tool"], input=c.get("input", {})
            )
            for i, c in enumerate(calls)
        ]
        blocks: list[dict[str, Any]] = []
        if step.get("text"):
            blocks.append({"type": "text", "text": step["text"]})
        blocks += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in tool_calls
        ]
        return ModelReply(
            text=step.get("text", ""),
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            assistant_message={"role": "assistant", "content": blocks},
            model=self.model_id,
            usage=Usage(),
            route="test",
        )

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


def load(path: Path) -> dict[str, ScriptedAgentModel]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {agent: ScriptedAgentModel(agent, list(steps)) for agent, steps in data.items()}
