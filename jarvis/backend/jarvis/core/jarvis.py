"""JarvisCore: receives a command and carries it through the lifecycle.

understand → plan → delegate/act/verify → respond. Two paths:

- INSTANT: commands the rule router understands with certainty ("open safari",
  "system status") run without any model call — minimum latency, zero cost.
- REASONING: everything else goes to the Brain (Claude), which plans and calls
  the same tools through the same Operator / permission / Sentinel chain.
  ``think: …`` selects the reasoning model.

JARVIS is the only voice the user hears.
"""

from __future__ import annotations

import asyncio
import logging

from jarvis.agents.workers import OperatorAgent
from jarvis.core.brain import Brain
from jarvis.core.responses import Reply, ResponseComposer
from jarvis.core.router import Intent, IntentKind, Router, split_think
from jarvis.core.state import JarvisState, StateService
from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.missions.engine import MissionEngine
from jarvis.missions.models import Mission
from jarvis.missions.planner import DeterministicPlanner
from jarvis.tools.registry import ToolRegistry

log = logging.getLogger("jarvis.core")


class JarvisCore:
    def __init__(
        self,
        *,
        bus: EventBus,
        state: StateService,
        router: Router,
        planner: DeterministicPlanner,
        missions: MissionEngine,
        operator: OperatorAgent,
        composer: ResponseComposer,
        brain: Brain,
        tools: ToolRegistry,
    ) -> None:
        self._bus = bus
        self._state = state
        self._router = router
        self._planner = planner
        self._missions = missions
        self._operator = operator
        self._composer = composer
        self._brain = brain
        self._tools = tools
        self._tasks: set[asyncio.Task[None]] = set()
        missions.on_finished(self._on_mission_finished)

    def submit(self, text: str, *, source: str = "text") -> TraceContext:
        """Accept a command and process it in the background. Returns its trace."""
        ctx = TraceContext.new()
        task = asyncio.create_task(self._guarded(text, source, ctx))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return ctx

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self._missions.wait_idle()

    async def _guarded(self, text: str, source: str, ctx: TraceContext) -> None:
        try:
            await self.handle(text, source=source, ctx=ctx)
        except Exception:
            log.exception("command failed", extra=ctx.log_fields())
            await self._respond(
                Reply(
                    text="Something went wrong on my side. The details are in the log.",
                    success=False,
                ),
                ctx,
            )

    async def handle(
        self, text: str, *, source: str = "text", ctx: TraceContext | None = None
    ) -> Intent:
        ctx = ctx or TraceContext.new()
        await self._bus.emit(
            EventType.COMMAND_RECEIVED,
            message=f"Command received — “{text}”",
            ctx=ctx,
            payload={"text": text, "source": source},
        )
        await self._state.set(JarvisState.UNDERSTANDING, detail=text, ctx=ctx)
        think, body = split_think(text)
        intent = self._router.route(body)
        reasoning = think or intent.kind is IntentKind.UNSUPPORTED
        if intent.kind is IntentKind.ACTION and not reasoning and self._brain.available:
            # Only take the instant path when every target is certain to work;
            # "open the notes thing" is better understood by the model.
            reasoning = not await self._targets_resolvable(intent, ctx)
        route = "think" if think else ("reasoning" if reasoning else "instant")
        await self._bus.emit(
            EventType.INTENT_CLASSIFIED,
            message=f"Intent classified — {_describe(intent, route)}",
            ctx=ctx,
            payload={"intent": intent.model_dump(mode="json"), "route": route},
        )
        if reasoning:
            reply = await self._brain.respond(body, ctx, mode="think" if think else "fast")
            await self._respond(reply, ctx)
            return intent
        match intent.kind:
            case IntentKind.ACTION:
                mission = self._planner.plan(intent)
                await self._state.set(JarvisState.PLANNING, detail=mission.title, ctx=ctx)
                await self._missions.submit(mission, ctx)
            case IntentKind.QUERY:
                reply = await self._answer(intent, ctx)
                self._brain.remember(body, reply.text)
            case _:
                reply = self._composer.conversation(intent)
                await self._respond(reply, ctx)
                self._brain.remember(body, reply.text)
        return intent

    async def _targets_resolvable(self, intent: Intent, ctx: TraceContext) -> bool:
        tool = self._tools.get("open_application")
        if tool is None:
            return False
        for target in intent.targets:
            args = tool.input_model.model_validate({"name": target})
            if await tool.precheck(args, ctx) is not None:
                return False
        return True

    async def create_mission(self, goal: str) -> Mission:
        """Plan and start a mission directly (API). Raises ``ValueError`` if unplannable."""
        intent = self._router.route(goal)
        if intent.kind is not IntentKind.ACTION:
            raise ValueError("I can only plan application launches as missions for now.")
        ctx = TraceContext.new()
        return await self._missions.submit(self._planner.plan(intent), ctx)

    async def _answer(self, intent: Intent, ctx: TraceContext) -> Reply:
        assert intent.tool is not None
        await self._state.set(JarvisState.EXECUTING, detail=intent.summary, ctx=ctx)
        result = await self._operator.perform(
            tool=intent.tool, args=intent.args, task=intent.summary, reason=intent.text, ctx=ctx
        )
        reply = self._composer.query(intent, result)
        await self._respond(reply, ctx)
        return reply

    async def _on_mission_finished(self, mission: Mission, reply: Reply, ctx: TraceContext) -> None:
        self._brain.remember(mission.goal, reply.text)
        await self._respond(reply, ctx)

    async def _respond(self, reply: Reply, ctx: TraceContext) -> None:
        await self._bus.emit(
            EventType.JARVIS_MESSAGE,
            message=reply.text,
            severity=Severity.IMPORTANT,
            ctx=ctx,
            payload={
                "text": reply.text,
                "success": reply.success,
                "error": reply.error.model_dump(exclude={"detail"}) if reply.error else None,
            },
        )
        await self._state.set(
            JarvisState.COMPLETE if reply.success else JarvisState.FAILED,
            detail=reply.text,
            ctx=ctx,
        )


def _describe(intent: Intent, route: str) -> str:
    if route == "think":
        return "open request → reasoning model (think mode)"
    if route == "reasoning":
        return f"{intent.summary} → reasoning"
    return f"{intent.summary} ({intent.complexity})"
