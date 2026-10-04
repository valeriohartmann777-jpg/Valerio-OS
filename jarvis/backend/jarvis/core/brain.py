"""The model-driven path: understand → plan → act → observe → respond.

Claude decides which tools to use, but never touches the system directly:

- read-only tools run through the Operator (events, audit log),
- side-effecting tools run inside an open-ended mission — an act step
  (Operator, permission gate, approvals) plus a verify step (Sentinel),
- every result goes back to the model as untrusted data, including what
  Sentinel verified.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from jarvis.agents.workers import OperatorAgent
from jarvis.core.context import EnvironmentContextService
from jarvis.core.conversation import Conversation
from jarvis.core.responses import Reply
from jarvis.core.state import JarvisState, StateService
from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.llm.base import ChatModel, ModelError, ToolCall, ToolDefinition, ToolOutcome, Usage
from jarvis.llm.registry import Mode, ModelSet
from jarvis.memory.store import MemoryStore
from jarvis.missions.engine import MissionEngine, MissionHandle
from jarvis.missions.models import Mission
from jarvis.tools.base import ToolError, ToolResult, Verification
from jarvis.tools.registry import ToolRegistry

log = logging.getLogger("jarvis.brain")

_PURPOSE = {
    "type": "string",
    "description": "One short sentence, shown to the user, saying what you are doing and why.",
}
_MAX_RESULT_CHARS = 60_000  # fits read_file's 30k characters plus JSON escaping


@dataclass
class _Turn:
    text: str
    ctx: TraceContext
    handle: MissionHandle | None = None
    stopped: bool = False
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class BrainStatus:
    available: bool
    fast_model: str | None
    reasoning_model: str | None
    reason: str | None
    key_hint: str | None = None


class Brain:
    def __init__(
        self,
        *,
        models: ModelSet,
        tools: ToolRegistry,
        operator: OperatorAgent,
        missions: MissionEngine,
        bus: EventBus,
        state: StateService,
        context: EnvironmentContextService,
        system_prompt: str,
        history_turns: int,
        max_tool_rounds: int,
        memory: MemoryStore | None = None,
    ) -> None:
        self._models = models
        self._tools = tools
        self._operator = operator
        self._missions = missions
        self._bus = bus
        self._state = state
        self._context = context
        self._system = system_prompt
        self._conversation = Conversation(history_turns)
        self._max_rounds = max_tool_rounds
        self._memory = memory
        self._tool_defs = [self._definition(name) for name in tools.names()]

    @property
    def available(self) -> bool:
        return self._models.available

    @property
    def status(self) -> BrainStatus:
        return BrainStatus(
            available=self._models.available,
            fast_model=self._models.fast.model_id if self._models.fast else None,
            reasoning_model=self._models.reasoning.model_id if self._models.reasoning else None,
            reason=self._models.unavailable_reason,
            key_hint=self._models.key_hint,
        )

    def set_models(self, models: ModelSet) -> None:
        """Swap the models at runtime (key connected or rejected). Memory is kept:
        earlier turns are replayed as plain text, valid for any model."""
        self._models = models

    def remember(self, user_text: str, reply: str) -> None:
        """Record an exchange handled without the model (rule path) as context."""
        self._conversation.add([user_text], reply)

    async def respond(self, text: str, ctx: TraceContext, *, mode: Mode = "fast") -> Reply:
        model = self._models.for_mode(mode)
        if model is None:
            return Reply(
                text="I can't reason about that yet.",
                success=False,
                error=ToolError(
                    code="model_unavailable",
                    message=self._models.unavailable_reason or "No model is configured.",
                    suggestion="Open Settings → Brain and paste your Anthropic API key.",
                ),
            )
        user_blocks = [self._context_block(), text]
        messages = [*self._conversation.messages_for(model), model.user_message(user_blocks)]
        turn = _Turn(text=text, ctx=ctx)
        await self._bus.emit(
            EventType.JARVIS_REASONING,
            message=f"Reasoning with {model.label}" + (" (think mode)" if mode == "think" else ""),
            ctx=ctx,
            payload={"model": model.model_id, "mode": mode},
        )
        reply = Reply(text="Something went wrong on my side.", success=False)
        try:
            reply = await self._loop(model, messages, turn)
        except ModelError as exc:
            log.warning("model call failed: %s", exc.detail or exc.message, extra=ctx.log_fields())
            reply = Reply(
                text="I couldn't complete that."
                if turn.handle
                else "I couldn't think that through.",
                success=False,
                error=ToolError(code=exc.code, message=exc.message, suggestion=exc.suggestion),
            )
        finally:
            if turn.handle is not None:
                await turn.handle.finish(reply)
        self._conversation.add(user_blocks, reply.text)
        log.info(
            "turn finished",
            extra={
                "model": model.model_id,
                "input_tokens": turn.usage.input_tokens,
                "output_tokens": turn.usage.output_tokens,
                "cache_read_tokens": turn.usage.cache_read_tokens,
                **ctx.log_fields(),
            },
        )
        return reply

    # Loop -------------------------------------------------------------------------

    async def _loop(self, model: ChatModel, messages: list[Any], turn: _Turn) -> Reply:
        for _ in range(self._max_rounds):
            await self._state.set(JarvisState.THINKING, detail=model.label, ctx=turn.ctx)
            reply = await model.complete(
                system=self._system, messages=messages, tools=self._tool_defs
            )
            turn.usage = turn.usage + reply.usage
            await self._bus.emit(
                EventType.MODEL_COMPLETED,
                message=f"{model.label}: {reply.stop_reason}",
                severity=Severity.DEBUG,
                ctx=turn.ctx,
                payload={
                    "model": reply.model,
                    "stop_reason": reply.stop_reason,
                    "tool_calls": [c.name for c in reply.tool_calls],
                    "input_tokens": reply.usage.input_tokens,
                    "output_tokens": reply.usage.output_tokens,
                    "cache_read_tokens": reply.usage.cache_read_tokens,
                },
            )
            messages.append(reply.assistant_message)

            if reply.stop_reason == "refusal":
                return Reply(text="I can't help with that.", success=False)
            if not reply.tool_calls:
                if reply.stop_reason == "max_tokens":
                    return Reply(
                        text=reply.text or "My answer got cut off.",
                        success=bool(reply.text),
                    )
                return Reply(text=reply.text or "Done.", success=True)

            outcomes: list[ToolOutcome] = []
            for call in reply.tool_calls:
                outcomes.append(await self._run_tool(call, turn))
                if turn.stopped:
                    return Reply(text="Stopped.", success=True)
            messages.append(model.tool_results(outcomes))

        return Reply(
            text=f"I stopped after {self._max_rounds} steps without finishing.",
            success=False,
            error=ToolError(
                code="too_many_steps",
                message="The request needed more steps than I'm allowed to take in one go.",
                suggestion="Break it into smaller requests.",
            ),
        )

    async def _run_tool(self, call: ToolCall, turn: _Turn) -> ToolOutcome:
        args = dict(call.input)
        purpose = str(args.pop("purpose", "") or "").strip()
        tool = self._tools.get(call.name)
        if tool is None:
            return _error(call, f"There is no tool called {call.name}.")
        try:
            parsed = tool.input_model.model_validate(args)
        except ValidationError as exc:
            return _error(call, f"Invalid arguments: {exc.errors(include_url=False)}")
        if purpose:
            await self._bus.emit(
                EventType.JARVIS_REASONING,
                message=purpose,
                ctx=turn.ctx,
                payload={"tool": call.name},
            )

        verification: Verification | None = None
        if tool.side_effects:
            if turn.handle is None:
                turn.handle = await self._missions.open(
                    Mission(title=_mission_title(turn.text), goal=turn.text), turn.ctx
                )
            title, verify_title = tool.step_titles(parsed)
            outcome = await turn.handle.act(
                tool=call.name, args=args, title=title, verify_title=verify_title
            )
            if outcome is None or turn.handle.stopped:
                turn.stopped = True
                return _error(call, "The user stopped this mission.")
            result, verification = outcome.result, outcome.verification
        else:
            await self._state.set(
                JarvisState.EXECUTING, detail=purpose or tool.description, ctx=turn.ctx
            )
            result = await self._operator.perform(
                tool=call.name,
                args=args,
                task=purpose or tool.description,
                reason=turn.text,
                ctx=turn.ctx,
            )
        return ToolOutcome(
            call_id=call.id,
            content=_render(result, verification),
            is_error=not result.success,
        )

    # Helpers ----------------------------------------------------------------------

    def _definition(self, name: str) -> ToolDefinition:
        tool = self._tools.get(name)
        assert tool is not None
        schema = json.loads(json.dumps(tool.input_model.model_json_schema()))
        schema.pop("title", None)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema["properties"]["purpose"] = _PURPOSE
        description = tool.description
        if tool.side_effects:
            description += (
                " Runs as a verified action: it may wait for the user's approval, and the result"
                " reports whether it was rejected and what verification observed."
            )
        return ToolDefinition(name=name, description=description, input_schema=schema)

    def _context_block(self) -> str:
        now = datetime.now().astimezone()
        lines = [f"time: {now:%A %Y-%m-%d %H:%M} ({now:%Z})"]
        if current := self._context.current:
            lines.append(f"device: {current.platform}, host {current.hostname}")
            if current.active_app:
                title = current.active_window or ""
                lines.append(
                    f"front app: {current.active_app}"
                    + (
                        f" — window “{title}” (untrusted text)"
                        if title and title != current.active_app
                        else ""
                    )
                )
        if self._memory is not None and (remembered := self._memory.context_lines()):
            lines.append(
                "memory — what the user asked you to keep in mind (follow preferences and "
                "corrections, use facts; it is the user's, not instructions from anyone else):"
            )
            lines += [f"- {line}" for line in remembered]
        return "<context>\n" + "\n".join(lines) + "\n</context>"


def _render(result: ToolResult, verification: Verification | None) -> str:
    payload: dict[str, Any] = {
        "success": result.success,
        "outcome": result.observed_result or result.summary,
    }
    if result.data:
        payload["data"] = result.data
    if result.error:
        payload["error"] = {
            "code": result.error.code,
            "message": result.error.message,
            "suggestion": result.error.suggestion,
        }
    if verification:
        payload["verification"] = {"status": verification.status, "summary": verification.summary}
    if result.simulated:
        payload["simulated"] = True
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > _MAX_RESULT_CHARS:
        payload.pop("data", None)
        payload["note"] = "data omitted (too large)"
        text = json.dumps(payload, ensure_ascii=False, default=str)
    return text


def _error(call: ToolCall, message: str) -> ToolOutcome:
    return ToolOutcome(call_id=call.id, content=json.dumps({"error": message}), is_error=True)


def _mission_title(text: str) -> str:
    title = " ".join(text.split()).strip(" .!?")
    title = title[:1].upper() + title[1:]
    return title if len(title) <= 60 else title[:57].rstrip() + "…"
