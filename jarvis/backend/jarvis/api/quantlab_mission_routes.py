"""QuantLab research missions: Idea-to-Edge from a source to a verdict, and bounded evolution.

Purchases: ``POST …/data-approval`` needs ``confirm: true`` and a maximum; it approves the
mission's one open quote through the Data Hub (caps, re-pricing, single use) and is never
called by JARVIS or ULTRON tools. The holdout lock needs ``confirm: true`` as well.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from jarvis.quantlab.ideas.missions import MissionError
from jarvis.runtime import Runtime

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]


def _error(exc: MissionError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": exc.message})


class MissionBody(BaseModel):
    source_id: str = Field(min_length=5, max_length=40)
    budget: dict[str, float] | None = None


class EvolveBody(BaseModel):
    budget: dict[str, float] | None = None


class AnswersBody(BaseModel):
    accept_defaults: bool = False
    choices: dict[str, str] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)


class DataApproval(BaseModel):
    max_usd: float = Field(gt=0, le=500)
    confirm: Literal[True]


class LockBody(BaseModel):
    version_id: str = Field(min_length=3, max_length=40)
    confirm: Literal[True]


@router.get("/quantlab/research/missions")
async def missions(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.quant_missions.missions()


@router.post("/quantlab/research/missions")
async def create_mission(body: MissionBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.quant_missions.create_from_source(body.source_id, body.budget)
    except MissionError as exc:
        raise _error(exc) from exc


@router.post("/quantlab/strategies/from-source/{source_id}")
async def from_source(source_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.quant_missions.create_from_source(source_id)
    except MissionError as exc:
        raise _error(exc) from exc


@router.get("/quantlab/research/agents")
async def research_agents(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.quant_missions.agents()


@router.get("/quantlab/research/activity")
async def research_activity(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.quant_missions.activity()


@router.get("/quantlab/research/missions/{mission_id}")
async def mission(mission_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.quant_missions.mission(mission_id)
    except MissionError as exc:
        raise _error(exc) from exc


@router.get("/quantlab/research/missions/{mission_id}/agents")
async def mission_agents(mission_id: str, rt: RuntimeDep) -> list[dict[str, Any]]:
    try:
        m = await rt.quant_missions.mission(mission_id)
    except MissionError as exc:
        raise _error(exc) from exc
    roster = await rt.quant_missions.agents()
    for agent in roster:
        mine = [t for t in m["tasks"] if t["agent"] == agent["id"]]
        agent["mission_tasks"] = [
            {
                k: t[k]
                for k in (
                    "id",
                    "kind",
                    "objective",
                    "state",
                    "cost_usd",
                    "started_at",
                    "finished_at",
                )
            }
            for t in mine
        ]
    return roster


@router.get("/quantlab/research/missions/{mission_id}/trials")
async def mission_trials(mission_id: str, rt: RuntimeDep) -> list[dict[str, Any]]:
    try:
        trials: list[dict[str, Any]] = (await rt.quant_missions.mission(mission_id))["trials"]
    except MissionError as exc:
        raise _error(exc) from exc
    return trials


@router.get("/quantlab/research/missions/{mission_id}/verdict")
async def mission_verdict(mission_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        m = await rt.quant_missions.mission(mission_id)
    except MissionError as exc:
        raise _error(exc) from exc
    return {"verdict": m["verdict"], "detail": m["refs"].get("verdict"), "state": m["state"]}


@router.get("/quantlab/research/missions/{mission_id}/dossier")
async def mission_dossier(mission_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.quant_missions.dossier(mission_id)
    except MissionError as exc:
        raise _error(exc) from exc


@router.get("/quantlab/research/missions/{mission_id}/dossier.md")
async def mission_dossier_md(mission_id: str, rt: RuntimeDep) -> PlainTextResponse:
    try:
        dossier = await rt.quant_missions.dossier(mission_id)
    except MissionError as exc:
        raise _error(exc) from exc
    return PlainTextResponse(dossier["markdown"], media_type="text/markdown")


@router.post("/quantlab/research/missions/{mission_id}/answers")
async def answers(mission_id: str, body: AnswersBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.quant_missions.answer(mission_id, body.model_dump())
    except MissionError as exc:
        raise _error(exc) from exc


@router.post("/quantlab/research/missions/{mission_id}/data-approval")
async def data_approval(mission_id: str, body: DataApproval, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.quant_missions.approve_data(mission_id, body.max_usd)
    except MissionError as exc:
        raise _error(exc) from exc


@router.post("/quantlab/research/missions/{mission_id}/data-decline")
async def data_decline(mission_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.quant_missions.decline_data(mission_id)
    except MissionError as exc:
        raise _error(exc) from exc


@router.post("/quantlab/research/missions/{mission_id}/evolution")
async def evolve(mission_id: str, body: EvolveBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.quant_missions.evolve(mission_id, body.budget)
    except MissionError as exc:
        raise _error(exc) from exc


@router.post("/quantlab/research/missions/{mission_id}/holdout-lock")
async def lock(mission_id: str, body: LockBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.quant_missions.lock_candidate(mission_id, body.version_id, body.confirm)
    except MissionError as exc:
        raise _error(exc) from exc


# Generic controls last: specific routes above must match first.
@router.post("/quantlab/research/missions/{mission_id}/{action}")
async def control(mission_id: str, action: str, rt: RuntimeDep) -> dict[str, Any]:
    handlers = {
        "pause": rt.quant_missions.pause,
        "resume": rt.quant_missions.resume,
        "cancel": rt.quant_missions.cancel,
    }
    if action == "evolve":
        try:
            return await rt.quant_missions.evolve(mission_id, None)
        except MissionError as exc:
            raise _error(exc) from exc
    if action not in handlers:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Unknown action."})
    try:
        return await handlers[action](mission_id)
    except MissionError as exc:
        raise _error(exc) from exc
