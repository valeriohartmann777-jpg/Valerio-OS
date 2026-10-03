"""Runs missions step by step with pause / resume / stop and approval awareness."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.agents.base import AgentRegistry
from jarvis.agents.workers import OperatorAgent, SentinelAgent
from jarvis.core.responses import Reply, ResponseComposer
from jarvis.core.state import JarvisState, StateService
from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType, Severity
from jarvis.missions.models import (
    Mission,
    MissionStatus,
    MissionStep,
    StepKind,
    StepStatus,
)
from jarvis.missions.repository import MissionRepository
from jarvis.permissions.service import PermissionService
from jarvis.tools.base import ToolError, ToolResult, Verification
from jarvis.util import utcnow

log = logging.getLogger("jarvis.missions")

FinishedHook = Callable[[Mission, Reply, TraceContext], Awaitable[None]]


class MissionError(Exception):
    pass


@dataclass
class _Run:
    mission: Mission
    ctx: TraceContext
    running: asyncio.Event = field(default_factory=asyncio.Event)
    stop_requested: bool = False
    task: asyncio.Task[None] | None = None


class MissionEngine:
    def __init__(
        self,
        *,
        repository: MissionRepository,
        bus: EventBus,
        state: StateService,
        agents: AgentRegistry,
        operator: OperatorAgent,
        sentinel: SentinelAgent,
        permissions: PermissionService,
        composer: ResponseComposer,
    ) -> None:
        self._repo = repository
        self._bus = bus
        self._state = state
        self._agents = agents
        self._operator = operator
        self._sentinel = sentinel
        self._permissions = permissions
        self._composer = composer
        self._runs: dict[str, _Run] = {}
        self._on_finished: FinishedHook | None = None
        bus.subscribe("permission.*", self._on_permission)

    def on_finished(self, hook: FinishedHook) -> None:
        self._on_finished = hook

    # Queries ---------------------------------------------------------------

    async def get(self, mission_id: str) -> Mission | None:
        if run := self._runs.get(mission_id):
            return run.mission
        return await self._repo.get(mission_id)

    async def list(self, limit: int = 50) -> list[Mission]:
        missions = await self._repo.list(limit)
        return [self._runs[m.id].mission if m.id in self._runs else m for m in missions]

    # Lifecycle -------------------------------------------------------------

    async def submit(self, mission: Mission, ctx: TraceContext) -> Mission:
        """Run a mission whose steps are planned up front (in the background)."""
        run = await self._register(mission, ctx)
        run.task = asyncio.create_task(self._execute(run))
        return mission

    async def open(self, mission: Mission, ctx: TraceContext) -> MissionHandle:
        """Start an open-ended mission whose steps are added as they are decided
        (model-driven turns). The caller drives it through the returned handle."""
        run = await self._register(mission, ctx)
        mission.status = MissionStatus.ACTIVE
        await self._publish(run, f"Mission {mission.number:03d} started")
        return MissionHandle(self, run)

    async def _register(self, mission: Mission, ctx: TraceContext) -> _Run:
        mission.number = await self._repo.next_number()
        mission.trace_id = ctx.trace_id
        run = _Run(mission=mission, ctx=ctx.for_mission(mission.id))
        run.running.set()
        self._runs[mission.id] = run
        await self._repo.save(mission)
        await self._bus.emit(
            EventType.MISSION_CREATED,
            message=f"Mission {mission.number:03d} created — {mission.title}",
            ctx=run.ctx,
            payload={"mission": mission.model_dump(mode="json")},
        )
        return run

    async def pause(self, mission_id: str) -> Mission:
        run = self._active(mission_id)
        if run.running.is_set():
            run.running.clear()
            run.mission.status = MissionStatus.PAUSED
            await self._publish(run, f"Mission {run.mission.number:03d} paused", Severity.IMPORTANT)
        return run.mission

    async def resume(self, mission_id: str) -> Mission:
        run = self._active(mission_id)
        if not run.running.is_set():
            run.mission.status = MissionStatus.ACTIVE
            run.running.set()
            await self._publish(
                run, f"Mission {run.mission.number:03d} resumed", Severity.IMPORTANT
            )
        return run.mission

    async def stop(self, mission_id: str) -> Mission:
        run = self._active(mission_id)
        if not run.stop_requested:
            run.stop_requested = True
            run.running.set()
            await self._publish(
                run, f"Stopping mission {run.mission.number:03d}", Severity.IMPORTANT
            )
            await self._permissions.reject_for_mission(mission_id, "Mission stopped by user.")
        return run.mission

    async def wait_idle(self) -> None:
        """Wait for all running missions (tests and shutdown)."""
        tasks = [run.task for run in self._runs.values() if run.task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        for run in list(self._runs.values()):
            if run.task:
                run.task.cancel()
        await self.wait_idle()

    def _active(self, mission_id: str) -> _Run:
        run = self._runs.get(mission_id)
        if run is None:
            raise MissionError("That mission is not running.")
        return run

    # Execution -------------------------------------------------------------

    async def _execute(self, run: _Run) -> None:
        try:
            await self._run_steps(run)
        except asyncio.CancelledError:
            run.stop_requested = True
            raise
        except Exception as exc:
            log.exception("mission crashed", extra=run.ctx.log_fields())
            run.mission.status = MissionStatus.FAILED
            run.mission.errors.append(
                ToolError(
                    code="internal_error",
                    message="The mission stopped because of an internal error.",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            try:
                await self._finalize(run)
            finally:
                self._runs.pop(run.mission.id, None)

    async def _run_steps(self, run: _Run) -> None:
        mission = run.mission
        mission.status = MissionStatus.ACTIVE
        await self._publish(run, f"Mission {mission.number:03d} started")
        results: dict[int, ToolResult] = {}
        for step in mission.steps:
            if not await self._checkpoint(run):
                return
            source = results.get(step.depends_on) if step.depends_on is not None else None
            if step.kind is StepKind.VERIFY and (source is None or not source.success):
                step.status = StepStatus.SKIPPED
                step.summary = "Skipped — nothing to verify"
                await self._publish(run, f"{step.title} skipped")
                continue
            await self._start_step(run, step)
            if step.kind is StepKind.ACTION:
                results[step.index] = await self._run_action(run, step)
            elif source is not None:
                await self._run_verify(run, step, source)
            await self._end_step(run, step)

    async def _start_step(self, run: _Run, step: MissionStep) -> None:
        step.status = StepStatus.ACTIVE
        step.started_at = utcnow()
        run.mission.current_step = step.index
        await self._publish(run, f"{step.title} started")

    async def _end_step(self, run: _Run, step: MissionStep) -> None:
        step.finished_at = utcnow()
        await self._publish(run, f"{step.title}: {step.status}")

    async def _run_action(self, run: _Run, step: MissionStep) -> ToolResult:
        mission = run.mission
        assert step.tool is not None
        await self._state.set(
            JarvisState.DELEGATING, detail=f"Operator · {step.title}", ctx=run.ctx
        )
        await self._state.set(JarvisState.EXECUTING, detail=step.title, ctx=run.ctx)
        result = await self._operator.perform(
            tool=step.tool, args=step.args, task=step.title, reason=mission.goal, ctx=run.ctx
        )
        step.result = result
        step.summary = result.observed_result or result.summary
        if step.tool not in mission.tools_used:
            mission.tools_used.append(step.tool)
        if result.success:
            step.status = StepStatus.COMPLETE
        else:
            step.error = result.error
            denied = result.error is not None and result.error.code == "permission_denied"
            step.status = StepStatus.REJECTED if denied else StepStatus.FAILED
            if result.error and not denied:
                mission.errors.append(result.error)
        return result

    async def _run_verify(self, run: _Run, step: MissionStep, source: ToolResult) -> None:
        assert step.tool is not None
        await self._state.set(JarvisState.VERIFYING, detail=step.title, ctx=run.ctx)
        verification = await self._sentinel.verify(
            tool=step.tool, args=step.args, result=source, task=step.title, ctx=run.ctx
        )
        step.verification = verification
        step.summary = verification.summary
        step.status = StepStatus.FAILED if verification.status == "failed" else StepStatus.COMPLETE
        if verification.status == "failed":
            run.mission.errors.append(
                ToolError(code="verification_failed", message=verification.summary)
            )

    async def _checkpoint(self, run: _Run) -> bool:
        if run.stop_requested:
            return False
        if not run.running.is_set():
            await self._state.set(JarvisState.PAUSED, detail=run.mission.title, ctx=run.ctx)
            await run.running.wait()
        return not run.stop_requested

    async def _finalize(self, run: _Run, reply: Reply | None = None) -> None:
        """Settle status and result. ``reply`` given = the caller answers the
        user itself (model-driven missions); otherwise the composer does."""
        mission = run.mission
        steps = mission.steps
        if mission.status is not MissionStatus.FAILED:
            if run.stop_requested or any(s.status is StepStatus.REJECTED for s in steps):
                mission.status = MissionStatus.STOPPED
            elif any(s.status is StepStatus.FAILED for s in steps):
                mission.status = MissionStatus.FAILED
            else:
                mission.status = MissionStatus.COMPLETE
        for step in steps:
            if step.status in (
                StepStatus.WAITING,
                StepStatus.ACTIVE,
                StepStatus.WAITING_FOR_APPROVAL,
            ):
                step.status = StepStatus.SKIPPED
        mission.verification = _aggregate([s.verification for s in steps if s.verification])
        mission.current_step = None
        mission.finished_at = utcnow()
        caller_replies = reply is not None
        if reply is None:
            reply = self._composer.mission(mission, stopped=run.stop_requested)
        mission.result = reply.text
        # The root cause is already in the stream as an error; the outcome is neutral.
        severity = Severity.IMPORTANT if mission.status is MissionStatus.COMPLETE else Severity.INFO
        await self._publish(run, f"Mission {mission.number:03d} {mission.status}", severity)
        if self._on_finished is not None and not caller_replies:
            await self._on_finished(mission, reply, run.ctx)

    async def _publish(self, run: _Run, message: str, severity: Severity = Severity.DEBUG) -> None:
        run.mission.touch()
        await self._repo.save(run.mission)
        await self._bus.emit(
            EventType.MISSION_UPDATED,
            message=message,
            severity=severity,
            ctx=run.ctx,
            payload={"mission": run.mission.model_dump(mode="json")},
        )

    # Approvals -------------------------------------------------------------

    async def _on_permission(self, event: Event) -> None:
        run = self._runs.get(event.mission_id or "")
        if run is None:
            return
        mission = run.mission
        request = event.payload.get("request", {})
        agent = request.get("agent")
        step = mission.steps[mission.current_step] if mission.current_step is not None else None
        if event.type is EventType.PERMISSION_REQUESTED:
            mission.status = MissionStatus.WAITING_FOR_APPROVAL
            mission.approvals.append(request.get("id", ""))
            if step:
                step.status = StepStatus.WAITING_FOR_APPROVAL
            action = request.get("action", {})
            detail = " — ".join(p for p in (action.get("title"), action.get("target")) if p)
            await self._state.set(JarvisState.WAITING_FOR_APPROVAL, detail=detail, ctx=run.ctx)
            if agent:
                await self._agents.wait(agent, "Awaiting your approval", run.ctx)
            await self._publish(run, "Waiting for approval")
        elif event.type is EventType.PERMISSION_APPROVED:
            if run.running.is_set():
                mission.status = MissionStatus.ACTIVE
            if step:
                step.status = StepStatus.ACTIVE
            await self._state.set(
                JarvisState.EXECUTING, detail=step.title if step else "", ctx=run.ctx
            )
            if agent:
                await self._agents.resume(agent, run.ctx)
            await self._publish(run, "Approval granted")
        else:  # rejected / expired — the step outcome is recorded when the tool returns
            if run.running.is_set():
                mission.status = MissionStatus.ACTIVE
            await self._publish(run, "Approval declined")


@dataclass(frozen=True)
class ActionOutcome:
    result: ToolResult
    verification: Verification | None


class MissionHandle:
    """Drives an open-ended mission one action at a time.

    Every action becomes an *act* step (Operator) followed by a *verify* step
    (Sentinel) — the same guarantees as planned missions, including approvals,
    pause and stop.
    """

    def __init__(self, engine: MissionEngine, run: _Run) -> None:
        self._engine = engine
        self._run = run
        self._finished = False

    @property
    def mission(self) -> Mission:
        return self._run.mission

    @property
    def stopped(self) -> bool:
        return self._run.stop_requested

    async def act(
        self, *, tool: str, args: dict[str, Any], title: str, verify_title: str
    ) -> ActionOutcome | None:
        """Execute and verify one action. ``None`` when the mission was stopped."""
        engine, run = self._engine, self._run
        if not await engine._checkpoint(run):
            return None
        mission = run.mission
        act = MissionStep(
            index=len(mission.steps),
            title=title,
            kind=StepKind.ACTION,
            agent="operator",
            tool=tool,
            args=args,
        )
        verify = MissionStep(
            index=act.index + 1,
            title=verify_title,
            kind=StepKind.VERIFY,
            agent="sentinel",
            tool=tool,
            args=args,
            depends_on=act.index,
        )
        mission.steps.extend([act, verify])
        for agent in ("operator", "sentinel"):
            if agent not in mission.agents:
                mission.agents.append(agent)

        await engine._start_step(run, act)
        result = await engine._run_action(run, act)
        await engine._end_step(run, act)

        verification: Verification | None = None
        if result.success:
            await engine._start_step(run, verify)
            await engine._run_verify(run, verify, result)
            verification = verify.verification
            await engine._end_step(run, verify)
        else:
            verify.status = StepStatus.SKIPPED
            verify.summary = "Skipped — nothing to verify"
            await engine._publish(run, f"{verify.title} skipped")
        return ActionOutcome(result=result, verification=verification)

    async def finish(self, reply: Reply) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            await self._engine._finalize(self._run, reply)
        finally:
            self._engine._runs.pop(self._run.mission.id, None)


def _aggregate(verifications: list[Verification]) -> Verification | None:
    if not verifications:
        return None
    verified = sum(1 for v in verifications if v.verified)
    total = len(verifications)
    if verified == total:
        status: str = "verified"
    elif any(v.status == "failed" for v in verifications):
        status = "failed"
    else:
        status = "unverifiable"
    return Verification.model_validate(
        {
            "status": status,
            "method": "sentinel",
            "summary": f"{verified} of {total} actions verified",
            "evidence": [v.summary for v in verifications],
        }
    )
