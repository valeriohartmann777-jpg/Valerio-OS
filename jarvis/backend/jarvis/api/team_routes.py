"""Team ULTRON (JARVIS 2.0): the HQ projection and the ULTRON QuantLab team workspaces."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.feedback.service import FeedbackError
from jarvis.quantlab.futures.service import ResearchError
from jarvis.quantlab.vault.lab import LabError
from jarvis.runtime import Runtime
from jarvis.team.workspace import TeamError

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]


@router.get("/team/hq")
async def hq(rt: RuntimeDep) -> dict[str, Any]:
    """Nine robots and five rooms, derived from persisted tasks (no simulated activity)."""
    return await rt.team_hq.snapshot()


# -- ULTRON QuantLab team workspaces -------------------------------------------------------


class ShareBody(BaseModel):
    version_id: str = Field(min_length=3, max_length=60)
    grants: dict[str, bool] = Field(default_factory=dict)
    confirm: bool = False
    name: str | None = Field(None, max_length=120)


class GrantsBody(BaseModel):
    grants: dict[str, bool]
    confirm: bool = False


class TeamBacktestBody(BaseModel):
    version_id: str | None = Field(None, max_length=60)
    symbol: Literal["NQ", "MNQ", "ES", "MES"]
    timeframe: str = Field(min_length=1, max_length=4)
    advanced: dict[str, Any] = Field(default_factory=dict)


class AskBody(BaseModel):
    agent: Literal["sentinel", "cipher"]
    kind: Literal["challenge", "improve"]
    run_id: str | None = Field(None, max_length=60)


class ReturnBody(BaseModel):
    version_id: str = Field(min_length=3, max_length=60)
    confirm: bool = False


def _team_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TeamError):
        return HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        )
    if isinstance(exc, LabError):
        return HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message, "suggestion": exc.remedy or None},
        )
    if isinstance(exc, ResearchError):
        return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)})
    raise exc


@router.get("/team/quantlab/projects")
async def projects(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.team.projects()


@router.post("/team/quantlab/projects")
async def share(body: ShareBody, rt: RuntimeDep) -> dict[str, Any]:
    """The owner's deliberate "Share with ULTRON": the team gets a copy, Solo stays private."""
    try:
        return await rt.team.share(body.version_id, body.grants, body.confirm, body.name)
    except (TeamError, ResearchError) as exc:
        raise _team_error(exc) from None


@router.get("/team/quantlab/projects/{project_id}")
async def project(project_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.team.project(project_id)
    except TeamError as exc:
        raise _team_error(exc) from None


@router.post("/team/quantlab/projects/{project_id}/grants")
async def grants(project_id: str, body: GrantsBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.team.set_grants(project_id, body.grants, body.confirm)
    except TeamError as exc:
        raise _team_error(exc) from None


@router.post("/team/quantlab/projects/{project_id}/backtests")
async def team_backtest(project_id: str, body: TeamBacktestBody, rt: RuntimeDep) -> dict[str, Any]:
    """Same BacktestLab and Data Vault as Solo; local data only, never a provider request."""
    try:
        return await rt.team.backtest(project_id, body.model_dump())
    except (TeamError, LabError, ResearchError) as exc:
        raise _team_error(exc) from None


@router.post("/team/quantlab/projects/{project_id}/ask")
async def ask(project_id: str, body: AskBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.team.ask(project_id, body.agent, body.kind, body.run_id)
    except TeamError as exc:
        raise _team_error(exc) from None


@router.post("/team/quantlab/projects/{project_id}/return")
async def return_to_solo(project_id: str, body: ReturnBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.team.return_to_solo(project_id, body.version_id, body.confirm)
    except (TeamError, ResearchError) as exc:
        raise _team_error(exc) from None


@router.post("/team/quantlab/projects/{project_id}/close")
async def close(project_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.team.close(project_id)
    except TeamError as exc:
        raise _team_error(exc) from None


# -- Training Hub feedback ------------------------------------------------------------------


class FeedbackBody(BaseModel):
    event_id: str = Field(min_length=3, max_length=80)
    rating: Literal[1, -1]
    note: str | None = Field(None, max_length=1000)


@router.get("/feedback")
async def feedback(rt: RuntimeDep, limit: int = 100) -> dict[str, Any]:
    """The owner's ratings of JARVIS replies, newest first, with totals per route."""
    return await rt.feedback.overview(limit)


@router.post("/feedback")
async def rate_reply(body: FeedbackBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.feedback.rate(body.event_id, body.rating, body.note)
    except FeedbackError as exc:
        raise _feedback_error(exc) from exc


# -- Training Hub evaluations ---------------------------------------------------------------


class PinBody(BaseModel):
    event_id: str = Field(min_length=3, max_length=80)


def _feedback_error(exc: FeedbackError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@router.get("/training/evals")
async def evals(rt: RuntimeDep) -> dict[str, Any]:
    """Curated and pinned behaviour cases with the last stored run."""
    return await rt.evals.overview()


@router.post("/training/evals/run")
async def run_evals(rt: RuntimeDep) -> dict[str, Any]:
    """Check every case against the local command router (offline, no side effects)."""
    return await rt.evals.run()


@router.post("/training/evals/cases")
async def pin_case(body: PinBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.evals.pin(body.event_id)
    except FeedbackError as exc:
        raise _feedback_error(exc) from exc


@router.delete("/training/evals/cases/{case_id}")
async def unpin_case(case_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.evals.unpin(case_id)
    except FeedbackError as exc:
        raise _feedback_error(exc) from exc
