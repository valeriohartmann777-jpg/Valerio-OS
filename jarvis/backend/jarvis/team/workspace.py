"""The ULTRON QuantLab: a team workspace on the same QuantLab engine (JARVIS 2.0, brief §6.5/§7.6).

One engine, one Data Vault, one strategy registry — two kinds of workspace:

- **Solo** (default): the owner's strategies. No agent reads or changes them.
- **Team project**: created only by the owner's explicit "Share with ULTRON". The team gets a
  *copy* of one Solo version as its own strategy family; grants say what the agents may do
  with it (read the spec, run backtests on the shared Data Vault, propose new versions).

Every agent action goes through :meth:`TeamWorkspace._require`, which checks the project, its
state and the grant server-side; a button in the UI is never the authorisation. Agents write
only to the team family (evolution writes versions to ``family_id = team strategy``), so a
Solo strategy can't be changed by them at all. Copying a reviewed team version back to Solo
is an owner action that adds a *new* Solo version — nothing is overwritten. Grants and
owner actions are kept in an append-only audit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from jarvis.events.bus import EventBus
from jarvis.team.store import TeamStore
from jarvis.util import new_id, utcnow

if TYPE_CHECKING:
    from jarvis.quantlab.futures.service import ResearchService
    from jarvis.quantlab.ideas.missions import MissionService
    from jarvis.quantlab.vault.lab import BacktestLab

log = logging.getLogger("jarvis.team")

GRANTS: dict[str, str] = {
    "read_spec": "Read the shared strategy and its results",
    "run_backtests": "Run backtests on the shared Data Vault (local data only, no purchases)",
    "propose_versions": "Propose improved versions (each a new team version + a registered trial)",
}
# What each agent task needs.
NEEDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("sentinel", "challenge"): ("read_spec",),
    ("cipher", "improve"): ("read_spec", "run_backtests", "propose_versions"),
}
TASK_LABEL = {
    (
        "sentinel",
        "challenge",
    ): "Challenge the latest team run (bias, look-ahead, intrabar, second engine)",
    ("cipher", "improve"): "Diagnose the latest team run and propose tested variants",
}


class TeamError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _grants(raw: dict[str, Any] | None) -> dict[str, bool]:
    raw = raw or {}
    unknown = set(raw) - set(GRANTS)
    if unknown:
        raise TeamError("UNKNOWN_GRANT", f"Unknown grant(s): {', '.join(sorted(unknown))}.")
    return {key: bool(raw.get(key, False)) for key in GRANTS}


class TeamWorkspace:
    def __init__(
        self,
        store: TeamStore,
        research: ResearchService,
        lab: BacktestLab,
        missions: MissionService,
        bus: EventBus,
        ai_ready: Callable[[], tuple[bool, str]],
    ) -> None:
        self.store = store
        self._research = research
        self._lab = lab
        self._missions = missions
        self._bus = bus
        self._ai_ready = ai_ready
        self._running: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        # A restart interrupts in-process SENTINEL checks; they are marked, never faked.
        for task in await self.store.tasks_in("QUEUED", "RUNNING"):
            if task["agent"] == "sentinel":
                await self.store.update_task(
                    task["id"],
                    state="FAILED",
                    error="Interrupted by a restart — ask again.",
                    finished_at=utcnow().isoformat(),
                )

    async def stop(self) -> None:
        for task in list(self._running.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._running.clear()

    async def wait_idle(self, timeout: float = 120.0) -> None:  # noqa: ASYNC109
        if self._running:
            await asyncio.wait_for(
                asyncio.gather(*self._running.values(), return_exceptions=True), timeout
            )

    # owner actions ----------------------------------------------------------------------------

    async def share(
        self, version_id: str, grants: dict[str, Any] | None, confirm: bool, name: str | None = None
    ) -> dict[str, Any]:
        """Share one Solo version with ULTRON: the team gets its own copy as a new family."""
        if not confirm:
            raise TeamError(
                "CONFIRM_REQUIRED",
                "Confirm the share: the team gets a copy; your Solo strategy stays unchanged.",
            )
        checked = _grants(grants)
        solo_strategy_id = await self._research.version_owner(version_id)
        if solo_strategy_id in await self.store.team_strategy_ids():
            raise TeamError(
                "ALREADY_TEAM", "This version already belongs to a team project.", status=409
            )
        solo = await self._research.strategy(solo_strategy_id)
        version = next(v for v in solo["versions"] if v["id"] == version_id)
        copy = await self._research.create_strategy(
            version["spec"],
            origin="team-share",
            note=f"Shared from Solo {solo_strategy_id} v{version['number']}",
        )
        project_id = "tp-" + new_id()[:12]
        await self.store.add_project(
            {
                "id": project_id,
                "name": (name or solo["name"])[:120],
                "solo_strategy_id": solo_strategy_id,
                "solo_version_id": version_id,
                "team_strategy_id": copy["id"],
                "grants": checked,
            }
        )
        await self.store.audit(
            project_id,
            "owner",
            "share",
            {"solo_version_id": version_id, "team_strategy_id": copy["id"], "grants": checked},
        )
        await self._emit(f"Shared “{solo['name']}” with ULTRON", project_id)
        return await self.project(project_id)

    async def set_grants(
        self, project_id: str, grants: dict[str, Any], confirm: bool
    ) -> dict[str, Any]:
        project = await self._open(project_id)
        if not confirm:
            raise TeamError("CONFIRM_REQUIRED", "Confirm the change of access.")
        checked = _grants(grants)
        await self.store.update_project(project_id, grants=checked)
        await self.store.audit(
            project_id, "owner", "grants", {"before": project["grants"], "after": checked}
        )
        await self._emit("Team access changed", project_id)
        return await self.project(project_id)

    async def close(self, project_id: str) -> dict[str, Any]:
        await self._open(project_id)
        await self.store.update_project(project_id, state="CLOSED")
        await self.store.audit(project_id, "owner", "close", {})
        await self._emit("Team project closed", project_id)
        return await self.project(project_id)

    async def backtest(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """A team backtest: the owner's click, the same BacktestLab and Data Vault as Solo."""
        project = await self._open(project_id)
        version_id = str(body.get("version_id") or "")
        if not version_id:
            team = await self._research.strategy(project["team_strategy_id"])
            version_id = team["versions"][-1]["id"]
        await self._team_version(project, version_id)
        advanced = {"kind": "validation", **(body.get("advanced") or {})}
        out = await self._lab.launch(
            {**body, "version_id": version_id, "advanced": advanced}, team_workspace=True
        )
        await self.store.audit(
            project_id,
            "owner",
            "backtest",
            {"version_id": version_id, "run_id": out["run"]["id"], "symbol": body.get("symbol")},
        )
        return out

    async def return_to_solo(
        self, project_id: str, version_id: str, confirm: bool
    ) -> dict[str, Any]:
        """Copy a reviewed team version into the Solo strategy as a *new* version."""
        project = await self._open(project_id)
        if not confirm:
            raise TeamError(
                "CONFIRM_REQUIRED",
                "Confirm the copy: it becomes a new version of your Solo strategy.",
            )
        version = await self._team_version(project, version_id)
        solo = await self._research.add_version(
            project["solo_strategy_id"],
            version["spec"],
            origin="team",
            note=f"From team project {project_id} (team v{version['number']}), reviewed by you",
            parent_id=project["solo_version_id"],
        )
        added = next(v for v in solo["versions"] if v["spec_sha256"] == version["spec_sha256"])
        await self.store.audit(
            project_id,
            "owner",
            "return_to_solo",
            {"team_version_id": version_id, "solo_version_id": added["id"]},
        )
        await self._emit("A team version was copied to your Solo QuantLab", project_id)
        return {"solo_strategy": solo, "solo_version_id": added["id"]}

    # agent tasks ------------------------------------------------------------------------------

    async def ask(
        self, project_id: str, agent: str, kind: str, run_id: str | None = None
    ) -> dict[str, Any]:
        """Give one bounded task to a robot — only within the project's grants."""
        project = await self._require(project_id, agent, kind)
        run = await self._baseline(project, run_id)
        if (agent, kind) == ("cipher", "improve"):
            ready, reason = self._ai_ready()
            if not ready:
                raise TeamError("NO_AI_ROUTE", reason, status=409)
        task_id = "tt-" + new_id()[:12]
        await self.store.add_task(
            {
                "id": task_id,
                "project_id": project_id,
                "agent": agent,
                "kind": kind,
                "state": "QUEUED",
                "request": {"run_id": run["id"], "version_id": run["version_id"]},
            }
        )
        await self.store.audit(
            project_id, "owner", "ask", {"task_id": task_id, "agent": agent, "kind": kind}
        )
        if (agent, kind) == ("sentinel", "challenge"):
            self._spawn(task_id, self._challenge(task_id, run["id"]))
        else:
            await self._improve(task_id, project, run)
        await self._emit(f"{agent.upper()}: {TASK_LABEL[(agent, kind)]}", project_id)
        task = await self.store.task(task_id)
        assert task is not None
        return task

    async def _require(self, project_id: str, agent: str, kind: str) -> dict[str, Any]:
        """The server-side gate for every agent action in a team workspace."""
        needs = NEEDS.get((agent, kind))
        if needs is None:
            raise TeamError("UNKNOWN_TASK", f"{agent.upper()} has no “{kind}” task here.", 404)
        project = await self._open(project_id)
        missing = [g for g in needs if not project["grants"].get(g)]
        if missing:
            raise TeamError(
                "NOT_GRANTED",
                f"{agent.upper()} needs access you haven't granted: "
                + ", ".join(GRANTS[g].lower() for g in missing)
                + ".",
                status=403,
            )
        return project

    async def _baseline(self, project: dict[str, Any], run_id: str | None) -> dict[str, Any]:
        runs = [
            r
            for r in await self._research.runs()
            if r["strategy_id"] == project["team_strategy_id"] and r["status"] == "COMPLETED"
        ]
        if run_id:
            run = next((r for r in runs if r["id"] == run_id), None)
            if run is None:
                raise TeamError(
                    "NOT_IN_PROJECT", "That run isn't a completed run of this team project.", 403
                )
        else:
            if not runs:
                raise TeamError(
                    "NO_RUN", "Run a team backtest first — the robots work on its results.", 409
                )
            run = runs[0]
        return run

    async def _challenge(self, task_id: str, run_id: str) -> None:
        """SENTINEL's independent audit of a team run (deterministic, no model)."""
        await self.store.update_task(task_id, state="RUNNING", started_at=utcnow().isoformat())
        try:
            audit = await self._missions.audit_run(run_id)
        except Exception as exc:
            log.exception("team challenge failed")
            await self.store.update_task(
                task_id, state="FAILED", error=str(exc)[:300], finished_at=utcnow().isoformat()
            )
            return
        checks = audit.get("checks") or []
        result = {
            "passed": bool(audit.get("passed")),
            "sha256": audit.get("sha256"),
            "second_engine": audit.get("second_engine"),
            "failed": [c for c in checks if c.get("result") == "FAIL"],
            "warnings": [c for c in checks if c.get("result") == "WARN"],
            "checked": len(checks),
        }
        await self.store.update_task(
            task_id, state="COMPLETE", result=result, finished_at=utcnow().isoformat()
        )
        task = await self.store.task(task_id)
        if task:
            await self._emit("SENTINEL finished its challenge", task["project_id"])

    async def _improve(self, task_id: str, project: dict[str, Any], run: dict[str, Any]) -> None:
        """CIPHER's improvement loop = the QuantLab evolution engine on the *team* family."""
        from jarvis.quantlab.ideas import evolution

        try:
            mission = await evolution.create_from_baseline(
                self._missions,
                strategy_id=project["team_strategy_id"],
                version_id=run["version_id"],
                dataset_id=run["dataset_id"],
                run_id=run["id"],
                title=f"Team · {project['name']}",
                # A bounded task: one round of at most three variants; the owner asks again.
                budget={"max_iterations": 1, "max_variants": 3},
                project_id=project["id"],
            )
        except Exception as exc:
            code = getattr(exc, "code", "FAILED")
            await self.store.update_task(
                task_id, state="FAILED", error=f"{code}: {getattr(exc, 'message', str(exc))}"[:300],
                finished_at=utcnow().isoformat(),
            )  # fmt: skip
            raise TeamError(code, getattr(exc, "message", str(exc)), 409) from exc
        await self.store.update_task(
            task_id,
            state="RUNNING",
            mission_id=mission["id"],
            started_at=utcnow().isoformat(),
        )

    async def sync_tasks(self) -> None:
        """Mirror the evolution missions' end state into their team tasks."""
        for task in await self.store.tasks_in("RUNNING"):
            if task["agent"] != "cipher" or not task.get("mission_id"):
                continue
            mission = await self._missions.store.get(task["mission_id"])
            if mission is None or mission["state"] not in (
                "COMPLETE",
                "FAILED",
                "CANCELED",
                "BLOCKED",
                "WAITING_USER",
            ):
                continue
            state = {"COMPLETE": "COMPLETE", "WAITING_USER": "COMPLETE"}.get(
                mission["state"], "FAILED"
            )
            trials = await self._missions.store.trials(mission_id=mission["id"])
            await self.store.update_task(
                task["id"],
                state=state,
                result={"mission_state": mission["state"], "trials": len(trials)},
                error=mission.get("error") if state == "FAILED" else None,
                finished_at=utcnow().isoformat(),
            )

    # views ------------------------------------------------------------------------------------

    async def projects(self) -> list[dict[str, Any]]:
        await self.sync_tasks()
        return [await self._view(p) for p in await self.store.projects()]

    async def project(self, project_id: str) -> dict[str, Any]:
        await self.sync_tasks()
        project = await self.store.project(project_id)
        if project is None:
            raise TeamError("NOT_FOUND", "No such team project.", 404)
        return await self._view(project)

    async def _view(self, project: dict[str, Any]) -> dict[str, Any]:
        team = await self._research.strategy(project["team_strategy_id"])
        runs = [
            r
            for r in await self._research.runs()
            if r["strategy_id"] == project["team_strategy_id"]
        ]
        tasks = await self.store.tasks(project["id"])
        missions = []
        for t in tasks:
            if t.get("mission_id"):
                m = await self._missions.store.get(t["mission_id"])
                if m:
                    trials = await self._missions.store.trials(mission_id=m["id"])
                    missions.append(
                        {
                            "id": m["id"],
                            "state": m["state"],
                            "stage": m["stage"],
                            "title": m["title"],
                            "trials": trials,
                        }
                    )
        return {
            **project,
            "grant_labels": GRANTS,
            "team_strategy": {
                "id": team["id"],
                "name": team["name"],
                "versions": [
                    {
                        k: v[k]
                        for k in (
                            "id",
                            "number",
                            "origin",
                            "note",
                            "created_at",
                            "spec_sha256",
                            "parent_id",
                            "state",
                        )
                    }
                    for v in team["versions"]
                ],
            },
            "runs": runs[:20],
            "tasks": tasks,
            "missions": missions,
            "audit": await self.store.audit_log(project["id"]),
        }

    # helpers ----------------------------------------------------------------------------------

    async def _open(self, project_id: str) -> dict[str, Any]:
        project = await self.store.project(project_id)
        if project is None:
            raise TeamError("NOT_FOUND", "No such team project.", 404)
        if project["state"] != "ACTIVE":
            raise TeamError("CLOSED", "This team project is closed.", 409)
        return project

    async def _team_version(self, project: dict[str, Any], version_id: str) -> dict[str, Any]:
        team = await self._research.strategy(project["team_strategy_id"])
        version: dict[str, Any] | None = next(
            (v for v in team["versions"] if v["id"] == version_id), None
        )
        if version is None:
            raise TeamError(
                "NOT_IN_PROJECT",
                "That version isn't part of this team project (Solo versions stay private).",
                403,
            )
        return version

    def _spawn(self, key: str, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._running[key] = task
        task.add_done_callback(lambda _t: self._running.pop(key, None))

    async def _emit(self, message: str, project_id: str) -> None:
        from jarvis.events.types import EventType

        await self._bus.emit(
            EventType.TEAM_WORKSPACE, message=message, payload={"project_id": project_id}
        )
