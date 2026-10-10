"""The ULTRON runtime: missions from goal to verified result.

goal → JARVIS plans (charter + task DAG, validated in code) → the scheduler runs
ready tasks in parallel (bounded) → AXIOM/FORGE work in their own worktrees →
the runtime commits, runs the task's checks itself and merges → SENTINEL
reviews a fresh checkout, with the runtime's own check results in hand →
rejected work goes back to FORGE (bounded) → final acceptance → approval-gated
delivery. SQLite is the queue: after a restart, interrupted tasks run again
from a clean tree; nothing is marked complete without evidence.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from jarvis.ai.checkpoint import RunCheckpoint
from jarvis.ai.types import AIPaused, ai_scope
from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType, Severity
from jarvis.llm.base import ChatModel, ModelError, Usage
from jarvis.settings import UltronSettings
from jarvis.storage.preferences import Preferences
from jarvis.ultron import plan as planning
from jarvis.ultron import prompts, sandbox
from jarvis.ultron.agents import BY_ID, ROSTER
from jarvis.ultron.models import (
    MISSION_DONE,
    ApprovalState,
    MissionState,
    TaskState,
    check_task_transition,
)
from jarvis.ultron.policy import CATEGORIES, PolicyError, check_command, effect_signature
from jarvis.ultron.runner import Budget, BudgetExceeded, run_agent
from jarvis.ultron.store import UltronStore
from jarvis.ultron.tools import SUBMIT_PLAN, Broker, Scope, definitions
from jarvis.ultron.workspace import GitError, Workspace, text
from jarvis.util import new_id, utcnow

log = logging.getLogger("jarvis.ultron")

ModelFactory = Callable[[str], ChatModel | None]
SENTINEL_TOOLS = ("list_files", "read_file", "write_file", "run_command", "submit_review")
PRODUCER_TOOLS = {
    "axiom": ("list_files", "read_file", "write_file", "edit_file", "submit_result"),
    "forge": ("list_files", "read_file", "write_file", "edit_file", "run_command", "submit_result"),
}
_ID = re.compile(r"^m\d{3,}-[0-9a-f]{6}$")


class UltronError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _slug(text_: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text_.lower()).strip("-")[:40] or "project"


class UltronService:
    def __init__(
        self,
        *,
        settings: UltronSettings,
        store: UltronStore,
        bus: EventBus,
        root: Path,
        repo_root: Path | None,
        jarvis_subdir: str,
        preferences: Preferences,
        model_factory: ModelFactory,
        model_label: Callable[[], str],
        checkpoints: Callable[[str], RunCheckpoint] | None = None,
    ) -> None:
        self._settings = settings
        self._checkpoints = checkpoints
        self._store = store
        self._bus = bus
        self._root = root
        self._repo_root = repo_root
        self._jarvis_subdir = jarvis_subdir
        self._prefs = preferences
        self._model = model_factory
        self._model_label = model_label
        self._running: dict[str, asyncio.Task[None]] = {}  # task id or "plan:<mission>"
        self._gates: dict[str, asyncio.Event] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._workspaces: dict[str, Workspace] = {}
        self._wake = asyncio.Event()
        self._loop: asyncio.Task[None] | None = None
        self._stopping = False
        self._finishing: set[str] = set()

    # Configuration -------------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        saved = self._prefs.get("ultron", {}) or {}
        s = self._settings
        return {
            "enabled": s.enabled,
            "budget_usd_per_mission": float(
                saved.get("budget_usd_per_mission", s.budget_usd_per_mission)
            ),
            "max_parallel_workers": int(saved.get("max_parallel_workers", s.max_parallel_workers)),
            "max_attempts": int(saved.get("max_attempts", s.max_attempts)),
            "max_rounds_per_run": s.max_rounds_per_run,
            "command_timeout_seconds": s.command_timeout_seconds,
            "max_delegation_depth": 1,
            "export_dir": str(self._export_dir()),
            "agents": {k: v.model_dump() for k, v in s.agents.items()},
        }

    def update_config(self, values: dict[str, Any]) -> dict[str, Any]:
        saved = dict(self._prefs.get("ultron", {}) or {})
        limits = {
            "budget_usd_per_mission": (0.05, 500.0, float),
            "max_parallel_workers": (1, 6, int),
            "max_attempts": (1, 5, int),
        }
        for key, (low, high, kind) in limits.items():
            if key in values and values[key] is not None:
                value = kind(values[key])
                if not low <= value <= high:
                    raise UltronError("INVALID", f"{key} must be between {low} and {high}.")
                saved[key] = value
        self._prefs.update(ultron=saved)
        self._wake.set()
        return self.config()

    def _export_dir(self) -> Path:
        if self._settings.export_dir:
            return Path(self._settings.export_dir).expanduser()
        return Path.home() / "Documents" / "JARVIS ULTRON"

    # Lifecycle -----------------------------------------------------------------------

    async def start(self) -> None:
        interrupted = await self._store.interrupt_runs()
        resumed = 0
        for task in await self._store.tasks_in("RUNNING", "VERIFYING", "RETRYING"):
            await self._store.update_task(
                task["id"],
                state=TaskState.READY,
                feedback=(task["feedback"] or "")
                + "\nThe previous attempt was interrupted when JARVIS stopped.",
            )
            resumed += 1
        for mission in await self._store.missions_in(*[s.value for s in MissionState]):
            gate = self._gate(mission["id"])
            if mission["state"] == MissionState.PAUSED:
                gate.clear()
            if mission["state"] == MissionState.PLANNING:
                self._spawn(f"plan:{mission['id']}", self._plan(mission["id"]))
        if interrupted or resumed:
            await self._log(
                "runtime.recovered",
                f"Recovered after a restart: {resumed} task(s) will run again "
                f"({interrupted} interrupted run(s) recorded).",
                severity="warning",
            )
        self._loop = asyncio.create_task(self._schedule_forever(), name="ultron-scheduler")

    async def stop(self) -> None:
        self._stopping = True
        if self._loop is not None:
            self._loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _gate(self, mission_id: str) -> asyncio.Event:
        if mission_id not in self._gates:
            event = asyncio.Event()
            event.set()
            self._gates[mission_id] = event
        return self._gates[mission_id]

    def _lock(self, mission_id: str) -> asyncio.Lock:
        return self._locks.setdefault(mission_id, asyncio.Lock())

    def _spawn(self, key: str, coro: Any) -> None:
        task = asyncio.create_task(coro, name=f"ultron-{key}")
        self._running[key] = task

        def done(_: asyncio.Task[None]) -> None:
            if self._running.get(key) is task:
                del self._running[key]
            self._wake.set()

        task.add_done_callback(done)

    def _workspace(self, mission: dict[str, Any]) -> Workspace:
        if mission["id"] not in self._workspaces:
            self._workspaces[mission["id"]] = Workspace(
                Path(mission["workspace"]),
                mission["id"],
                mission["project_kind"],
                source=self._repo_root,
                subdir=self._jarvis_subdir,
            )
        return self._workspaces[mission["id"]]

    # Events --------------------------------------------------------------------------

    async def _log(
        self,
        kind: str,
        message: str,
        *,
        severity: str = "info",
        mission_id: str | None = None,
        task_id: str | None = None,
        agent: str | None = None,
        data: dict[str, Any] | None = None,
        event_type: EventType = EventType.ULTRON_ACTIVITY,
    ) -> None:
        record = await self._store.add_event(
            kind=kind,
            message=message,
            severity=severity,
            mission_id=mission_id,
            task_id=task_id,
            agent=agent,
            data=data,
        )
        await self._bus.publish(
            Event(
                type=event_type,
                source=f"ultron.{agent}" if agent else "ultron",
                message=message,
                severity=Severity(severity),
                trace_id=mission_id,
                payload={k: v for k, v in record.items() if k != "message"},
            )
        )

    async def _mission_state(
        self, mission_id: str, state: MissionState, message: str, **fields: Any
    ) -> None:
        if state in MISSION_DONE:
            fields.setdefault("finished_at", utcnow().isoformat())
        await self._store.update_mission(mission_id, state=state, **fields)
        await self._log(
            "mission.state",
            message,
            mission_id=mission_id,
            severity="warning" if state in (MissionState.BLOCKED, MissionState.FAILED) else "info",
            data={"state": state},
            event_type=EventType.ULTRON_MISSION_CHANGED,
        )
        self._wake.set()

    async def _task_state(
        self, task: dict[str, Any], state: TaskState, message: str, **fields: Any
    ) -> None:
        check_task_transition(TaskState(task["state"]), state)
        await self._store.update_task(task["id"], state=state, **fields)
        task["state"] = state
        await self._log(
            "task.state",
            message,
            mission_id=task["mission_id"],
            task_id=task["id"],
            agent=task["owner"],
            severity="warning" if state in (TaskState.BLOCKED, TaskState.FAILED) else "info",
            data={"state": state, "key": task["key"], "attempt": task["attempts"]},
            event_type=EventType.ULTRON_TASK_CHANGED,
        )
        self._wake.set()

    def _auditor(self, mission_id: str, task_id: str | None, agent: str) -> Any:
        async def audit(*, kind: str, message: str, severity: str, data: dict[str, Any]) -> None:
            await self._log(
                kind,
                message,
                severity=severity,
                mission_id=mission_id,
                task_id=task_id,
                agent=agent,
                data=data,
            )

        return audit

    # Missions ------------------------------------------------------------------------

    async def create_mission(
        self, goal: str, *, project: str = "sandbox", budget_usd: float | None = None
    ) -> dict[str, Any]:
        if not self._settings.enabled:
            raise UltronError("DISABLED", "ULTRON is turned off in config/ultron.yaml.")
        goal = goal.strip()
        if len(goal) < 8:
            raise UltronError("INVALID", "Describe the goal in a sentence or two.")
        if project not in ("sandbox", "jarvis"):
            raise UltronError("INVALID", "project must be 'sandbox' or 'jarvis'.")
        if project == "jarvis" and (
            self._repo_root is None or not (self._repo_root / ".git").exists()
        ):
            raise UltronError("NO_REPO", "This JARVIS isn't running from a git checkout.")
        if self._model("jarvis") is None:
            raise UltronError(
                "NO_MODEL",
                "ULTRON needs Claude: add your Anthropic API key under Settings → Brain.",
            )
        budget = budget_usd if budget_usd is not None else self.config()["budget_usd_per_mission"]
        if not 0.05 <= budget <= 500:
            raise UltronError("INVALID", "The budget must be between $0.05 and $500.")
        mission = await self._store.add_mission(
            goal=goal,
            kind=project,
            budget=budget,
            workspace=str(self._root / "workspaces" / "{id}"),
            model_label=self._model_label(),
        )
        await self._log(
            "mission.created",
            f"Mission {mission['id']} created: {mission['title']}",
            mission_id=mission["id"],
            agent="jarvis",
            data={"state": MissionState.PLANNING, "project": project, "budget_usd": budget},
            event_type=EventType.ULTRON_MISSION_CHANGED,
        )
        self._spawn(f"plan:{mission['id']}", self._plan(mission["id"]))
        return await self.mission(mission["id"])

    def _checked(self, mission_id: str) -> str:
        if not _ID.match(mission_id):
            raise UltronError("NOT_FOUND", "No such mission.", 404)
        return mission_id

    async def _get(self, mission_id: str) -> dict[str, Any]:
        mission = await self._store.mission(self._checked(mission_id))
        if mission is None:
            raise UltronError("NOT_FOUND", "No such mission.", 404)
        return mission

    async def _budget(self, mission_id: str, agent: str) -> Budget:
        model_cfg = self._settings.agents.get(agent)
        scripted = bool(self._settings.scripted_model)
        price = None
        if not scripted and model_cfg is not None:
            price = self._settings.prices.get(model_cfg.model)
            if price is None:
                raise UltronError(
                    "NO_PRICE", f"No price for {model_cfg.model} in config/ultron.yaml."
                )

        async def remaining() -> float:
            m = await self._store.mission(mission_id)
            return 0.0 if m is None else float(m["budget_usd"]) - float(m["spent_usd"])

        return Budget(price, model_cfg.max_tokens if model_cfg else 16000, remaining)

    def _usage_sink(self, mission_id: str, task_id: str | None, run_id: str) -> Any:
        totals = {"in": 0, "out": 0, "cost": 0.0}

        async def sink(usage: Usage, cost: float) -> None:
            totals["in"] += usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
            totals["out"] += usage.output_tokens
            totals["cost"] += cost
            await self._store.add_spend(mission_id, cost)
            if task_id:
                await self._store.add_task_cost(task_id, cost)
            await self._store.update_run(
                run_id,
                input_tokens=totals["in"],
                output_tokens=totals["out"],
                cost_usd=totals["cost"],
            )

        return sink

    # Planning (JARVIS) -----------------------------------------------------------------

    def _project_brief(self, mission: dict[str, Any]) -> str:
        if mission["project_kind"] == "jarvis":
            prefix = f"{self._jarvis_subdir}/" if self._jarvis_subdir else ""
            return (
                prompts.PROJECT_JARVIS.format(branch=f"ultron/{mission['id']}")
                .replace("backend/", f"{prefix}backend/")
                .replace("apps/desktop/", f"{prefix}apps/desktop/")
                .replace("docs/", f"{prefix}docs/")
            )
        return prompts.PROJECT_SANDBOX

    async def _plan(self, mission_id: str) -> None:
        mission = await self._get(mission_id)
        model = self._model("jarvis")
        if model is None:
            await self._mission_state(
                mission_id,
                MissionState.BLOCKED,
                "JARVIS can't plan: no model available.",
                blocker={
                    "kind": "model",
                    "message": "Add your Anthropic API key under Settings → Brain.",
                },
            )
            return
        run_id = await self._store.add_run(
            mission_id, None, "jarvis", int(mission["revision"]) + 1, model.model_id
        )
        await self._log(
            "agent.started",
            "JARVIS is planning the mission",
            mission_id=mission_id,
            agent="jarvis",
            data={"run_id": run_id},
        )
        answers = (mission.get("charter") or {}).get("answers", [])
        opening = f"Goal from the user:\n{mission['goal']}"
        if answers:
            opening += "\n\nThe user answered your earlier questions:\n" + "\n".join(answers)

        def validate(_: str, raw: dict[str, Any]) -> str | None:
            try:
                planning.validate(raw)
            except planning.PlanError as exc:
                return "; ".join(exc.problems)
            return None

        try:
            with ai_scope("ultron", mission_id=mission_id, agent="jarvis"):
                result = await run_agent(
                    model=model,
                    system=prompts.PLANNER.format(project=self._project_brief(mission)),
                    opening=opening,
                    tools=[SUBMIT_PLAN],
                    broker=None,
                    validate=validate,
                    max_rounds=6,
                    gate=self._gate(mission_id).wait,
                    budget=await self._budget(mission_id, "jarvis"),
                    on_usage=self._usage_sink(mission_id, None, run_id),
                )
        except AIPaused as exc:
            await self._store.finish_run(run_id, state="INTERRUPTED", error=exc.message)
            await self._mission_state(
                mission_id,
                MissionState.BLOCKED,
                f"Planning paused: {exc.message}",
                blocker={"kind": "ai_route", "message": exc.message},
            )
            return
        except asyncio.CancelledError:
            await self._store.finish_run(run_id, state="INTERRUPTED", error="stopped")
            raise
        except BudgetExceeded as exc:
            await self._store.finish_run(run_id, state="FAILED", error=str(exc))
            await self._mission_state(
                mission_id,
                MissionState.BLOCKED,
                f"Budget too small to plan ({exc}).",
                blocker={"kind": "budget", "message": f"Planning {exc}. Raise the mission budget."},
            )
            return
        except (ModelError, UltronError) as exc:
            message = exc.message
            await self._store.finish_run(run_id, state="FAILED", error=message)
            await self._mission_state(
                mission_id,
                MissionState.BLOCKED,
                f"Planning failed: {message}",
                blocker={"kind": "model", "message": message},
            )
            return
        if result.final is None:
            await self._store.finish_run(
                run_id, state="FAILED", error=result.error, tool_calls=result.tool_calls
            )
            await self._mission_state(
                mission_id,
                MissionState.BLOCKED,
                f"JARVIS couldn't produce a valid plan: {result.error}",
                blocker={"kind": "plan", "message": result.error or "no plan"},
            )
            return
        plan, notes = planning.validate(result.final)
        await self._store.finish_run(
            run_id,
            state="COMPLETE",
            tool_calls=result.tool_calls,
            summary=f"Planned {len(plan.tasks)} task(s)",
        )
        await self._accept_plan(mission, plan, notes)

    async def _accept_plan(
        self, mission: dict[str, Any], plan: planning.Plan, notes: list[str]
    ) -> None:
        mission_id = mission["id"]
        ws = self._workspace(mission)
        try:
            base = await ws.create(plan.title)
        except GitError as exc:
            await self._mission_state(
                mission_id,
                MissionState.FAILED,
                f"Couldn't create the workspace: {exc}",
                blocker={"kind": "workspace", "message": str(exc)},
            )
            return
        charter = plan.model_dump(exclude={"tasks"})
        charter["answers"] = (mission.get("charter") or {}).get("answers", [])
        await self._store.delete_tasks(mission_id)
        max_attempts = self.config()["max_attempts"]
        by_key = {t.key: t for t in plan.tasks}
        for key in planning.topological(plan.tasks):
            t = by_key[key]
            await self._store.add_task(
                mission_id,
                key=t.key,
                title=t.title,
                owner=t.owner,
                depends_on=t.depends_on,
                contract={
                    "objective": t.objective,
                    "deliverables": t.deliverables,
                    "done_when": t.done_when,
                    "allowed_paths": t.allowed_paths,
                    "checks": t.checks,
                    "depth": planning.depth(by_key, t.key),
                    "reviews": sorted(
                        k
                        for k in planning.ancestors(by_key, t.key)
                        if by_key[k].owner != "sentinel"
                    )
                    if t.owner == "sentinel"
                    else [],
                },
                max_attempts=max_attempts,
            )
        await self._artifact(
            mission_id,
            None,
            None,
            "jarvis",
            "charter",
            "charter.json",
            json.dumps(
                {**charter, "tasks": [t.model_dump() for t in plan.tasks], "runtime_notes": notes},
                indent=2,
            ),
        )
        await self._store.update_mission(
            mission_id,
            title=plan.title[:80],
            charter=charter,
            plan_notes=notes,
            revision=int(mission["revision"]) + 1,
            base_commit=base,
            head_commit=base,
            blocker=None,
        )
        if plan.blocking_unknowns:
            await self._mission_state(
                mission_id,
                MissionState.BLOCKED,
                "JARVIS needs answers before the team can start.",
                blocker={"kind": "questions", "questions": plan.blocking_unknowns},
            )
            return
        await self._mission_state(
            mission_id,
            MissionState.RUNNING,
            f"Plan accepted: {len(plan.tasks)} task(s)"
            + (f" — {'; '.join(notes)}" if notes else ""),
        )

    async def answer(self, mission_id: str, text_: str) -> dict[str, Any]:
        mission = await self._get(mission_id)
        blocker = mission.get("blocker") or {}
        if mission["state"] != MissionState.BLOCKED or blocker.get("kind") not in (
            "questions",
            "plan",
            "model",
        ):
            raise UltronError("CONFLICT", "This mission isn't waiting for an answer.", 409)
        charter = mission.get("charter") or {}
        charter["answers"] = [*charter.get("answers", []), text_.strip()[:2000]]
        await self._store.update_mission(mission_id, charter=charter, blocker=None)
        await self._mission_state(
            mission_id, MissionState.PLANNING, "JARVIS re-plans with your answer"
        )
        self._spawn(f"plan:{mission_id}", self._plan(mission_id))
        return await self.mission(mission_id)

    # Scheduling ----------------------------------------------------------------------

    async def _schedule_forever(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # the scheduler must survive anything one mission does
                log.exception("ULTRON scheduler tick failed")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=5)

    def _busy(self) -> int:
        return sum(1 for k in self._running if not k.startswith(("plan:", "finish:")))

    async def _tick(self) -> None:
        if self._stopping:
            return
        limit = self.config()["max_parallel_workers"]
        for mission in await self._store.missions_in(MissionState.RUNNING):
            mission_id = mission["id"]
            if not self._gate(mission_id).is_set():
                continue
            tasks = await self._store.tasks(mission_id)
            done = {t["key"] for t in tasks if t["state"] == TaskState.COMPLETE}
            for t in tasks:
                if t["state"] == TaskState.DRAFT and all(d in done for d in t["depends_on"]):
                    await self._task_state(t, TaskState.READY, f"{t['key']} is ready")
            if tasks and all(t["state"] == TaskState.COMPLETE for t in tasks):
                if mission_id not in self._finishing:
                    self._finishing.add(mission_id)
                    self._spawn(f"finish:{mission_id}", self._finish(mission_id))
                continue
            for t in tasks:
                if self._busy() >= limit:
                    return
                if t["state"] == TaskState.READY and t["id"] not in self._running:
                    self._spawn(t["id"], self._run_task(t["id"]))

    # Running a task ------------------------------------------------------------------

    async def _run_task(self, task_id: str) -> None:
        task = await self._store.task(task_id)
        if task is None or task["state"] != TaskState.READY:
            return
        mission = await self._get(task["mission_id"])
        resuming = await self._resumable(task_id)
        # A run that paused (AI route, restart) continues its attempt; it isn't a new one.
        counted = not (resuming and int(task["attempts"]))
        attempt = int(task["attempts"]) + (1 if counted else 0)
        await self._store.update_task(task_id, attempts=attempt, started_at=utcnow().isoformat())
        task["attempts"] = attempt
        await self._task_state(
            task,
            TaskState.RUNNING,
            f"{task['owner'].upper()} started {task['key']} (attempt {attempt})",
        )
        try:
            if task["owner"] == "sentinel":
                await self._review(mission, task)
            else:
                await self._produce(mission, task)
        except asyncio.CancelledError:
            current = await self._store.task(task_id)
            if current and current["state"] in (TaskState.RUNNING, TaskState.VERIFYING):
                if self._stopping:
                    await self._store.update_task(task_id, state=TaskState.READY)
                else:
                    await self._task_state(
                        current, TaskState.CANCELLED, f"{task['key']} was stopped mid-run"
                    )
            raise
        except BudgetExceeded as exc:
            await self._task_state(task, TaskState.READY, f"{task['key']} waits: budget")
            await self._block(
                mission["id"],
                "budget",
                f"The next step {exc}. Raise the mission budget to continue.",
            )
        except AIPaused as exc:
            # Checkpoint kept: the run continues where it stopped once a route is back. A pause
            # is not a failed attempt, so the attempt counter goes back.
            if counted:
                await self._store.update_task(task_id, attempts=attempt - 1)
                task["attempts"] = attempt - 1
            await self._task_state(task, TaskState.READY, f"{task['key']} waits: AI route")
            await self._block(mission["id"], "ai_route", f"AI paused — {exc.message}")
        except ModelError as exc:
            if exc.retryable:
                await self._failed_attempt(task, f"Model call failed ({exc.message}).")
            else:
                await self._task_state(task, TaskState.READY, f"{task['key']} waits: model")
                await self._block(mission["id"], "model", exc.message)
        except (GitError, UltronError, PolicyError, OSError) as exc:
            log.warning("task %s failed: %s", task_id, exc)
            await self._failed_attempt(task, f"Runtime error: {exc}")
        except Exception as exc:  # a bug must never mark work complete
            log.exception("task %s crashed", task_id)
            await self._failed_attempt(task, f"Internal error: {exc}")

    async def _block(self, mission_id: str, kind: str, message: str, **extra: Any) -> None:
        await self._mission_state(
            mission_id,
            MissionState.BLOCKED,
            message,
            blocker={"kind": kind, "message": message, **extra},
        )

    async def _failed_attempt(self, task: dict[str, Any], feedback: str) -> None:
        task = await self._store.task(task["id"]) or task
        if task["state"] not in (TaskState.RUNNING, TaskState.VERIFYING):
            return
        if int(task["attempts"]) < int(task["max_attempts"]):
            await self._task_state(
                task,
                TaskState.RETRYING,
                f"{task['key']} attempt {task['attempts']} failed — retrying",
                feedback=feedback[-6000:],
            )
            await self._task_state(
                task, TaskState.READY, f"{task['key']} queued for another attempt"
            )
            return
        await self._task_state(
            task,
            TaskState.BLOCKED,
            f"{task['key']} failed {task['attempts']} times",
            feedback=feedback[-6000:],
            finished_at=utcnow().isoformat(),
        )
        await self._block(
            task["mission_id"],
            "retries",
            f"{task['owner'].upper()} couldn't finish '{task['key']}' in "
            f"{task['attempts']} attempts. Last problem: {feedback[:400]}",
            task=task["key"],
        )

    def _scope(
        self,
        mission: dict[str, Any],
        task: dict[str, Any],
        root: Path,
        writable: list[str],
        tools: tuple[str, ...],
    ) -> Scope:
        ws = self._workspace(mission)
        return Scope(
            mission_id=mission["id"],
            task_id=task["id"],
            agent=task["owner"],
            root=root,
            scratch=ws.scratch(task["key"]),
            pythonpath=ws.pythonpath(root),
            writable=writable,
            timeout=self.config()["command_timeout_seconds"],
            tools=tools,
        )

    async def _agent(
        self,
        mission: dict[str, Any],
        task: dict[str, Any],
        system: str,
        opening: str,
        scope: Scope,
    ) -> tuple[Any, str]:
        agent = task["owner"]
        model = self._model(agent)
        if model is None:
            raise ModelError("not_configured", "No model available for ULTRON agents.")
        run_id = await self._store.add_run(
            mission["id"], task["id"], agent, int(task["attempts"]), model.model_id
        )
        await self._log(
            "agent.started",
            f"{agent.upper()} is working on {task['key']}",
            mission_id=mission["id"],
            task_id=task["id"],
            agent=agent,
            data={"run_id": run_id, "model": model.model_id},
        )
        broker = Broker(scope, self._auditor(mission["id"], task["id"], agent))

        def validate(name: str, raw: dict[str, Any]) -> str | None:
            if name == "submit_review" and raw.get("verdict") not in ("pass", "fail"):
                return "verdict must be 'pass' or 'fail'"
            if not str(raw.get("summary", "")).strip():
                return "summary is required"
            if not isinstance(raw.get("criteria"), list):
                return "criteria must be a list"
            return None

        checkpoint = self._checkpoints(f"ultron:{task['id']}") if self._checkpoints else None
        try:
            with ai_scope("ultron", mission_id=mission["id"], task_id=task["id"], agent=agent):
                result = await run_agent(
                    model=model,
                    system=system,
                    opening=opening,
                    tools=definitions(scope.tools),
                    broker=broker,
                    validate=validate,
                    max_rounds=self.config()["max_rounds_per_run"],
                    gate=self._gate(mission["id"]).wait,
                    budget=await self._budget(mission["id"], agent),
                    on_usage=self._usage_sink(mission["id"], task["id"], run_id),
                    checkpoint=checkpoint,
                )
        except (asyncio.CancelledError, AIPaused) as exc:
            # Paused or stopped: the checkpoint stays, the run continues later.
            note = "stopped" if isinstance(exc, asyncio.CancelledError) else exc.message
            await self._store.finish_run(run_id, state="INTERRUPTED", error=note)
            raise
        except Exception as exc:
            await self._store.finish_run(run_id, state="FAILED", error=str(exc)[:500])
            if checkpoint is not None:
                await checkpoint.clear()
            raise
        if checkpoint is not None:
            await checkpoint.clear()  # the run is over; another attempt starts fresh
        summary = str((result.final or {}).get("summary", ""))[:2000] or None
        await self._store.finish_run(
            run_id,
            state="COMPLETE" if result.final else "FAILED",
            tool_calls=result.tool_calls,
            summary=summary,
            error=result.error,
        )
        await self._log(
            "agent.finished",
            f"{agent.upper()} finished {task['key']}: "
            + (summary or result.error or "no result")[:160],
            mission_id=mission["id"],
            task_id=task["id"],
            agent=agent,
            data={
                "run_id": run_id,
                "tool_calls": result.tool_calls,
                "cost_usd": round(result.cost, 4),
            },
        )
        return result, run_id

    async def _run_checks(
        self,
        mission: dict[str, Any],
        task: dict[str, Any],
        root: Path,
        checks: list[list[str]],
        agent: str,
    ) -> list[dict[str, Any]]:
        """Checks run by the runtime itself — the evidence, independent of any agent's claim."""
        ws = self._workspace(mission)
        results = []
        for argv in checks:
            command = check_command(argv)
            outcome = await sandbox.run(
                command,
                cwd=root,
                scratch=ws.scratch(f"{task['key']}-checks"),
                pythonpath=ws.pythonpath(root),
                timeout=self.config()["command_timeout_seconds"],
            )
            results.append(
                {
                    "command": outcome.command,
                    "exit_code": outcome.exit_code,
                    "passed": outcome.passed,
                    "timed_out": outcome.timed_out,
                    "seconds": outcome.seconds,
                    "output": outcome.output[-8000:],
                }
            )
            await self._log(
                "check",
                f"Check {outcome.command} → " + ("passed" if outcome.passed else "FAILED"),
                severity="info" if outcome.passed else "warning",
                mission_id=mission["id"],
                task_id=task["id"],
                agent=agent,
                data={
                    "command": outcome.command,
                    "exit_code": outcome.exit_code,
                    "passed": outcome.passed,
                    "seconds": outcome.seconds,
                },
            )
        return results

    async def _produce(self, mission: dict[str, Any], task: dict[str, Any]) -> None:
        ws = self._workspace(mission)
        key, owner, contract = task["key"], task["owner"], task["contract"]
        tree = await ws.task_tree(key)
        if not await self._resumable(task["id"]):
            # A fresh attempt starts clean; a resumed run keeps the files its tools wrote.
            await ws.reset(key)
            if int(task["attempts"]) > 1:
                await ws.catch_up(key)
        start = (await text(tree, "rev-parse", "HEAD")).strip()
        base = (await text(tree, "merge-base", "HEAD", ws.integration)).strip()
        extra = []
        if task.get("feedback"):
            extra = ["", "Feedback from the previous attempt (fix the cause):", task["feedback"]]
        system = (prompts.AXIOM if owner == "axiom" else prompts.FORGE).format(
            name=BY_ID[owner].name
        )
        scope = self._scope(mission, task, tree, contract["allowed_paths"], PRODUCER_TOOLS[owner])
        result, run_id = await self._agent(
            mission, task, system, prompts.task_brief(mission, task, extra), scope
        )
        if result.final is None:
            await self._failed_attempt(
                task, f"{owner.upper()} stopped without a result: {result.error}"
            )
            return
        commit = await ws.commit(key, f"{owner.upper()} {key} (attempt {task['attempts']})")
        changed = (await text(tree, "rev-list", "--count", f"{base}..HEAD")).strip()
        if commit is None and changed == "0":
            await self._failed_attempt(
                task, f"{owner.upper()} submitted without changing any file."
            )
            return
        await self._task_state(task, TaskState.VERIFYING, f"Runtime is checking {key}")
        checks = await self._run_checks(mission, task, tree, contract.get("checks", []), owner)
        if checks:
            await self._artifact(
                mission["id"],
                task["id"],
                run_id,
                owner,
                "checks",
                f"{key}-checks-attempt{task['attempts']}.log",
                "\n\n".join(
                    f"$ {c['command']}\nexit {c['exit_code']}\n{c['output']}" for c in checks
                ),
            )
        failed = [c for c in checks if not c["passed"]]
        if failed:
            await self._failed_attempt(
                task,
                "These checks failed when the runtime ran them:\n"
                + "\n\n".join(
                    f"$ {c['command']} → exit {c['exit_code']}\n{c['output'][-2500:]}"
                    for c in failed
                ),
            )
            return
        head = (await text(tree, "rev-parse", "HEAD")).strip()
        patch = await ws.diff(start if int(task["attempts"]) > 1 else base, head, cwd=tree)
        patch_id = await self._artifact(
            mission["id"],
            task["id"],
            run_id,
            owner,
            "patch",
            f"{key}-attempt{task['attempts']}.patch",
            patch,
        )
        if owner == "axiom":
            files = (await text(tree, "diff", "--name-only", f"{base}..HEAD")).split()
            for name in files[:10]:
                path = tree / name
                if path.is_file():
                    await self._artifact(
                        mission["id"],
                        task["id"],
                        run_id,
                        owner,
                        "spec",
                        name,
                        path.read_text(encoding="utf-8", errors="replace"),
                    )
        integrated = await ws.merge(key, f"Merge {key} ({owner.upper()})")
        await self._store.update_mission(mission["id"], head_commit=integrated)
        final = result.final or {}
        await self._task_state(
            task,
            TaskState.COMPLETE,
            f"{key} complete — checks passed, merged",
            finished_at=utcnow().isoformat(),
            feedback=None,
            result={
                "summary": final.get("summary"),
                "criteria": final.get("criteria", []),
                "checks": [{k: v for k, v in c.items() if k != "output"} for c in checks],
                "commit": head,
                "integrated": integrated,
                "patch_artifact": patch_id,
                "commands_run": scope.commands,
            },
        )

    async def _review(self, mission: dict[str, Any], task: dict[str, Any]) -> None:
        ws = self._workspace(mission)
        key, contract = task["key"], task["contract"]
        tasks = {t["key"]: t for t in await self._store.tasks(mission["id"])}
        reviewed = [tasks[k] for k in contract.get("reviews", []) if k in tasks]
        tree = await ws.review_tree(key)
        await self._task_state(task, TaskState.VERIFYING, f"Runtime re-runs all checks for {key}")
        argvs: list[list[str]] = []
        for t in reviewed:
            argvs += [c for c in t["contract"].get("checks", []) if c not in argvs]
        for c in (mission.get("charter") or {}).get("success_criteria", []):
            if c.get("check") and c["check"] not in argvs:
                argvs.append(c["check"])
        checks = await self._run_checks(mission, task, tree, argvs, "sentinel")
        await self._artifact(
            mission["id"],
            task["id"],
            None,
            "sentinel",
            "checks",
            f"{key}-independent-checks-attempt{task['attempts']}.log",
            "\n\n".join(f"$ {c['command']}\nexit {c['exit_code']}\n{c['output']}" for c in checks)
            or "(no checks defined)",
        )
        evidence = [
            "",
            "Work under review (integrated in your checkout):",
            *[
                f"- {t['key']} by {t['owner'].upper()}: "
                + str((t.get("result") or {}).get("summary", ""))
                for t in reviewed
            ],
            "",
            "Checks the runtime just ran in your checkout:",
            *[
                f"- {c['command']}: "
                + ("passed" if c["passed"] else f"FAILED (exit {c['exit_code']})")
                + f"\n{c['output'][-1500:]}"
                for c in checks
            ],
        ]
        system = prompts.SENTINEL.format(name="SENTINEL")
        scope = self._scope(mission, task, tree, ["sentinel_checks/**"], SENTINEL_TOOLS)
        result, run_id = await self._agent(
            mission, task, system, prompts.task_brief(mission, task, evidence), scope
        )
        review = result.final or {}
        runtime_ok = all(c["passed"] for c in checks)
        verdict_ok = review.get("verdict") == "pass"
        report = {
            "verdict": review.get("verdict", "none"),
            "summary": review.get("summary", result.error),
            "findings": review.get("findings", []),
            "criteria": review.get("criteria", []),
            "runtime_checks": [{k: v for k, v in c.items() if k != "output"} for c in checks],
            "runtime_checks_passed": runtime_ok,
            "reviewed": [t["key"] for t in reviewed],
            "accepted": runtime_ok and verdict_ok,
        }
        report_id = await self._artifact(
            mission["id"],
            task["id"],
            run_id,
            "sentinel",
            "review",
            f"{key}-review-attempt{task['attempts']}.json",
            json.dumps(report, indent=2),
        )
        if result.final is None:
            await self._failed_attempt(task, f"SENTINEL stopped without a verdict: {result.error}")
            return
        if report["accepted"]:
            await self._task_state(
                task,
                TaskState.COMPLETE,
                f"SENTINEL accepted {', '.join(report['reviewed'])}",
                finished_at=utcnow().isoformat(),
                result={**report, "report_artifact": report_id},
            )
            return
        problems = (
            "\n".join(
                f"[{f.get('severity')}] {f.get('issue')} — {f.get('evidence')}"
                for f in report["findings"]
            )
            or "(no findings given)"
        )
        failing = "\n".join(
            f"$ {c['command']} → exit {c['exit_code']}\n{c['output'][-1500:]}"
            for c in checks
            if not c["passed"]
        )
        feedback = f"SENTINEL rejected the work.\nFindings:\n{problems}"
        if failing:
            feedback += f"\nChecks that failed in the independent run:\n{failing}"
        reopen = [
            t
            for t in reviewed
            if t["owner"] == "forge" and int(t["attempts"]) < int(t["max_attempts"])
        ]
        if reopen and int(task["attempts"]) < int(task["max_attempts"]):
            for t in reopen:
                await self._task_state(
                    t,
                    TaskState.RETRYING,
                    f"SENTINEL sent {t['key']} back to FORGE",
                    feedback=feedback[-6000:],
                )
                await self._task_state(t, TaskState.READY, f"{t['key']} queued for a fix")
            await self._task_state(
                task,
                TaskState.RETRYING,
                f"{key} will review the fix",
                result={**report, "report_artifact": report_id},
            )
            await self._task_state(task, TaskState.DRAFT, f"{key} waits for the fix")
            return
        await self._task_state(
            task,
            TaskState.BLOCKED,
            f"SENTINEL rejected {key}; no attempts left",
            result={**report, "report_artifact": report_id},
            finished_at=utcnow().isoformat(),
        )
        await self._block(
            mission["id"],
            "review_failed",
            f"SENTINEL rejected the work and the retry budget is used up. {problems[:400]}",
            task=key,
        )

    # Finishing -----------------------------------------------------------------------

    async def _finish(self, mission_id: str) -> None:
        try:
            mission = await self._get(mission_id)
            if mission["state"] != MissionState.RUNNING:
                return
            ws = self._workspace(mission)
            charter = mission.get("charter") or {}
            final_task = {"id": f"{mission_id}:final", "key": "final", "mission_id": mission_id}
            tree = await ws.review_tree("final")
            checks: list[dict[str, Any]] = []
            by_criterion: dict[int, dict[str, Any]] = {}
            for i, c in enumerate(charter.get("success_criteria", [])):
                if c.get("check"):
                    outcome = (
                        await self._run_checks(mission, final_task, tree, [c["check"]], "jarvis")
                    )[0]
                    by_criterion[i] = outcome
                    checks.append(outcome)
            tasks = await self._store.tasks(mission_id)
            reviews = [t for t in tasks if t["owner"] == "sentinel"]
            reviewed_criteria = {
                c.get("criterion"): c
                for t in reviews
                for c in (t.get("result") or {}).get("criteria", [])
            }
            criteria = []
            for i, c in enumerate(charter.get("success_criteria", [])):
                check = by_criterion.get(i)
                if c.get("check"):
                    status = "verified" if check and check["passed"] else "failed"
                    evidence = (
                        f"{check['command']} exit {check['exit_code']}" if check else "not run"
                    )
                elif (r := reviewed_criteria.get(c["criterion"])) is not None:
                    status = "reviewed" if r.get("met") else "failed"
                    evidence = f"SENTINEL: {r.get('evidence', '')}"
                else:
                    status = "reviewed" if reviews else "not_checked"
                    evidence = "covered by SENTINEL's accepted review" if reviews else "—"
                criteria.append(
                    {"criterion": c["criterion"], "status": status, "evidence": evidence}
                )
            files = await ws.files()
            mission = await self._get(mission_id)
            report = {
                "criteria": criteria,
                "final_checks": [{k: v for k, v in c.items() if k != "output"} for c in checks],
                "tasks": [
                    {
                        "key": t["key"],
                        "owner": t["owner"],
                        "attempts": t["attempts"],
                        "summary": (t.get("result") or {}).get("summary"),
                    }
                    for t in tasks
                ],
                "files": files,
                "branch": ws.integration,
                "head_commit": mission["head_commit"],
                "spent_usd": round(float(mission["spent_usd"]), 4),
                "isolation": sandbox.detect().kind,
                "model": mission["model_label"],
            }
            await self._artifact(
                mission_id,
                None,
                None,
                "jarvis",
                "report",
                "report.json",
                json.dumps(report, indent=2),
            )
            if any(c["status"] == "failed" for c in criteria):
                await self._store.update_mission(mission_id, report=report)
                await self._block(
                    mission_id,
                    "acceptance",
                    "Final acceptance failed: "
                    + "; ".join(c["criterion"] for c in criteria if c["status"] == "failed"),
                )
                return
            if mission["project_kind"] == "sandbox":
                destination = self._export_dir() / f"{_slug(mission['title'])}-{mission_id}"
                await self._store.update_mission(mission_id, report=report)
                await self.request_approval(
                    mission_id,
                    None,
                    "export.project",
                    f"Export the verified project to {destination}",
                    {
                        "kind": "export.project",
                        "mission_id": mission_id,
                        "commit": mission["head_commit"],
                        "destination": str(destination),
                        "files": len(files),
                    },
                    requested_by="jarvis",
                )
                await self._mission_state(
                    mission_id,
                    MissionState.WAITING_APPROVAL,
                    "Verified. Export awaits your approval.",
                )
            else:
                report["delivery"] = (
                    f"Reviewed branch {ws.integration} in your repository. Merging into your "
                    "running checkout is locked in this release."
                )
                await self._mission_state(
                    mission_id,
                    MissionState.COMPLETE,
                    f"Complete: branch {ws.integration} reviewed",
                    report=report,
                )
        finally:
            self._finishing.discard(mission_id)

    # Approvals -----------------------------------------------------------------------

    async def request_approval(
        self,
        mission_id: str,
        task_id: str | None,
        category: str,
        title: str,
        effect: dict[str, Any],
        *,
        requested_by: str,
    ) -> str:
        if CATEGORIES.get(category, ("locked",))[0] != "approval":
            raise PolicyError(category, f"{category} is locked; it can't even be requested.")
        approval_id = f"ap-{new_id()[:10]}"
        await self._store.add_approval(
            {
                "id": approval_id,
                "mission_id": mission_id,
                "task_id": task_id,
                "category": category,
                "title": title,
                "effect": effect,
                "signature": effect_signature(effect),
                "requested_by": requested_by,
            }
        )
        await self._log(
            "approval.requested",
            f"Approval needed: {title}",
            severity="important",
            mission_id=mission_id,
            task_id=task_id,
            agent=requested_by,
            data={"approval_id": approval_id, "category": category},
            event_type=EventType.ULTRON_APPROVAL_CHANGED,
        )
        return approval_id

    async def decide(self, approval_id: str, approve: bool) -> dict[str, Any]:
        if not re.match(r"^ap-[0-9a-f]{10}$", approval_id):
            raise UltronError("NOT_FOUND", "No such approval.", 404)
        approval = await self._store.approval(approval_id)
        if approval is None:
            raise UltronError("NOT_FOUND", "No such approval.", 404)
        if approval["state"] != ApprovalState.PENDING:
            raise UltronError("CONFLICT", f"Already {approval['state']}.", 409)
        if effect_signature(approval["effect"]) != approval["signature"]:
            raise UltronError("TAMPERED", "The approval's effect changed after it was requested.")
        state = ApprovalState.APPROVED if approve else ApprovalState.REJECTED
        if not await self._store.decide_approval(approval_id, state):
            raise UltronError("CONFLICT", "Someone decided this approval already.", 409)
        mission_id = approval["mission_id"]
        await self._log(
            "approval.decided",
            f"You {'approved' if approve else 'declined'}: {approval['title']}",
            mission_id=mission_id,
            data={"approval_id": approval_id, "state": state},
            event_type=EventType.ULTRON_APPROVAL_CHANGED,
        )
        if not approve:
            mission = await self._get(mission_id)
            if mission["state"] == MissionState.WAITING_APPROVAL:
                report = {
                    **(mission.get("report") or {}),
                    "delivery": "Export declined; the project stays in the ULTRON workspace.",
                }
                await self._mission_state(
                    mission_id, MissionState.COMPLETE, "Complete (export declined)", report=report
                )
            return (await self._store.approval(approval_id)) or approval
        await self._execute(approval)
        return (await self._store.approval(approval_id)) or approval

    async def _execute(self, approval: dict[str, Any]) -> None:
        effect = approval["effect"]
        mission = await self._get(approval["mission_id"])
        if effect.get("kind") != "export.project":
            await self._store.finish_approval(
                approval["id"], ApprovalState.FAILED, "unknown effect"
            )
            return
        ws = self._workspace(mission)
        head = await ws.head(ws.integration)
        if head != effect["commit"]:  # the approved effect must be exactly what runs
            await self._store.finish_approval(
                approval["id"],
                ApprovalState.FAILED,
                "The project changed after approval was requested.",
            )
            return
        try:
            count = await ws.export(Path(effect["destination"]))
        except (GitError, OSError) as exc:
            await self._store.finish_approval(approval["id"], ApprovalState.FAILED, str(exc))
            await self._log(
                "approval.failed",
                f"Export failed: {exc}",
                severity="warning",
                mission_id=mission["id"],
                event_type=EventType.ULTRON_APPROVAL_CHANGED,
            )
            return
        result = f"Exported {count} file(s) to {effect['destination']}"
        await self._store.finish_approval(approval["id"], ApprovalState.EXECUTED, result)
        report = {
            **(mission.get("report") or {}),
            "delivery": result,
            "exported_to": effect["destination"],
        }
        await self._mission_state(
            mission["id"], MissionState.COMPLETE, f"Complete. {result}", report=report
        )

    # Controls ------------------------------------------------------------------------

    async def pause(self, mission_id: str) -> dict[str, Any]:
        mission = await self._get(mission_id)
        if mission["state"] not in (MissionState.RUNNING, MissionState.PLANNING):
            raise UltronError("CONFLICT", f"A {mission['state']} mission can't be paused.", 409)
        self._gate(mission_id).clear()
        await self._mission_state(
            mission_id, MissionState.PAUSED, "Paused — running agents stop before their next step"
        )
        return await self.mission(mission_id)

    async def resume(self, mission_id: str) -> dict[str, Any]:
        mission = await self._get(mission_id)
        blocker = mission.get("blocker") or {}
        if mission["state"] == MissionState.BLOCKED and blocker.get("kind") in (
            "budget",
            "model",
            "ai_route",
            "retries",
            "review_failed",
        ):
            fresh = blocker.get("kind") in ("retries", "review_failed")
            for t in await self._store.tasks(mission_id):
                if fresh:  # the user grants another round of attempts
                    t["max_attempts"] = int(t["attempts"]) + self.config()["max_attempts"]
                    await self._store.update_task(t["id"], max_attempts=t["max_attempts"])
                if t["state"] == TaskState.BLOCKED:
                    await self._task_state(t, TaskState.READY, f"{t['key']} resumes")
        elif mission["state"] != MissionState.PAUSED:
            raise UltronError("CONFLICT", f"A {mission['state']} mission can't be resumed.", 409)
        tasks = await self._store.tasks(mission_id)
        state = MissionState.RUNNING if tasks else MissionState.PLANNING
        self._gate(mission_id).set()
        await self._mission_state(mission_id, state, "Resumed", blocker=None)
        if not tasks and f"plan:{mission_id}" not in self._running:
            self._spawn(f"plan:{mission_id}", self._plan(mission_id))
        return await self.mission(mission_id)

    async def on_ai_available(self) -> None:
        """An AI route is back: missions paused for it continue from their checkpoints."""
        for mission in await self._store.missions_in(MissionState.BLOCKED):
            if (mission.get("blocker") or {}).get("kind") == "ai_route":
                with contextlib.suppress(UltronError):
                    await self.resume(mission["id"])

    async def _resumable(self, task_id: str) -> bool:
        if self._checkpoints is None:
            return False
        return await self._checkpoints(f"ultron:{task_id}").exists()

    async def set_budget(self, mission_id: str, usd: float) -> dict[str, Any]:
        mission = await self._get(mission_id)
        if not float(mission["spent_usd"]) < usd <= 500:
            raise UltronError("INVALID", "The new budget must exceed what's spent (max $500).")
        await self._store.update_mission(mission_id, budget_usd=usd)
        await self._log("mission.budget", f"Budget set to ${usd:.2f}", mission_id=mission_id)
        if (
            mission["state"] == MissionState.BLOCKED
            and (mission.get("blocker") or {}).get("kind") == "budget"
        ):
            return await self.resume(mission_id)
        return await self.mission(mission_id)

    async def cancel(self, mission_id: str) -> dict[str, Any]:
        mission = await self._get(mission_id)
        if mission["state"] in MISSION_DONE:
            raise UltronError("CONFLICT", f"The mission is already {mission['state']}.", 409)
        interrupted = [
            t["key"]
            for t in await self._store.tasks(mission_id)
            if t["state"] in (TaskState.RUNNING, TaskState.VERIFYING)
        ]
        await self._mission_state(mission_id, MissionState.CANCELLED, "Stopping the mission…")
        running = [
            t
            for k, t in self._running.items()
            if k.startswith(mission_id) or k.endswith(mission_id)
        ]
        for job in running:
            job.cancel()
        for job in running:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job
        for t in await self._store.tasks(mission_id):
            if t["state"] not in (TaskState.COMPLETE, TaskState.CANCELLED):
                with contextlib.suppress(Exception):
                    await self._task_state(t, TaskState.CANCELLED, f"{t['key']} cancelled")
        for a in await self._store.approvals(mission_id, ApprovalState.PENDING):
            await self._store.decide_approval(a["id"], ApprovalState.REJECTED, "mission cancelled")
        self._gate(mission_id).set()
        note = (
            f"Cancelled. Interrupted mid-run: {', '.join(interrupted)} — their uncommitted "
            "changes stay in their worktrees; nothing left the workspace."
            if interrupted
            else "Cancelled. Nothing was running."
        )
        await self._mission_state(
            mission_id, MissionState.CANCELLED, note, blocker={"kind": "cancelled", "message": note}
        )
        return await self.mission(mission_id)

    async def pause_all(self) -> int:
        count = 0
        for m in await self._store.missions_in(MissionState.RUNNING, MissionState.PLANNING):
            await self.pause(m["id"])
            count += 1
        return count

    async def stop_all(self) -> int:
        count = 0
        for m in await self._store.missions_in(
            MissionState.RUNNING,
            MissionState.PLANNING,
            MissionState.PAUSED,
            MissionState.BLOCKED,
            MissionState.WAITING_APPROVAL,
        ):
            await self.cancel(m["id"])
            count += 1
        await self._log(
            "runtime.stop_all", f"Emergency stop: {count} mission(s) cancelled", severity="warning"
        )
        return count

    # Artifacts -----------------------------------------------------------------------

    async def _artifact(
        self,
        mission_id: str,
        task_id: str | None,
        run_id: str | None,
        agent: str,
        kind: str,
        name: str,
        content: str,
    ) -> str:
        import hashlib

        artifact_id = f"a-{new_id()[:12]}"
        folder = self._root / "artifacts" / mission_id
        folder.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[-80:]
        path = folder / f"{artifact_id}-{safe}"
        path.write_bytes(data)
        await self._store.add_artifact(
            {
                "id": artifact_id,
                "mission_id": mission_id,
                "task_id": task_id,
                "run_id": run_id,
                "agent": agent,
                "kind": kind,
                "name": name,
                "path": str(path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
        await self._log(
            "artifact.created",
            f"{agent.upper()} produced {kind}: {name}",
            mission_id=mission_id,
            task_id=task_id,
            agent=agent,
            severity="debug",
            data={"artifact_id": artifact_id, "kind": kind, "bytes": len(data)},
        )
        return artifact_id

    async def artifact(self, artifact_id: str) -> dict[str, Any]:
        if not re.match(r"^a-[0-9a-f]{12}$", artifact_id):
            raise UltronError("NOT_FOUND", "No such artifact.", 404)
        record = await self._store.artifact(artifact_id)
        if record is None:
            raise UltronError("NOT_FOUND", "No such artifact.", 404)
        content = await asyncio.to_thread(_read_text, Path(record["path"]))
        import hashlib

        intact = hashlib.sha256(content.encode()).hexdigest() == record["sha256"]
        return {
            **{k: v for k, v in record.items() if k != "path"},
            "content": content[:400_000],
            "intact": intact,
        }

    # Views ---------------------------------------------------------------------------

    async def mission(self, mission_id: str) -> dict[str, Any]:
        mission = await self._get(mission_id)
        tasks = await self._store.tasks(mission_id)
        return {
            **{k: v for k, v in mission.items() if k != "workspace"},
            "workspace": mission["workspace"],
            "paused": not self._gate(mission_id).is_set(),
            "tasks": [{**t, "running": t["id"] in self._running} for t in tasks],
            "runs": await self._store.runs(mission_id),
            "artifacts": await self._store.artifacts(mission_id),
            "approvals": await self._store.approvals(mission_id),
            "progress": {
                "complete": sum(1 for t in tasks if t["state"] == TaskState.COMPLETE),
                "total": len(tasks),
            },
        }

    async def missions(self) -> list[dict[str, Any]]:
        out = []
        for m in await self._store.missions():
            tasks = await self._store.tasks(m["id"])
            out.append(
                {
                    "id": m["id"],
                    "number": m["number"],
                    "title": m["title"],
                    "state": m["state"],
                    "project_kind": m["project_kind"],
                    "spent_usd": m["spent_usd"],
                    "budget_usd": m["budget_usd"],
                    "created_at": m["created_at"],
                    "updated_at": m["updated_at"],
                    "blocker": m["blocker"],
                    "model_label": m["model_label"],
                    "progress": {
                        "complete": sum(1 for t in tasks if t["state"] == TaskState.COMPLETE),
                        "total": len(tasks),
                    },
                    "owners": sorted({t["owner"] for t in tasks}),
                }
            )
        return out

    def set_research_tasks(self, provider: Callable[[], Any] | None) -> None:
        """QuantLab research missions report their running agent tasks here (real work only)."""
        self._research_tasks = provider

    async def agents(self) -> list[dict[str, Any]]:
        running = await self._store.tasks_in("RUNNING", "VERIFYING")
        provider = getattr(self, "_research_tasks", None)
        research: list[dict[str, Any]] = await provider() if provider is not None else []
        counts = await self._store.run_counts()
        spend = await self._store.spend_by_agent()
        planning_now = [k.split(":", 1)[1] for k in self._running if k.startswith("plan:")]
        out = []
        for profile in ROSTER:
            current = [
                {
                    "task_id": t["id"],
                    "mission_id": t["mission_id"],
                    "key": t["key"],
                    "title": t["title"],
                    "state": t["state"],
                }
                for t in running
                if t["owner"] == profile.id
            ]
            if profile.id == "jarvis":
                current = [
                    {
                        "task_id": None,
                        "mission_id": m,
                        "key": "plan",
                        "title": "Planning",
                        "state": "RUNNING",
                    }
                    for m in planning_now
                ]
            quant = [
                {
                    "task_id": t["id"],
                    "mission_id": t["mission_id"],
                    "key": t["kind"],
                    "title": f"QuantLab · {t['objective']}",
                    "state": t["state"],
                }
                for t in research
                if t["agent"] == profile.id
            ]
            current += quant
            model = self._settings.agents.get(profile.id)
            out.append(
                {
                    "id": profile.id,
                    "name": profile.name,
                    "role": profile.role,
                    "deliverables": profile.deliverables,
                    "tools": list(profile.tools),
                    "writes": profile.writes,
                    "must_not": profile.must_not,
                    "active": profile.active,
                    "release": profile.release,
                    "status": ("working" if current else "idle")
                    if profile.active
                    else ("working" if quant else "not_active"),
                    "current": current,
                    "runs": counts.get(profile.id, {}),
                    "spent_usd": round(spend.get(profile.id, 0.0), 4),
                    "model": model.model
                    if (model and not self._settings.scripted_model)
                    else (
                        "scripted-test"
                        if self._settings.scripted_model and profile.active
                        else None
                    ),
                }
            )
        return out

    async def activity(
        self, mission_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if mission_id:
            self._checked(mission_id)
        return await self._store.events(mission_id, limit=min(max(limit, 1), 1000))

    async def approvals(self, state: str | None = None) -> list[dict[str, Any]]:
        return await self._store.approvals(state=state)

    async def overview(self) -> dict[str, Any]:
        missions = await self.missions()
        midnight = datetime.combine(utcnow().date(), time.min, tzinfo=utcnow().tzinfo)
        active_states = {
            MissionState.RUNNING,
            MissionState.PLANNING,
            MissionState.PAUSED,
            MissionState.BLOCKED,
            MissionState.WAITING_APPROVAL,
        }
        iso = sandbox.detect()
        return {
            "enabled": self._settings.enabled,
            "model_ready": self._model("jarvis") is not None,
            "model_label": self._model_label(),
            "scripted": bool(self._settings.scripted_model),
            "isolation": {
                "kind": iso.kind,
                "network_blocked": iso.network_blocked,
                "writes_confined": iso.writes_confined,
                "detail": iso.detail,
            },
            "workers": {"busy": self._busy(), "limit": self.config()["max_parallel_workers"]},
            "spend": {
                "today_usd": round(await self._store.spend_since(midnight.isoformat()), 4),
                "week_usd": round(
                    await self._store.spend_since((midnight - timedelta(days=6)).isoformat()), 4
                ),
            },
            "counts": {
                "active": sum(1 for m in missions if m["state"] in active_states),
                "complete": sum(1 for m in missions if m["state"] == MissionState.COMPLETE),
                "blocked": sum(1 for m in missions if m["state"] == MissionState.BLOCKED),
                "total": len(missions),
            },
            "pending_approvals": await self._store.approvals(state=ApprovalState.PENDING),
            "missions": missions[:12],
            "agents": await self.agents(),
            "config": self.config(),
            "policy": [
                {"category": k, "decision": v[0], "description": v[1]}
                for k, v in CATEGORIES.items()
            ],
            "repo_available": self._repo_root is not None and (self._repo_root / ".git").exists(),
        }

    async def knowledge(self) -> dict[str, Any]:
        specs = await self._store.artifacts_of_kind("spec")
        reports = await self._store.artifacts_of_kind("report")
        reviews = await self._store.artifacts_of_kind("review")
        return {"specs": specs, "reports": reports, "reviews": reviews}

    async def status_text(self) -> str:
        """For the brain: missions, blockers and approvals in a few lines."""
        missions = await self.missions()
        if not missions:
            return "No ULTRON mission yet."
        lines = []
        for m in missions[:6]:
            p = m["progress"]
            line = (
                f"{m['id']} '{m['title']}': {m['state']}, {p['complete']}/{p['total']} tasks, "
                f"${m['spent_usd']:.2f} of ${m['budget_usd']:.2f}"
            )
            if m["blocker"]:
                b = m["blocker"]
                why = b.get("message") or "; ".join(b.get("questions", []))
                line += f" — blocked ({b.get('kind')}): {why}"
            lines.append(line)
        pending = await self._store.approvals(state=ApprovalState.PENDING)
        if pending:
            lines.append(
                "Waiting for the user's approval: " + "; ".join(a["title"] for a in pending)
            )
        return "\n".join(lines)
