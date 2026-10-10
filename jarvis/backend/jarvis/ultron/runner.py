"""One agent run: the model ↔ tool loop, bounded by rounds, budget and the pause gate."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.ai import messages as msg
from jarvis.ai.checkpoint import RunCheckpoint
from jarvis.llm.base import ChatModel, ToolCall, ToolDefinition, ToolOutcome, Usage
from jarvis.settings import ModelPrice
from jarvis.ultron.tools import FINAL_TOOLS, Broker


class BudgetExceeded(Exception):
    def __init__(self, needed: float, remaining: float) -> None:
        super().__init__(f"needs up to ${needed:.2f}, ${remaining:.2f} left")
        self.needed = needed
        self.remaining = remaining


@dataclass
class Budget:
    """Checks a call's worst case against what the mission has left, before the call."""

    price: ModelPrice | None  # None: free (scripted test model)
    max_tokens: int
    remaining: Callable[[], Awaitable[float]]

    def cost(self, usage: Usage) -> float:
        if self.price is None:
            return 0.0
        p = self.price
        return (
            usage.input_tokens * p.input
            + usage.output_tokens * p.output
            + usage.cache_read_tokens * p.cache_read
            + usage.cache_write_tokens * p.cache_write
        ) / 1_000_000

    async def check(self, system: str, messages: list[Any]) -> None:
        if self.price is None:
            return
        tokens_in = (len(system) + len(json.dumps(messages, default=str))) / 3.0
        worst = (
            tokens_in * max(self.price.input, self.price.cache_write)
            + self.max_tokens * self.price.output
        ) / 1_000_000
        left = await self.remaining()
        if worst > left:
            raise BudgetExceeded(worst, left)


@dataclass
class AgentResult:
    final_tool: str | None = None
    final: dict[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    cost: float = 0.0
    tool_calls: int = 0
    rounds: int = 0
    error: str | None = None
    text: str = ""


Validator = Callable[[str, dict[str, Any]], str | None]
UsageSink = Callable[[Usage, float], Awaitable[None]]


def _will_pay(model: ChatModel, messages: list[Any]) -> bool:
    """Routed models know whether the next call goes to the paid API; others always pay."""
    probe = getattr(model, "will_pay", None)
    return True if probe is None else bool(probe(messages))


def _pending(messages: list[Any]) -> list[ToolCall]:
    """Tool calls of a saved last assistant turn whose results were never appended."""
    if not messages:
        return []
    role, blocks = msg.blocks_of(messages[-1])
    if role != "assistant":
        return []
    return [
        ToolCall(id=str(b.get("id")), name=str(b.get("name")), input=dict(b.get("input") or {}))
        for b in blocks
        if b.get("type") == "tool_use"
    ]


async def run_agent(
    *,
    model: ChatModel,
    system: str,
    opening: str,
    tools: list[ToolDefinition],
    broker: Broker | None,
    validate: Validator,
    max_rounds: int,
    gate: Callable[[], Awaitable[object]],
    budget: Budget,
    on_usage: UsageSink,
    final_tools: frozenset[str] | None = None,
    checkpoint: RunCheckpoint | None = None,
) -> AgentResult:
    """With a ``checkpoint`` the run survives pauses and restarts: the conversation is saved
    after every reply and every round of results, and tool calls that already ran are
    answered from the tool-action ledger instead of running again."""
    result = AgentResult()
    names = {t.name for t in tools}
    finals = FINAL_TOOLS if final_tools is None else final_tools
    messages: list[Any] = [model.user_message([opening])]
    saved = await checkpoint.load() if checkpoint is not None else None
    if saved is not None:
        messages, result.rounds = saved
    nudges = 0

    async def save() -> None:
        if checkpoint is not None:
            await checkpoint.save(messages, result.rounds)

    async def handle(calls: list[ToolCall]) -> tuple[str, dict[str, Any]] | None:
        outcomes: list[ToolOutcome] = []
        done: tuple[str, dict[str, Any]] | None = None
        for call in calls:
            result.tool_calls += 1
            if call.name in finals:
                if call.name not in names:
                    outcomes.append(ToolOutcome(call.id, f"{call.name} isn't your tool.", True))
                    continue
                problem = validate(call.name, call.input)
                if problem:
                    outcomes.append(ToolOutcome(call.id, f"Not accepted: {problem}", True))
                else:
                    done = (call.name, call.input)
                    outcomes.append(ToolOutcome(call.id, "Accepted.", False))
            elif broker is None:
                outcomes.append(ToolOutcome(call.id, f"{call.name} isn't available here.", True))
            else:
                recorded = await checkpoint.recorded(call.id) if checkpoint is not None else None
                if recorded is not None:
                    content, is_error = recorded  # ran before the pause: never twice
                else:
                    content, is_error = await broker.execute(call)
                    if checkpoint is not None:
                        await checkpoint.record(call.id, call.name, call.input, content, is_error)
                outcomes.append(ToolOutcome(call.id, content, is_error))
        messages.append(model.tool_results(outcomes))
        await save()
        return done

    if saved is not None and (pending := _pending(messages)):
        done = await handle(pending)
        if done is not None:
            result.final_tool, result.final = done
            return result
    while result.rounds < max_rounds:
        await gate()  # a paused mission waits here, between steps
        if _will_pay(model, messages):
            await budget.check(system, messages)
        reply = await model.complete(system=system, messages=messages, tools=tools)
        result.rounds += 1
        # Only the Claude API is billed per token; plan, local and test routes cost nothing.
        cost = budget.cost(reply.usage) if reply.route == "api" else 0.0
        result.usage = result.usage + reply.usage
        result.cost += cost
        await on_usage(reply.usage, cost)
        messages.append(reply.assistant_message)
        await save()
        if reply.text:
            result.text = reply.text
        if not reply.tool_calls:
            nudges += 1
            if nudges > 2:
                result.error = "stopped without submitting a result"
                return result
            hint = (
                "Your reply was cut off; continue in smaller steps."
                if reply.stop_reason == "max_tokens"
                else "Continue with your tools; finish by calling your submit tool."
            )
            messages.append(model.user_message([hint]))
            await save()
            continue
        done = await handle(reply.tool_calls)
        if done is not None:
            result.final_tool, result.final = done
            return result
    result.error = f"no result within {max_rounds} steps"
    return result
