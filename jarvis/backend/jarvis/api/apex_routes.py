"""JARVIS APEX: analyses, the candidate journal and track records (research only)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.apex.service import ApexError
from jarvis.runtime import Runtime

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]


def _error(exc: ApexError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message, "suggestion": exc.remedy or None},
    )


class AnalyzeBody(BaseModel):
    market: str = Field(min_length=2, max_length=10)
    # Replay: the analysis as it would have been at this moment (epoch seconds, UTC).
    as_of: int | None = Field(None, ge=1_500_000_000)
    refresh: bool = False  # fetch missing free Dukascopy days first


class EvaluateBody(BaseModel):
    market: str = Field(min_length=2, max_length=10)
    days: int | None = Field(None, ge=10, le=730)


@router.get("/apex/markets")
async def markets(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.apex.markets()


@router.post("/apex/analyze")
async def analyze(body: AnalyzeBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.apex.analyze(body.market, as_of=body.as_of, refresh=body.refresh)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/analyses")
async def analyses(rt: RuntimeDep, market: str | None = None) -> list[dict[str, Any]]:
    return await rt.apex.store.analyses(market)


@router.get("/apex/analyses/{analysis_id}")
async def analysis(analysis_id: str, rt: RuntimeDep) -> dict[str, Any]:
    found = await rt.apex.store.analysis(analysis_id)
    if found is None:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "No such analysis."})
    return found


@router.post("/apex/analyses/{analysis_id}/audit")
async def audit(analysis_id: str, rt: RuntimeDep) -> dict[str, Any]:
    """SENTINEL recomputes the analysis from today's data and re-judges its candidates."""
    try:
        return await rt.apex.audit(analysis_id)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/audits")
async def audits(rt: RuntimeDep, market: str | None = None) -> list[dict[str, Any]]:
    return await rt.apex.store.audits(market)


@router.get("/apex/journal")
async def journal(rt: RuntimeDep, market: str | None = None, limit: int = 100) -> dict[str, Any]:
    return await rt.apex.journal(market, min(max(limit, 1), 500))


@router.post("/apex/journal/resolve")
async def resolve(rt: RuntimeDep, market: str | None = None) -> dict[str, Any]:
    return await rt.apex.resolve_open(market)


@router.post("/apex/evaluations")
async def evaluate(body: EvaluateBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.apex.evaluate(body.market, days=body.days)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/evaluations")
async def evaluations(rt: RuntimeDep, market: str | None = None) -> list[dict[str, Any]]:
    return await rt.apex.evaluations(market)


@router.get("/apex/evaluations/{evaluation_id}")
async def evaluation(evaluation_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.apex.evaluation(evaluation_id)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/analyses/{analysis_id}/chart")
async def chart(
    analysis_id: str, rt: RuntimeDep, minutes: int = 15, limit: int = 300
) -> dict[str, Any]:
    """The analysis' bars at another timeframe — only what was known at its moment."""
    try:
        return await rt.apex.chart(analysis_id, minutes, limit)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/data/{market}")
async def data_status(market: str, rt: RuntimeDep) -> dict[str, Any]:
    """Coverage, suspicious days, update reports and concrete causes with repair actions."""
    try:
        return await rt.apex.data_status(market)
    except ApexError as exc:
        raise _error(exc) from None


@router.post("/apex/data/{market}/repair")
async def repair(market: str, rt: RuntimeDep) -> dict[str, Any]:
    """Re-check every trading day without candles and download what is missing (free data)."""
    try:
        return await rt.apex.repair(market)
    except ApexError as exc:
        raise _error(exc) from None
