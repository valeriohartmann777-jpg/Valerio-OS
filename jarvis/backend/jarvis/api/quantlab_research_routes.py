"""QuantLab futures research endpoints: strategy specs, runs, ledgers, validation, reports.

Research only: nothing here can place an order or reach a broker.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from jarvis.quantlab.futures.architect import ArchitectError
from jarvis.quantlab.futures.service import ResearchError
from jarvis.runtime import Runtime

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]


class SpecBody(BaseModel):
    spec: dict[str, Any]
    note: str | None = Field(None, max_length=1000)
    parent_id: str | None = Field(None, max_length=40)


class RunBody(BaseModel):
    version_id: str = Field(min_length=3, max_length=40)
    dataset_id: str = Field(min_length=3, max_length=40)
    kind: Literal["backtest", "validation"] = "backtest"
    include_holdout: bool = False
    confirm_holdout: bool = False


class InterpretBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    current: dict[str, Any] | None = None


class NoteBody(BaseModel):
    kind: Literal["note", "decision"] = "note"
    text: str = Field(min_length=1, max_length=4000)
    run_id: str | None = Field(None, max_length=40)


def _error(exc: ResearchError) -> HTTPException:
    return HTTPException(exc.status, exc.as_dict())


@router.get("/quantlab/research/overview")
async def research_overview(rt: RuntimeDep) -> dict[str, Any]:
    return {**await rt.research.overview(), "hub": await rt.hub.overview()}


@router.get("/quantlab/research/templates")
async def research_templates(rt: RuntimeDep) -> list[dict[str, Any]]:
    return rt.research.templates()


@router.post("/quantlab/research/strategies/check")
async def research_check(body: SpecBody, rt: RuntimeDep) -> dict[str, Any]:
    return rt.research.check(body.spec)


@router.post("/quantlab/research/interpret")
async def research_interpret(body: InterpretBody, rt: RuntimeDep) -> dict[str, Any]:
    """A draft spec from words. Nothing is saved or run; the user reviews it first."""
    try:
        return await rt.architect.interpret(body.text, body.current)
    except ArchitectError as exc:
        status = 409 if exc.code == "NO_MODEL" else 400
        raise HTTPException(
            status, {"code": exc.code, "message": exc.message, "remedy": exc.remedy}
        ) from None


@router.post("/quantlab/research/strategies/ai")
async def research_create_ai(body: SpecBody, rt: RuntimeDep) -> dict[str, Any]:
    """Save a reviewed AI draft: origin 'ai', so the trial registry counts it as a variant."""
    try:
        if body.parent_id:
            strategy = await rt.research.version_owner(body.parent_id)
            return await rt.research.add_version(
                strategy, body.spec, origin="ai", note=body.note, parent_id=body.parent_id
            )
        return await rt.research.create_strategy(body.spec, origin="ai", note=body.note)
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/research/strategies")
async def research_strategies(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.research.strategies()


@router.post("/quantlab/research/strategies")
async def research_create(body: SpecBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.create_strategy(body.spec, note=body.note)
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/research/strategies/{strategy_id}")
async def research_strategy(strategy_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.strategy(strategy_id)
    except ResearchError as exc:
        raise _error(exc) from None


@router.post("/quantlab/research/strategies/{strategy_id}/versions")
async def research_version(strategy_id: str, body: SpecBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.add_version(
            strategy_id, body.spec, note=body.note, parent_id=body.parent_id
        )
    except ResearchError as exc:
        raise _error(exc) from None


@router.post("/quantlab/research/strategies/{strategy_id}/notes")
async def research_note(strategy_id: str, body: NoteBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.add_note(strategy_id, body.kind, body.text, body.run_id)
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/research/runs")
async def research_runs(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.research.runs()


@router.post("/quantlab/research/runs")
async def research_start(body: RunBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.start_run(
            body.version_id,
            body.dataset_id,
            kind=body.kind,
            include_holdout=body.include_holdout,
            confirm_holdout=body.confirm_holdout,
        )
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/research/compare")
async def research_compare(
    rt: RuntimeDep, ids: Annotated[str, Query(max_length=300)]
) -> dict[str, Any]:
    try:
        return await rt.research.compare([i for i in ids.split(",") if i.strip()])
    except ResearchError as exc:
        raise _error(exc) from None


@router.post("/quantlab/stop-all")
async def quantlab_stop_all(rt: RuntimeDep) -> dict[str, Any]:
    """Stop every research run and data download (charges already incurred stay)."""
    stopped = await rt.research.stop_all()
    jobs = []
    for job in await rt.hub.jobs():
        if job["status"] in ("QUEUED", "RUNNING"):
            await rt.hub.cancel(job["id"])
            jobs.append(job["id"])
    return {**stopped, "downloads": jobs}


@router.get("/quantlab/research/runs/{run_id}")
async def research_run(run_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.run(run_id)
    except ResearchError as exc:
        raise _error(exc) from None


@router.post("/quantlab/research/runs/{run_id}/cancel")
async def research_cancel(run_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.cancel(run_id)
    except ResearchError as exc:
        raise _error(exc) from None


@router.post("/quantlab/research/runs/{run_id}/reproduce")
async def research_reproduce(run_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.reproduce(run_id)
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/research/runs/{run_id}/trades")
async def research_trades(
    run_id: str,
    rt: RuntimeDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    segment: str | None = None,
) -> dict[str, Any]:
    try:
        return await rt.research.trades(run_id, offset, limit, segment)
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/research/runs/{run_id}/trades/{number}")
async def research_trade(
    run_id: str, number: int, rt: RuntimeDep, segment: str | None = None
) -> dict[str, Any]:
    try:
        return await rt.research.trade(run_id, number, segment)
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/research/runs/{run_id}/chart")
async def research_chart(run_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.chart(run_id)
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/research/runs/{run_id}/report", response_class=PlainTextResponse)
async def research_report(run_id: str, rt: RuntimeDep) -> str:
    try:
        return await rt.research.report(run_id)
    except ResearchError as exc:
        raise _error(exc) from None
