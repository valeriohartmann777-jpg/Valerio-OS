"""Research missions: one idea from source to evidence verdict, worked by JARVIS and its agents.

An ``idea`` mission runs fixed stages; each stage is one typed task owned by one agent
(``mission_id, task_id, agent, objective, inputs_hash, allowed_tools, budget, artifacts,
actual_actions, results, evidence_refs, limitations, status``):

    register  ARCHIVE   source fingerprint and evidence coverage          (code)
    claims    ATLAS     claims with segment provenance                     (Claude)
    blueprint JARVIS    formal rule + provenance + ambiguities             (Claude, checked)
    boundary  SENTINEL  extraction/ambiguity boundaries → READY gate       (code)
    freeze    ARCHIVE   immutable strategy version, spec hash              (code)
    protocol  CIPHER    pre-registered test protocol, frozen before data   (Claude, floors)
    data      VECTOR    library reuse → quote → owner approval → dataset   (code)
    baseline  CIPHER    deterministic validation run, holdout sealed       (engine)
    audit     SENTINEL  second engine + ledger checks, sealed report       (code)
    verdict   JARVIS    research verdict (fixed function) + checked note   (code + Claude)

Nothing here can buy data on its own: a purchase needs the owner's explicit approval
of a fresh quote, and the hub enforces caps and single use. Waiting states are real:
``WAITING_USER`` (a grouped question), ``WAITING_APPROVAL`` (a data quote), ``BLOCKED``
(no model, budget spent, a failure to look at). Restarts resume at the stage; every
stage is idempotent (a job or quote that exists is never requested again).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from jarvis.ai.types import AIPaused, ai_scope
from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType, Severity
from jarvis.llm.base import ChatModel
from jarvis.quantlab.futures.engine import ENGINE_VERSION
from jarvis.quantlab.futures.service import ResearchService
from jarvis.quantlab.futures.spec import parse
from jarvis.quantlab.hub.service import DataHubService, HubError
from jarvis.quantlab.ideas import agents, catalog, sentinel
from jarvis.quantlab.ideas import blueprint as bp
from jarvis.quantlab.ideas import claims as cl
from jarvis.quantlab.ideas.store import MissionStore
from jarvis.quantlab.intake.detect import IntakeError
from jarvis.quantlab.intake.service import SourceService
from jarvis.quantlab.spec import canonical_json
from jarvis.settings import ModelPrice
from jarvis.ultron.runner import Budget, BudgetExceeded
from jarvis.util import new_id

log = logging.getLogger("jarvis.quantlab.missions")

IDEA_STAGES = (
    "register",
    "claims",
    "blueprint",
    "boundary",
    "freeze",
    "protocol",
    "data",
    "baseline",
    "audit",
    "verdict",
)
OPEN = ("RUNNING", "WAITING_USER", "WAITING_APPROVAL", "PAUSED", "BLOCKED")
DONE = ("COMPLETE", "FAILED", "CANCELED")
DEFAULT_BUDGET: dict[str, Any] = {
    "max_model_usd": 2.0,
    "max_paid_data_usd": 15.0,
    "max_variants": 6,
    "max_parameter_combinations": 150,
    "max_compute_minutes": 30,
    "max_concurrent_workers": 1,
    "max_iterations": 2,
    "max_retries": 1,
}
BUDGET_LIMITS = {
    "max_model_usd": (0.0, 50.0),
    "max_paid_data_usd": (0.0, 500.0),
    "max_variants": (0, 24),
    "max_parameter_combinations": (0, 2000),
    "max_compute_minutes": (1, 600),
    "max_concurrent_workers": (1, 2),
    "max_iterations": (0, 6),
    "max_retries": (0, 3),
}
VERDICTS = (
    "SOURCE_UNAVAILABLE",
    "RULES_UNCLEAR",
    "DATA_INSUFFICIENT",
    "SIMULATION_INVALID",
    "INSUFFICIENT_EVIDENCE",
    "REJECTED_UNDER_TESTED_ASSUMPTIONS",
    "PROMISING_RESEARCH_CANDIDATE",
    "ROBUST_UNDER_TESTED_ASSUMPTIONS",
    "FORWARD_VALIDATION_REQUIRED",
)
TEST_IDS = (
    "DATA_INTEGRITY",
    "LEAKAGE",
    "INDEPENDENT_REFERENCE",
    "BASELINE",
    "OUT_OF_SAMPLE",
    "WALK_FORWARD",
    "COST_STRESS",
    "PARAMETER_SENSITIVITY",
    "BOOTSTRAP",
    "SELECTION_BIAS",
    "REGIMES",
    "SUBPERIODS",
    "TAIL_DEPENDENCE",
    "AMBIGUITY",
    "HOLDOUT",
)

ROSTER: tuple[dict[str, Any], ...] = (
    {
        "id": "jarvis",
        "name": "JARVIS",
        "role": "Mission lead · formal rules · closing note",
        "engine": "Claude (tool calls checked in code)",
        "active": True,
    },
    {
        "id": "atlas",
        "name": "ATLAS",
        "role": "Source claims · definitions · hypotheses",
        "engine": "Claude (tool calls checked in code)",
        "active": True,
    },
    {
        "id": "cipher",
        "name": "CIPHER",
        "role": "Test protocol · variants · statistics",
        "engine": "Claude for design; the deterministic engine for every number",
        "active": True,
    },
    {
        "id": "sentinel",
        "name": "SENTINEL",
        "role": "Independent audit · second engine · veto",
        "engine": "deterministic code (no model)",
        "active": True,
    },
    {
        "id": "vector",
        "name": "VECTOR",
        "role": "Market data · quotes · datasets",
        "engine": "deterministic code (no model); purchases need your approval",
        "active": True,
    },
    {
        "id": "archive",
        "name": "ARCHIVE",
        "role": "Source lineage · frozen versions · trial ledger",
        "engine": "deterministic code (no model)",
        "active": True,
    },
    {
        "id": "forge",
        "name": "FORGE",
        "role": "New rule operators (code changes)",
        "engine": "not used by research missions yet (ULTRON software missions only)",
        "active": False,
    },
    {
        "id": "prism",
        "name": "PRISM",
        "role": "Interface work",
        "engine": "not used yet",
        "active": False,
    },
    {
        "id": "axiom",
        "name": "AXIOM",
        "role": "Architecture",
        "engine": "not used by research missions",
        "active": False,
    },
    {
        "id": "operator",
        "name": "OPERATOR",
        "role": "Desktop automation",
        "engine": "not used by research missions",
        "active": False,
    },
)

ModelFactory = Callable[[str], ChatModel | None]


class MissionError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class Blocked(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def check_budget(raw: dict[str, Any] | None) -> dict[str, Any]:
    budget = dict(DEFAULT_BUDGET)
    for key, value in (raw or {}).items():
        if key not in BUDGET_LIMITS:
            raise MissionError("BUDGET_INVALID", f"Unknown budget field {key}.")
        low, high = BUDGET_LIMITS[key]
        if not isinstance(value, int | float) or not low <= value <= high:
            raise MissionError("BUDGET_INVALID", f"{key} must be between {low} and {high}.")
        budget[key] = value
    return budget


class Task:
    """One agent task: its contract, its actions as they happen, its outcome."""

    def __init__(self, svc: MissionService, mission: dict[str, Any], row: dict[str, Any]) -> None:
        self.svc = svc
        self.mission = mission
        self.id = row["id"]
        self.agent = row["agent"]
        self.actions: list[dict[str, Any]] = []

    async def act(self, text: str, **data: Any) -> None:
        self.actions.append({"at": sentinel_now(), "text": text, **data})
        await self.svc.store.update_task(self.id, actions=self.actions)
        await self.svc.store.event(
            self.mission["id"], "action", text, task_id=self.id, agent=self.agent, data=data or None
        )
        await self.svc.emit(self.mission["id"], f"{self.agent.upper()}: {text}")

    async def done(
        self,
        results: dict[str, Any],
        *,
        artifacts: list[dict[str, Any]] | None = None,
        evidence: list[str] | None = None,
        limitations: list[str] | None = None,
        state: str = "COMPLETE",
    ) -> None:
        await self.svc.store.update_task(
            self.id,
            state=state,
            results=results,
            artifacts=artifacts or [],
            evidence_refs=evidence or [],
            limitations=limitations or [],
            finished_at=sentinel_now(),
        )
        await self.svc.store.event(
            self.mission["id"],
            "task",
            f"{self.agent} {state.lower()}",
            task_id=self.id,
            agent=self.agent,
        )

    async def fail(self, error: str, state: str = "FAILED") -> None:
        await self.svc.store.update_task(
            self.id, state=state, error=error[:1000], finished_at=sentinel_now()
        )
        await self.svc.store.event(
            self.mission["id"],
            "task",
            f"{self.agent}: {error[:200]}",
            task_id=self.id,
            agent=self.agent,
        )


def sentinel_now() -> str:
    from jarvis.util import utcnow

    return utcnow().isoformat()


class MissionService:
    def __init__(
        self,
        *,
        db: Any,
        bus: EventBus,
        sources: SourceService,
        research: ResearchService,
        hub: DataHubService,
        model: ModelFactory,
        label: Callable[[], str],
        prices: dict[str, ModelPrice],
        root: Path,
        scripted: Callable[[], bool] = lambda: False,
    ) -> None:
        self.store = MissionStore(db)
        self._bus = bus
        self.sources = sources
        self.research = research
        self.hub = hub
        self._model = model
        self._label = label
        self._prices = prices
        self._scripted = scripted
        self.root = root
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._gates: dict[str, asyncio.Event] = {}
        self._slots = asyncio.Semaphore(2)
        self._stopping = False

    # lifecycle ------------------------------------------------------------------------------

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._stopping = False
        self._unsubscribe = self._bus.subscribe(EventType.QUANTLAB_HUB_CHANGED, self._on_hub)
        for m in await self.store.in_state("RUNNING"):
            for task in await self.store.tasks(m["id"]):
                if task["state"] == "RUNNING":
                    await self.store.update_task(
                        task["id"], state="INTERRUPTED", finished_at=sentinel_now()
                    )
            await self.store.event(m["id"], "resume", "Resumed after a restart")
            self._spawn(m["id"])

    async def _on_hub(self, _event: Event) -> None:
        """A mission waiting for a data connection continues once the hub is connected."""
        if self._stopping:
            return
        status = await self.hub.status()
        if status["status"] != "CONNECTED":
            return
        for m in await self.store.in_state("WAITING_USER"):
            if (m.get("waiting") or {}).get("kind") == "connect_data":
                if await self.store.transition(m["id"], ("WAITING_USER",), "RUNNING", waiting=None):
                    await self.store.event(
                        m["id"], "resume", "Data Hub connected — continuing", agent="vector"
                    )
                    self._spawn(m["id"])

    async def stop(self) -> None:
        self._stopping = True
        unsubscribe = getattr(self, "_unsubscribe", None)
        if unsubscribe is not None:
            unsubscribe()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    async def wait_idle(self, timeout: float = 120.0) -> None:  # noqa: ASYNC109
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if not any(not t.done() for t in self._tasks.values()):
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("missions still running")

    def _spawn(self, mission_id: str) -> None:
        current = self._tasks.get(mission_id)
        if current is not None and not current.done():
            return
        self._gates.setdefault(mission_id, asyncio.Event()).set()
        self._tasks[mission_id] = asyncio.create_task(
            self._drive(mission_id), name=f"mission-{mission_id}"
        )

    async def emit(self, mission_id: str, message: str, severity: Severity = Severity.INFO) -> None:
        m = await self.store.get(mission_id)
        await self._bus.publish(
            Event(
                type=EventType.QUANTLAB_MISSION,
                message=message,
                severity=severity,
                data={
                    "mission_id": mission_id,
                    "state": m["state"] if m else None,
                    "stage": m["stage"] if m else None,
                    "kind": m["kind"] if m else None,
                },
            )
        )

    # creating -------------------------------------------------------------------------------

    async def create_from_source(
        self, source_id: str, budget: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            src = await self.sources.source(source_id)
        except IntakeError as exc:
            raise MissionError(exc.code, exc.message, exc.status) from exc
        for existing in await self.store.for_source(source_id):
            if existing["kind"] == "idea" and existing["state"] in OPEN:
                return await self.mission(existing["id"])  # one live mission per source
        checked = check_budget(budget)
        mission_id = "qm-" + new_id()[:12]
        await self.store.add(
            {
                "id": mission_id,
                "kind": "idea",
                "source_id": source_id,
                "title": src["title"][:120],
                "state": "RUNNING",
                "stage": IDEA_STAGES[0],
                "budget": checked,
                "budget_sha256": _hash(checked),
                "refs": {},
            }
        )
        await self.store.event(
            mission_id,
            "created",
            f"Research mission for “{src['title'][:80]}”",
            agent="jarvis",
            data={"budget": checked},
        )
        await self.emit(mission_id, "Research mission started")
        self._spawn(mission_id)
        return await self.mission(mission_id)

    async def on_source_ready(self, source_id: str) -> None:
        if self._stopping:
            return
        if not await self.store.for_source(source_id):
            await self.create_from_source(source_id)

    # owner actions --------------------------------------------------------------------------

    async def _open(self, mission_id: str) -> dict[str, Any]:
        m = await self.store.get(mission_id)
        if m is None:
            raise MissionError("NOT_FOUND", "No such mission.", 404)
        return m

    async def answer(self, mission_id: str, answers: dict[str, Any]) -> dict[str, Any]:
        m = await self._open(mission_id)
        waiting = m.get("waiting") or {}
        if m["state"] != "WAITING_USER" or waiting.get("kind") != "questions":
            raise MissionError("NOT_WAITING", "The mission isn't waiting for answers.", 409)
        current = await self.sources.store.blueprint(m["refs"]["blueprint_id"])
        assert current is not None
        try:
            resolved = bp.resolve(current, answers)
        except bp.BlueprintError as exc:
            raise MissionError("ANSWER_INVALID", str(exc)) from exc
        new = await self._save_blueprint(m, resolved, origin="owner", parent=current["id"])
        refs = {**m["refs"], "blueprint_id": new["id"]}
        await self.store.event(
            mission_id,
            "answer",
            "Your definitions were applied",
            agent="jarvis",
            data={"answers": answers, "blueprint": new["id"]},
        )
        await self.store.transition(
            mission_id, ("WAITING_USER",), "RUNNING", stage="boundary", waiting=None, refs=refs
        )
        self._spawn(mission_id)
        return await self.mission(mission_id)

    async def approve_data(self, mission_id: str, max_usd: float) -> dict[str, Any]:
        """The owner approves the mission's data quote (an explicit, bounded purchase)."""
        m = await self._open(mission_id)
        waiting = m.get("waiting") or {}
        if m["refs"].get("job_id"):
            return await self.mission(mission_id)  # already approved: never twice
        if m["state"] != "WAITING_APPROVAL" or waiting.get("kind") != "data_purchase":
            raise MissionError("NOT_WAITING", "The mission isn't waiting for a data approval.", 409)
        cap = float(m["budget"]["max_paid_data_usd"])
        if max_usd > cap:
            raise MissionError(
                "OVER_MISSION_BUDGET",
                f"This mission's data budget is ${cap:.2f}; approve at most that, or start a "
                "mission with a larger data budget.",
            )
        try:
            job = await self.hub.approve(waiting["quote_id"], max_usd)
        except HubError as exc:
            raise MissionError(exc.code, exc.message, exc.status) from exc
        refs = {**m["refs"], "job_id": job["id"], "data_approved_usd": max_usd}
        await self.store.event(
            mission_id,
            "approval",
            f"You approved up to ${max_usd:.2f} for data",
            agent="vector",
            data={"quote": waiting["quote_id"], "job": job["id"]},
        )
        await self.store.transition(
            mission_id, ("WAITING_APPROVAL",), "RUNNING", waiting=None, refs=refs
        )
        self._spawn(mission_id)
        return await self.mission(mission_id)

    async def decline_data(self, mission_id: str) -> dict[str, Any]:
        m = await self._open(mission_id)
        waiting = m.get("waiting") or {}
        if waiting.get("kind") != "data_purchase":
            raise MissionError("NOT_WAITING", "No data quote is waiting.", 409)
        with contextlib.suppress(HubError):
            await self.hub.reject(waiting["quote_id"])
        await self.store.transition(
            mission_id,
            ("WAITING_APPROVAL",),
            "BLOCKED",
            waiting=None,
            error="You declined the data purchase. Resume to get a fresh quote, or cancel.",
            refs={**m["refs"], "quote_id": None},
        )
        await self.store.event(mission_id, "decline", "Data purchase declined", agent="vector")
        await self.emit(mission_id, "Data purchase declined")
        return await self.mission(mission_id)

    async def pause(self, mission_id: str) -> dict[str, Any]:
        m = await self._open(mission_id)
        if m["state"] == "RUNNING":
            self._gates.setdefault(mission_id, asyncio.Event()).clear()
            await self.store.transition(mission_id, ("RUNNING",), "PAUSED")
            await self.store.event(mission_id, "pause", "Paused by you")
            await self.emit(mission_id, "Mission paused")
        return await self.mission(mission_id)

    async def resume(self, mission_id: str) -> dict[str, Any]:
        m = await self._open(mission_id)
        connect = (
            m["state"] == "WAITING_USER" and (m.get("waiting") or {}).get("kind") == "connect_data"
        )
        if m["state"] in ("PAUSED", "BLOCKED") or connect:
            await self.store.transition(
                mission_id,
                ("PAUSED", "BLOCKED", "WAITING_USER"),
                "RUNNING",
                error=None,
                waiting=None,
            )
            await self.store.event(mission_id, "resume", "Resumed by you")
            self._gates.setdefault(mission_id, asyncio.Event()).set()
            self._spawn(mission_id)
        return await self.mission(mission_id)

    async def on_ai_available(self) -> None:
        """An AI route is back: missions paused for it continue at their stage."""
        for m in await self.store.in_state("BLOCKED"):
            if str(m.get("error") or "").startswith("AI_PAUSED"):
                await self.resume(m["id"])

    async def cancel(self, mission_id: str) -> dict[str, Any]:
        m = await self._open(mission_id)
        if m["state"] in DONE:
            return await self.mission(mission_id)
        waiting = m.get("waiting") or {}
        if waiting.get("kind") == "data_purchase":
            with contextlib.suppress(HubError):
                await self.hub.reject(waiting["quote_id"])
        await self.store.transition(mission_id, OPEN, "CANCELED", waiting=None)
        task = self._tasks.get(mission_id)
        if task is not None and not task.done():
            task.cancel()
        self._gates.setdefault(mission_id, asyncio.Event()).set()
        await self.store.event(mission_id, "cancel", "Cancelled by you")
        await self.emit(mission_id, "Mission cancelled")
        return await self.mission(mission_id)

    # reading --------------------------------------------------------------------------------

    async def missions(self) -> list[dict[str, Any]]:
        out = []
        for m in await self.store.recent():
            out.append(
                {
                    k: m[k]
                    for k in (
                        "id",
                        "kind",
                        "source_id",
                        "parent_id",
                        "title",
                        "state",
                        "stage",
                        "verdict",
                        "spent_usd",
                        "created_at",
                        "updated_at",
                        "waiting",
                    )
                }
            )
        return out

    async def mission(self, mission_id: str) -> dict[str, Any]:
        m = await self._open(mission_id)
        m["tasks"] = await self.store.tasks(mission_id)
        m["events"] = await self.store.events(mission_id, 200)
        m["trials"] = await self.store.trials(mission_id=mission_id)
        m["stages"] = list(IDEA_STAGES) if m["kind"] == "idea" else list(EVOLUTION_STAGES)
        m["model_label"] = self._label()
        bid = m["refs"].get("blueprint_id")
        m["blueprint"] = bp.explain(await self.sources.store.blueprint(bid)) if bid else None
        return m

    async def agents(self) -> list[dict[str, Any]]:
        running = await self.store.tasks_in("RUNNING")
        stats = await self.store.agent_stats()
        out = []
        for profile in ROSTER:
            current = [
                {
                    "task_id": t["id"],
                    "mission_id": t["mission_id"],
                    "kind": t["kind"],
                    "objective": t["objective"],
                }
                for t in running
                if t["agent"] == profile["id"]
            ]
            entry = stats.get(profile["id"], {"tasks": {}, "spent_usd": 0.0})
            out.append(
                {
                    **profile,
                    "status": ("working" if current else "idle")
                    if profile["active"]
                    else "not_used",
                    "current": current,
                    "tasks": entry["tasks"],
                    "spent_usd": round(entry["spent_usd"], 4),
                }
            )
        return out

    async def activity(self, limit: int = 200) -> list[dict[str, Any]]:
        return await self.store.events(None, limit)

    async def dossier(self, mission_id: str) -> dict[str, Any]:
        m = await self._open(mission_id)
        path = self.root / mission_id / "dossier.md"
        if not path.exists():
            raise MissionError(
                "NO_DOSSIER", "The dossier is written when the mission completes.", 404
            )
        return {
            "markdown": path.read_text(encoding="utf-8"),
            "json": json.loads(
                (self.root / mission_id / "dossier.json").read_text(encoding="utf-8")
            ),
            "verdict": m["verdict"],
        }

    # driving --------------------------------------------------------------------------------

    async def _gate(self, mission_id: str) -> None:
        gate = self._gates.setdefault(mission_id, asyncio.Event())
        if not gate.is_set():
            await gate.wait()
        m = await self.store.get(mission_id)
        if m is None or m["state"] in DONE:
            raise asyncio.CancelledError

    async def _drive(self, mission_id: str) -> None:
        async with self._slots:
            while True:
                m = await self.store.get(mission_id)
                if m is None or m["state"] != "RUNNING":
                    return
                stages = IDEA_STAGES if m["kind"] == "idea" else EVOLUTION_STAGES
                stage = m["stage"] or stages[0]
                try:
                    outcome = await getattr(self, f"_step_{stage}")(m)
                except asyncio.CancelledError:
                    raise
                except Blocked as b:
                    await self._block(m, b.code, b.message)
                    return
                except Exception as exc:  # one bad step must not take JARVIS down
                    log.exception(
                        "mission step failed", extra={"mission": mission_id, "stage": stage}
                    )
                    await self._block(
                        m, "STEP_FAILED", f"{stage} failed: {type(exc).__name__}: {str(exc)[:300]}"
                    )
                    return
                if outcome == "wait":
                    return
                if outcome.startswith("goto:"):
                    await self.store.update(mission_id, stage=outcome.split(":", 1)[1])
                    continue
                if outcome == "done" or stage == stages[-1]:
                    await self.store.transition(mission_id, ("RUNNING",), "COMPLETE", stage=None)
                    await self.store.event(
                        mission_id, "complete", "Mission complete", agent="jarvis"
                    )
                    await self.emit(mission_id, "Mission complete")
                    return
                await self.store.update(mission_id, stage=stages[stages.index(stage) + 1])

    async def _block(self, m: dict[str, Any], code: str, message: str) -> None:
        await self.store.transition(m["id"], ("RUNNING",), "BLOCKED", error=f"{code}: {message}")
        await self.store.event(m["id"], "blocked", message, data={"code": code})
        await self.emit(m["id"], f"Mission blocked: {message}", Severity.WARNING)

    async def _wait(
        self, m: dict[str, Any], state: str, waiting: dict[str, Any], message: str
    ) -> str:
        await self.store.transition(m["id"], ("RUNNING",), state, waiting=waiting)
        await self.store.event(m["id"], "waiting", message, data=waiting)
        await self.emit(m["id"], message)
        return "wait"

    async def _set_refs(self, m: dict[str, Any], **refs: Any) -> None:
        m["refs"] = {**m["refs"], **refs}
        await self.store.update(m["id"], refs=m["refs"])

    async def task(
        self,
        m: dict[str, Any],
        agent: str,
        kind: str,
        objective: str,
        inputs: Any,
        tools: list[str],
        model: str | None = None,
    ) -> Task:
        row = {
            "id": "qt-" + new_id()[:12],
            "mission_id": m["id"],
            "agent": agent,
            "kind": kind,
            "objective": objective,
            "inputs_hash": _hash(inputs),
            "allowed_tools": tools,
            "budget": {"max_model_usd": m["budget"]["max_model_usd"], "spent_usd": m["spent_usd"]},
            "state": "RUNNING",
            "model": model,
            "actions": [],
            "artifacts": [],
            "evidence_refs": [],
            "limitations": [],
            "started_at": sentinel_now(),
        }
        await self.store.add_task(row)
        await self.store.event(
            m["id"], "task", f"{agent.upper()} · {objective}", task_id=row["id"], agent=agent
        )
        await self.emit(m["id"], f"{agent.upper()}: {objective}")
        return Task(self, m, row)

    async def call(self, m: dict[str, Any], task: Task, role: str, fn: Any, **kwargs: Any) -> Any:
        """One model-backed step with the mission's budget, pause gate and retries."""
        model = self._model(role)
        if model is None:
            await task.fail("No model connected", state="BLOCKED")
            raise Blocked(
                "NO_MODEL", "Connect Claude in Settings — ATLAS, CIPHER and JARVIS need it."
            )
        price = None if self._scripted() else self._prices.get(model.model_id)
        if price is None and not self._scripted():
            await task.fail(f"No price for {model.model_id}", state="BLOCKED")
            raise Blocked("NO_PRICE", f"No price for {model.model_id} in config/ultron.yaml.")
        await self.store.update_task(task.id, model=self._label())

        async def remaining() -> float:
            fresh = await self.store.get(m["id"])
            spent = float(fresh["spent_usd"]) if fresh else 0.0
            return float(m["budget"]["max_model_usd"]) - spent

        async def sink(_usage: Any, cost: float) -> None:
            await self.store.add_spend(m["id"], cost)
            await self.store.add_task_cost(task.id, cost)

        budget = Budget(price, 8000, remaining)
        tries = int(m["budget"]["max_retries"]) + 1
        last = ""
        for attempt in range(tries):
            try:
                with ai_scope("quantlab", mission_id=m["id"], task_id=task.id, agent=role):
                    result = await fn(
                        model,
                        budget=budget,
                        gate=lambda: self._gate(m["id"]),
                        on_usage=sink,
                        **kwargs,
                    )
            except BudgetExceeded as exc:
                await task.fail(str(exc), state="BLOCKED")
                raise Blocked("BUDGET", f"The mission's model budget is used up ({exc}).") from exc
            except AIPaused as exc:
                # No route right now: the stage waits and reruns when a route is back.
                # Model steps here have no side effects; data purchases are separate.
                await task.fail(exc.message, state="BLOCKED")
                raise Blocked("AI_PAUSED", exc.message) from exc
            if result.value is not None:
                await task.act(
                    f"{role.upper()} submitted a checked result"
                    + (f" after {attempt + 1} attempts" if attempt else ""),
                    rounds=result.agent.rounds,
                    cost_usd=round(result.agent.cost, 4),
                )
                return result.value
            last = result.agent.error or "no valid submission"
            await task.act(f"Attempt {attempt + 1} produced no valid result: {last[:160]}")
        await task.fail(last)
        raise Blocked("NO_RESULT", f"{role.upper()} produced no valid result ({last[:200]}).")

    # stages: idea -----------------------------------------------------------------------------

    async def _source(self, m: dict[str, Any]) -> dict[str, Any]:
        return await self.sources.source(m["source_id"])

    async def _step_register(self, m: dict[str, Any]) -> str:
        src = await self._source(m)
        task = await self.task(
            m,
            "archive",
            "register",
            "Register the source and its evidence",
            {"source": src["id"]},
            ["read_source"],
        )
        modalities: dict[str, int] = {}
        for seg in src["segments"]:
            modalities[seg["modality"]] = modalities.get(seg["modality"], 0) + 1
        results = {
            "source": src["id"],
            "kind": src["kind"],
            "status": src["status"],
            "segments": len(src["segments"]),
            "modalities": modalities,
            "warnings": src["meta"].get("warnings", []),
        }
        await self._set_refs(
            m, source={"id": src["id"], "kind": src["kind"], "title": src["title"]}
        )
        if not src["segments"]:
            await task.done(results, limitations=["Nothing readable in the source."])
            return await self._finish(
                m,
                "SOURCE_UNAVAILABLE",
                [
                    "Nothing readable was extracted from this source."
                    + (
                        " A link only gives metadata — drop the video file or paste the transcript."
                        if src["kind"] == "link"
                        else ""
                    )
                ],
            )
        await task.act(
            f"{len(src['segments'])} evidence segment(s): "
            + ", ".join(f"{n} {k}" for k, n in modalities.items())
        )
        await task.done(
            results,
            evidence=[s["id"] for s in src["segments"]],
            limitations=src["meta"].get("warnings", []),
        )
        return "next"

    async def _step_claims(self, m: dict[str, Any]) -> str:
        src = await self._source(m)
        segments, notes = src["segments"], src["notes"]
        task = await self.task(
            m,
            "atlas",
            "claims",
            "Extract the source's claims with provenance",
            {"segments": segments, "notes": notes},
            ["submit_claims"],
        )
        characters = sum(len(s["text"]) for s in segments)
        await self.sources.store.audit(
            src["id"],
            "TEXT_SENT_TO_MODEL",
            {
                "agent": "ATLAS",
                "model": self._label(),
                "characters": characters,
                "media_sent": False,
            },
        )
        await task.act(
            f"Reading {len(segments)} segment(s) ({characters} characters of text; no media leaves the computer)"
        )
        payload = await self.call(
            m,
            task,
            "atlas",
            agents.extract_claims,
            kind=src["kind"],
            segments=segments,
            notes=notes,
        )
        extraction = "qx-" + new_id()[:10]
        claims = cl.numbered(cl.augment(payload["claims"], segments), extraction)
        await self.sources.store.add_extraction(
            extraction, src["id"], "claims", "atlas", self._label(), _hash(segments)
        )
        await self.sources.store.add_claims(src["id"], extraction, claims)
        await self.sources.store.finish_extraction(
            extraction,
            "DONE",
            {"summary": payload["summary"], "unreadable": payload["unreadable"]},
            None,
            0.0,
        )
        kinds: dict[str, int] = {}
        for c in claims:
            kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
        await self._set_refs(
            m,
            claims={
                "extraction_id": extraction,
                "count": len(claims),
                "kinds": kinds,
                "summary": payload["summary"],
            },
        )
        await task.done(
            {"claims": len(claims), "kinds": kinds, "summary": payload["summary"]},
            artifacts=[{"kind": "claims", "id": extraction}],
            evidence=sorted({s for c in claims for s in c["segment_ids"]}),
            limitations=payload["unreadable"],
        )
        return "next"

    async def _step_blueprint(self, m: dict[str, Any]) -> str:
        src = await self._source(m)
        claims = await self.sources.store.claims(src["id"], m["refs"]["claims"]["extraction_id"])
        detected = catalog.detect(src["segments"])
        task = await self.task(
            m,
            "jarvis",
            "blueprint",
            "Formalize the claims into a testable rule",
            {"claims": claims, "notes": src["notes"]},
            ["submit_blueprint"],
        )
        if detected["terms"]:
            await task.act(
                "Ambiguous terms in the source: "
                + ", ".join(catalog.BY_ID[t].name for t in detected["terms"])
            )
        compiled = await self.call(
            m,
            task,
            "jarvis",
            agents.draft_blueprint,
            kind=src["kind"],
            segments=src["segments"],
            notes=src["notes"],
            claims=claims,
            detected=detected,
        )
        saved = await self._save_blueprint(m, compiled, origin="model", parent=None)
        await self._set_refs(m, blueprint_id=saved["id"])
        await task.done(
            {
                "status": compiled["status"],
                "blocking": compiled.get("blocking", []),
                "unsupported": [u["feature"] for u in compiled["unsupported"]],
            },
            artifacts=[{"kind": "blueprint", "id": saved["id"]}],
        )
        if compiled["status"] == "DRAFT":
            return await self._finish(
                m, "RULES_UNCLEAR", [compiled.get("reason") or "No testable rule."]
            )
        return "next"

    async def _save_blueprint(
        self, m: dict[str, Any], compiled: dict[str, Any], *, origin: str, parent: str | None
    ) -> dict[str, Any]:
        bid = "bp-" + new_id()[:12]
        await self.sources.store.add_blueprint(
            {
                "id": bid,
                "source_id": m.get("source_id"),
                "parent_id": parent,
                "status": compiled["status"],
                "spec": compiled["spec"],
                "spec_sha256": compiled.get("spec_sha256"),
                "provenance": compiled.get("provenance", {}),
                "ambiguities": compiled.get("ambiguities", []),
                "questions": compiled.get("questions", []),
                "unsupported": compiled.get("unsupported", []),
                "summary": compiled.get("summary"),
                "origin": origin,
            }
        )
        saved = await self.sources.store.blueprint(bid)
        assert saved is not None
        return saved

    async def _step_boundary(self, m: dict[str, Any]) -> str:
        src = await self._source(m)
        blueprint = await self.sources.store.blueprint(m["refs"]["blueprint_id"])
        assert blueprint is not None
        claims = await self.sources.store.claims(src["id"], m["refs"]["claims"]["extraction_id"])
        task = await self.task(
            m,
            "sentinel",
            "boundary",
            "Check extraction and definition boundaries",
            {"blueprint": blueprint["id"]},
            [],
        )
        compiled = {
            **blueprint,
            "blocking": sorted(
                p for p, e in blueprint["provenance"].items() if e["class"] == "MISSING_BLOCKING"
            ),
        }
        report = sentinel.boundary(src, claims, compiled, catalog.detect(src["segments"]))
        await self._set_refs(m, boundary=report)
        for check in report["checks"]:
            await task.act(f"{check['result']} · {check['title']} — {check['detail']}")
        await task.done(
            {"passed": report["passed"], "sha256": report["sha256"]},
            artifacts=[{"kind": "boundary_report", "sha256": report["sha256"]}],
            state="COMPLETE" if report["passed"] else "VETOED",
        )
        if not report["passed"]:
            raise Blocked("BOUNDARY_VETO", "SENTINEL found an extraction problem; see its report.")
        if blueprint["status"] == "NEEDS_DEFINITION":
            return await self._wait(
                m,
                "WAITING_USER",
                {
                    "kind": "questions",
                    "blueprint_id": blueprint["id"],
                    "questions": blueprint["questions"],
                },
                "JARVIS needs one decision from you before testing",
            )
        return "next"

    async def _step_freeze(self, m: dict[str, Any]) -> str:
        blueprint = await self.sources.store.blueprint(m["refs"]["blueprint_id"])
        assert blueprint is not None
        task = await self.task(
            m,
            "archive",
            "freeze",
            "Freeze the rule as an immutable version",
            {"spec_sha256": blueprint["spec_sha256"]},
            ["create_strategy"],
        )
        if m["refs"].get("version_id"):
            await task.done({"version_id": m["refs"]["version_id"], "reused": True})
            return "next"
        spec = blueprint["spec"]  # frozen exactly as checked: same bytes, same hash
        created = await self.research.create_strategy(
            spec, origin="ai", note=f"From source {m['source_id']} (blueprint {blueprint['id']})"
        )
        version = created["versions"][-1]
        await self.sources.store.link_blueprint(blueprint["id"], created["id"], version["id"])
        await self._set_refs(
            m,
            strategy_id=created["id"],
            version_id=version["id"],
            spec_sha256=version["spec_sha256"],
        )
        await task.act(
            f"Frozen as {created['name']} v{version['number']} (sha256 {version['spec_sha256'][:12]}…)"
        )
        await task.done(
            {
                "strategy_id": created["id"],
                "version_id": version["id"],
                "spec_sha256": version["spec_sha256"],
            },
            artifacts=[{"kind": "strategy_version", "id": version["id"]}],
        )
        return "next"

    async def _step_protocol(self, m: dict[str, Any]) -> str:
        blueprint = await self.sources.store.blueprint(m["refs"]["blueprint_id"])
        assert blueprint is not None
        spec = parse((await self._version(m))["spec"])  # the frozen version, nothing else
        task = await self.task(
            m,
            "cipher",
            "protocol",
            "Pre-register the test protocol before any data",
            {"spec_sha256": spec.sha256()},
            ["submit_protocol"],
        )
        opening = (
            "Frozen rule (plain language):\n- "
            + "\n- ".join(blueprint.get("what_it_does") or [])
            + f"\n\nValidation plan in the spec: {json.dumps(blueprint['spec']['validation'])}"
            + f"\nTests that exist: {', '.join(TEST_IDS)}"
            + "\nData: 1-minute OHLCV bars from Databento (GLBX.MDP3) plus contract definitions."
        )

        def check(payload: dict[str, Any]) -> dict[str, Any]:
            months = int(payload["months"])
            if not 3 <= months <= 36:
                raise ValueError("months must be 3–36")
            floor = spec.validation.min_trades_oos
            if int(payload["min_trades_oos"]) < floor:
                raise ValueError(f"min_trades_oos can't be below the spec's {floor}")
            tests = [t for t in payload.get("tests") or [] if t in TEST_IDS] or list(TEST_IDS)
            return {
                "months": months,
                "min_trades_oos": int(payload["min_trades_oos"]),
                "max_cost_share": float(payload.get("max_cost_share") or 0.5),
                "tests": tests,
                "acceptance": [str(a)[:240] for a in payload["acceptance"]][:8],
                "rejection": [str(r)[:240] for r in payload["rejection"]][:8],
                "limitations": [str(x)[:240] for x in payload.get("limitations") or []][:8],
                "rationale": str(payload["rationale"])[:1200],
                "floors": {
                    "min_trades_oos": floor,
                    "cost_stress": spec.validation.cost_stress,
                    "holdout_fraction": spec.validation.holdout_fraction,
                    "verdict": "fixed function; synthetic data capped",
                },
            }

        protocol = await self.call(
            m,
            task,
            "cipher",
            lambda model, **kw: agents.run_tool(
                model, agents.CIPHER_PROTOCOL_SYSTEM, opening, agents.SUBMIT_PROTOCOL, check, **kw
            ),
        )
        sealed = sentinel.seal({"kind": "protocol", "spec_sha256": spec.sha256(), **protocol})
        await self._set_refs(m, protocol=sealed)
        await task.act(
            f"Protocol frozen: {protocol['months']} months of data, ≥ {protocol['min_trades_oos']} out-of-sample trades"
        )
        await task.done(
            {"protocol_sha256": sealed["sha256"], "months": protocol["months"]},
            artifacts=[{"kind": "protocol", "sha256": sealed["sha256"]}],
            limitations=protocol["limitations"],
        )
        return "next"

    async def _step_data(self, m: dict[str, Any]) -> str:
        refs = m["refs"]
        if refs.get("dataset_id"):
            return "next"
        version = await self._version(m)
        spec = parse(version["spec"])
        task = await self.task(
            m,
            "vector",
            "data",
            "Find or acquire the data the protocol needs",
            {"symbol": spec.instrument.symbol, "months": refs["protocol"]["months"]},
            ["library", "quote", "build_dataset"],
        )
        if refs.get("job_id"):
            outcome = await self._follow_job(m, task, spec)
            if outcome != "requote":
                return outcome
        status = await self.hub.status()
        if status["status"] != "CONNECTED":
            await task.done({"waiting": "connection"}, state="WAITING")
            return await self._wait(
                m,
                "WAITING_USER",
                {"kind": "connect_data"},
                "Connect Databento in the Data Hub so VECTOR can check the data",
            )
        try:
            info = await self.hub.dataset_info(spec.instrument.dataset)
        except HubError as exc:
            await task.done({"error": exc.code}, limitations=[exc.message])
            return await self._finish(
                m, "DATA_INSUFFICIENT", [f"{spec.instrument.dataset}: {exc.message}"]
            )
        end = date.fromisoformat(info["available_end"])
        months = int(refs["protocol"]["months"])
        start = max(_months_before(end, months), date.fromisoformat(info["available_start"]))
        wanted_days = (end - start).days
        await task.act(
            f"Need {spec.instrument.symbol} ohlcv-1m + definitions {start} → {end} (UTC, end exclusive)"
        )
        for ds in await self.hub.datasets():
            span = (date.fromisoformat(ds["end"]) - date.fromisoformat(ds["start"])).days
            if (
                ds["symbol"] == spec.instrument.symbol
                and ds["schema"] == "ohlcv-1m"
                and span >= 0.9 * wanted_days
            ):
                await task.act(
                    f"Reusing verified dataset {ds['id']} ({ds['start']} → {ds['end']}) — no purchase"
                )
                await self._set_refs(m, dataset_id=ds["id"], data_reused=True)
                await task.done(
                    {"dataset_id": ds["id"], "reused": True},
                    artifacts=[{"kind": "dataset", "id": ds["id"]}],
                )
                return "next"
        request = {
            "dataset": spec.instrument.dataset,
            "schema": "ohlcv-1m",
            "stype_in": spec.instrument.stype_in,
            "symbols": [spec.instrument.symbol],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "include_definitions": True,
        }
        try:
            quote = await self.hub.quote(request)
        except HubError as exc:
            await task.done({"error": exc.code}, limitations=[exc.message])
            return await self._finish(m, "DATA_INSUFFICIENT", [exc.message])
        if quote["cost_usd"] <= 0:
            await task.act("Every requested day is already cached — building the dataset")
            return await self._build(m, task, request)
        await self._set_refs(m, quote_id=quote["id"], data_request=request)
        cap = float(m["budget"]["max_paid_data_usd"])
        await task.act(
            f"Quote {quote['id']}: estimated ${quote['cost_usd']:.2f} — waiting for your approval"
        )
        await task.done({"quote_id": quote["id"], "cost_usd": quote["cost_usd"]}, state="WAITING")
        return await self._wait(
            m,
            "WAITING_APPROVAL",
            {
                "kind": "data_purchase",
                "quote_id": quote["id"],
                "cost_usd": quote["cost_usd"],
                "mission_cap_usd": cap,
                "within_mission_budget": quote["cost_usd"] <= cap,
                "fixture": quote.get("fixture", False),
                "request": request,
            },
            f"Data costs an estimated ${quote['cost_usd']:.2f} — approve or decline",
        )

    async def _follow_job(self, m: dict[str, Any], task: Task, spec: Any) -> str:
        job_id = m["refs"]["job_id"]
        while True:
            await self._gate(m["id"])
            job = await self.hub.job(job_id)
            if job["status"] == "COMPLETED":
                break
            if job["status"] in ("FAILED", "CANCELED", "INTERRUPTED"):
                # Delivered days are cached and never bought again; only the rest is quoted
                # anew — and needs your approval again.
                await self._set_refs(m, job_id=None, quote_id=None)
                await task.act(
                    f"Download {job['status'].lower()} after {job.get('chunks_done', 0)} of "
                    f"{job.get('chunks_total', '?')} chunk(s); delivered days stay cached — "
                    "quoting only the missing days"
                )
                return "requote"
            await asyncio.sleep(0.2)
        await task.act(f"Download {job_id} completed")
        return await self._build(m, task, m["refs"]["data_request"])

    async def _build(self, m: dict[str, Any], task: Task, request: dict[str, Any]) -> str:
        built = await self.hub.build_dataset(
            {
                "dataset": request["dataset"],
                "schema": "ohlcv-1m",
                "stype_in": request["stype_in"],
                "symbol": request["symbols"][0],
                "start": request["start"],
                "end": request["end"],
            }
        )
        await self._set_refs(m, dataset_id=built["id"], data_reused=False)
        await task.act(
            f"Verified dataset {built['id']} built ({built.get('records', '?')} records)"
        )
        await task.done(
            {"dataset_id": built["id"]}, artifacts=[{"kind": "dataset", "id": built["id"]}]
        )
        return "next"

    async def _version(self, m: dict[str, Any]) -> dict[str, Any]:
        strategy = await self.research.strategy(m["refs"]["strategy_id"])
        return next(v for v in strategy["versions"] if v["id"] == m["refs"]["version_id"])

    async def wait_run(self, m: dict[str, Any], run_id: str) -> dict[str, Any]:
        while True:
            await self._gate(m["id"])
            run = await self.research.run(run_id)
            if run["status"] in ("COMPLETED", "FAILED", "CANCELED"):
                return run
            if run["status"] == "INTERRUPTED":
                await self.research.start_run(
                    run["version_id"], run["dataset_id"], kind=run["kind"]
                )
            await asyncio.sleep(0.2)

    async def _step_baseline(self, m: dict[str, Any]) -> str:
        task = await self.task(
            m,
            "cipher",
            "baseline",
            "Run the deterministic validation (holdout sealed)",
            {"version": m["refs"]["version_id"], "dataset": m["refs"]["dataset_id"]},
            ["start_run"],
        )
        run_id = m["refs"].get("run_id")
        if run_id is None:
            run = await self.research.start_run(
                m["refs"]["version_id"], m["refs"]["dataset_id"], kind="validation"
            )
            run_id = run["id"]
            await self._set_refs(m, run_id=run_id)
        await task.act(
            f"Engine run {run_id} started (conservative fills, optimistic bound, test suite)"
        )
        run = await self.wait_run(m, run_id)
        if run["status"] != "COMPLETED":
            await task.fail(run.get("error") or run["status"])
            raise Blocked(
                "RUN_FAILED", f"The engine run {run['status'].lower()}: {run.get('error') or ''}"
            )
        summary = run["summary"]
        oos = summary["segments"]["oos"]
        await task.act(
            f"Out-of-sample: {oos['trades']} trades, net {oos['net_pnl']}; verdict {summary['verdict']['verdict']}"
        )
        await task.done(
            {"run_id": run_id, "verdict": summary["verdict"], "oos": oos},
            artifacts=[{"kind": "run", "id": run_id}],
            limitations=[r["message"] for r in summary["fitness"]["reasons"]],
        )
        return "next"

    async def _step_audit(self, m: dict[str, Any]) -> str:
        run_id = m["refs"]["run_id"]
        task = await self.task(
            m, "sentinel", "audit", "Independently recompute and audit the run", {"run": run_id}, []
        )
        report = await self.audit_run(run_id)
        for check in report["checks"]:
            await task.act(f"{check['result']} · {check['title']} — {check['detail']}")
        await self._set_refs(m, audit=report)
        await task.done(
            {"passed": report["passed"], "sha256": report["sha256"]},
            artifacts=[{"kind": "audit_report", "sha256": report["sha256"]}],
            limitations=[report["limitation"]],
            state="COMPLETE" if report["passed"] else "VETOED",
        )
        return "next"

    async def audit_run(self, run_id: str) -> dict[str, Any]:
        materials = await self.research.materials(run_id)
        rows = (await self.research.trades(run_id, 0, 10**9))["rows"]
        return await asyncio.to_thread(sentinel.run_audit, materials, rows)

    async def _step_verdict(self, m: dict[str, Any]) -> str:
        run = await self.research.run(m["refs"]["run_id"])
        summary = run["summary"]
        audit = m["refs"]["audit"]
        label, reasons = research_verdict(summary, audit)
        task = await self.task(
            m,
            "jarvis",
            "report",
            "Write the evidence verdict and dossier",
            {"run": run["id"], "audit": audit["sha256"]},
            ["submit_report"],
        )
        evidence = evidence_block(summary, audit, label)
        note = await self._narrative(m, task, evidence)
        await self._finish(m, label, reasons, note=note, summary=summary)
        await task.done(
            {"verdict": label, "narrative_checked": note["checked"]},
            artifacts=[{"kind": "dossier", "path": f"{m['id']}/dossier.md"}],
        )
        return "done"

    async def _narrative(self, m: dict[str, Any], task: Task, evidence: str) -> dict[str, Any]:
        problems: list[str] = []

        def check(payload: dict[str, Any]) -> dict[str, Any]:
            text = str(payload.get("text") or "")[:2500]
            invented = sentinel.narrative_guard(text, evidence)
            claims = sentinel.forbidden(text)
            if invented or claims:
                problems.append(f"numbers {invented} / phrases {claims}")
                raise ValueError(
                    f"SENTINEL refused the note: numbers not in the evidence {invented}; "
                    f"overclaiming {claims}. Use only the evidence's numbers."
                )
            return {"text": text, "next_step": str(payload.get("next_step") or "")[:400]}

        opening = f"Evidence (the only numbers you may use):\n{evidence}\n\nWrite the closing note with submit_report."
        try:
            out = await self.call(
                m,
                task,
                "jarvis",
                lambda model, **kw: agents.run_tool(
                    model, agents.REPORT_SYSTEM, opening, agents.SUBMIT_REPORT, check, **kw
                ),
            )
            await task.act("SENTINEL checked the note: every number is in the evidence")
            return {**out, "checked": True, "by": self._label()}
        except Blocked as b:
            await task.act(
                f"The model's note was not used ({b.message[:160]}); a plain summary is shown instead"
            )
            return {
                "text": evidence.split("\n")[0],
                "next_step": "",
                "checked": False,
                "refused": problems,
            }

    async def _finish(
        self,
        m: dict[str, Any],
        label: str,
        reasons: list[str],
        *,
        note: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> str:
        assert label in VERDICTS
        verdict = {
            "label": label,
            "reasons": reasons,
            "note": note,
            "never": "No verdict here means a strategy will make money.",
            # Synthetic fixture prices: an engineering check, shown as such everywhere.
            "fixture": bool(summary and summary.get("fixture")),
        }
        await self._set_refs(m, verdict=verdict)
        await self.store.update(m["id"], verdict=label)
        await self.write_dossier(m, verdict, summary)
        await self.store.event(
            m["id"],
            "verdict",
            f"Verdict: {label.replace('_', ' ').lower()}",
            agent="jarvis",
            data={"reasons": reasons},
        )
        return "done"

    async def write_dossier(
        self, m: dict[str, Any], verdict: dict[str, Any], summary: dict[str, Any] | None
    ) -> None:
        from jarvis.quantlab.ideas.dossier import build

        fresh = await self.store.get(m["id"])
        assert fresh is not None
        src = await self.sources.source(m["source_id"]) if m.get("source_id") else None
        blueprint = (
            await self.sources.store.blueprint(fresh["refs"]["blueprint_id"])
            if fresh["refs"].get("blueprint_id")
            else None
        )
        trials = await self.store.trials(mission_id=m["id"])
        markdown, data = build(
            fresh, verdict, src, blueprint, summary, trials, engine_version=ENGINE_VERSION
        )
        folder = self.root / m["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "dossier.md").write_text(markdown, encoding="utf-8")
        (folder / "dossier.json").write_text(
            json.dumps(data, indent=1, default=str), encoding="utf-8"
        )

    # evolution (see evolution.py) -----------------------------------------------------------

    async def evolve(self, mission_id: str, budget: dict[str, Any] | None = None) -> dict[str, Any]:
        from jarvis.quantlab.ideas import evolution

        return await evolution.create(self, mission_id, budget)

    async def lock_candidate(
        self, mission_id: str, version_id: str, confirm: bool
    ) -> dict[str, Any]:
        from jarvis.quantlab.ideas import evolution

        return await evolution.lock(self, mission_id, version_id, confirm)

    async def _step_diagnose(self, m: dict[str, Any]) -> str:
        from jarvis.quantlab.ideas import evolution

        return await evolution.diagnose(self, m)

    async def _step_propose(self, m: dict[str, Any]) -> str:
        from jarvis.quantlab.ideas import evolution

        return await evolution.propose(self, m)

    async def _step_test(self, m: dict[str, Any]) -> str:
        from jarvis.quantlab.ideas import evolution

        return await evolution.test(self, m)

    async def _step_compare(self, m: dict[str, Any]) -> str:
        from jarvis.quantlab.ideas import evolution

        return await evolution.compare(self, m)

    async def _step_holdout(self, m: dict[str, Any]) -> str:
        from jarvis.quantlab.ideas import evolution

        return await evolution.holdout(self, m)


EVOLUTION_STAGES = ("diagnose", "propose", "test", "compare", "holdout")


def _months_before(end: date, months: int) -> date:
    year, month = end.year, end.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1) if end.day == 1 else date(year, month, min(end.day, 28))


def research_verdict(summary: dict[str, Any], audit: dict[str, Any]) -> tuple[str, list[str]]:
    """The A11 verdict from the run's fixed verdict function and SENTINEL's audit."""
    run_verdict = summary["verdict"]
    tests = {t["id"]: t["status"] for t in summary.get("tests") or []}
    failed = [c for c in audit["checks"] if c["result"] == "FAIL"]
    reasons = list(run_verdict.get("reasons") or [])
    if failed:
        return "SIMULATION_INVALID", [f"SENTINEL: {c['title']} — {c['detail']}" for c in failed]
    if summary["fitness"]["status"] == "INVALID" or tests.get("DATA_INTEGRITY") == "FAILED":
        return "DATA_INSUFFICIENT", [r["message"] for r in summary["fitness"]["reasons"]] or reasons
    mapping = {
        "INVALID_DATA_OR_METHOD": "SIMULATION_INVALID",
        "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
        "REJECTED_HYPOTHESIS": "REJECTED_UNDER_TESTED_ASSUMPTIONS",
        "PROMISING_RESEARCH_CANDIDATE": "PROMISING_RESEARCH_CANDIDATE",
        "FORWARD_VALIDATION_REQUIRED": "FORWARD_VALIDATION_REQUIRED",
        "ROBUST_UNDER_TESTED_ASSUMPTIONS": "ROBUST_UNDER_TESTED_ASSUMPTIONS",
    }
    label = mapping.get(run_verdict["verdict"], "INSUFFICIENT_EVIDENCE")
    if run_verdict.get("would_be"):
        reasons.append(
            f"Without the synthetic-data cap the gates would say: "
            f"{mapping.get(run_verdict['would_be'], run_verdict['would_be'])}."
        )
    return label, reasons


def evidence_block(summary: dict[str, Any], audit: dict[str, Any], label: str) -> str:
    seg = summary["segments"]
    oos, ins = seg["oos"], seg["insample"]
    lines = [
        f"Verdict: {label}"
        + (
            " — SYNTHETIC FIXTURE DATA, an engineering check, not market evidence"
            if summary.get("fixture")
            else ""
        ),
        f"Rule: {summary.get('name')} on {summary.get('product')}",
        f"In-sample: {ins['trades']} trades, net {ins['net_pnl']}",
        f"Out-of-sample: {oos['trades']} trades, net {oos['net_pnl']}, win rate {oos.get('win_rate')}, "
        f"profit factor {oos.get('profit_factor')}",
        f"Max drawdown: {summary['metrics'].get('max_drawdown')}",
        "Tests: " + ", ".join(f"{t['id']} {t['status']}" for t in summary.get("tests") or []),
        f"SENTINEL audit: {'passed' if audit['passed'] else 'failed'}; second engine "
        f"{audit['second_engine']['agree']} of {audit['second_engine']['compared']} trades agree",
    ]
    return "\n".join(lines)
