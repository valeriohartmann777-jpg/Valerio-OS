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
    strategy: str = Field("zone_reaction", min_length=3, max_length=40)


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


class MacroBody(BaseModel):
    csv: str = Field(min_length=10, max_length=2_000_000)
    source: str = Field("owner import", min_length=2, max_length=120)


@router.post("/apex/macro/import")
async def import_macro(body: MacroBody, rt: RuntimeDep) -> dict[str, Any]:
    """Import a macro calendar (CSV) with its schedule vintage; nothing is fetched online."""
    return await rt.apex.import_macro(body.csv, body.source)


@router.get("/apex/context")
async def context(market: str, rt: RuntimeDep, as_of: int | None = None) -> dict[str, Any]:
    """Macro events known at the moment, the blackout and related markets."""
    try:
        return await rt.apex.context(market, as_of)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/zones")
async def zones(market: str, rt: RuntimeDep, snapshot: str | None = None) -> dict[str, Any]:
    """A stored, immutable zone snapshot: four strongest supports and resistances as of then."""
    try:
        return await rt.apex.zones(market, snapshot)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/alerts")
async def alerts(
    rt: RuntimeDep, market: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Qualified paper alerts (immutable; at most four per market and trading day; paper only)."""
    try:
        return await rt.apex.alerts(market, limit=min(max(limit, 1), 500))
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/probability")
async def probability_models(rt: RuntimeDep, market: str | None = None) -> list[dict[str, Any]]:
    """Fitted P(TP before SL) heads with their grading (validated or not) — immutable."""
    try:
        return await rt.apex.probability_models(market)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/signals")
async def signals(
    rt: RuntimeDep, market: str | None = None, strategy: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """The immutable signal journal: one record per strategy decision, with its outcome."""
    try:
        return await rt.apex.signals(market, strategy=strategy, limit=min(max(limit, 1), 500))
    except ApexError as exc:
        raise _error(exc) from None


@router.post("/apex/journal/resolve")
async def resolve(rt: RuntimeDep, market: str | None = None) -> dict[str, Any]:
    return await rt.apex.resolve_open(market)


@router.post("/apex/evaluations")
async def evaluate(body: EvaluateBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.apex.evaluate(body.market, days=body.days, strategy=body.strategy)
    except ApexError as exc:
        raise _error(exc) from None


class ValidateBody(BaseModel):
    market: str = Field(min_length=2, max_length=10)
    strategy: str = Field(min_length=3, max_length=40)


@router.post("/apex/validations")
async def validate(body: ValidateBody, rt: RuntimeDep) -> dict[str, Any]:
    """Run the pre-registered validation protocol (background job; poll or listen)."""
    try:
        return await rt.apex.validate(body.market, body.strategy)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/validations")
async def validations(rt: RuntimeDep, market: str | None = None) -> list[dict[str, Any]]:
    try:
        return await rt.apex.validations(market)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/validations/{validation_id}")
async def validation(validation_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.apex.validation(validation_id)
    except ApexError as exc:
        raise _error(exc) from None


@router.post("/apex/validations/{validation_id}/cancel")
async def cancel_validation(validation_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.apex.cancel_validation(validation_id)
    except ApexError as exc:
        raise _error(exc) from None


@router.get("/apex/trials")
async def trials(market: str, strategy: str, rt: RuntimeDep) -> dict[str, Any]:
    """The append-only trial register of a market and strategy."""
    try:
        return await rt.apex.trials(market, strategy)
    except ApexError as exc:
        raise _error(exc) from None


class ForwardBody(BaseModel):
    enabled: bool


@router.get("/apex/forward")
async def forward(rt: RuntimeDep, market: str | None = None) -> dict[str, Any]:
    """The paper forward log: on/off, last round per market, live evidence per strategy."""
    try:
        return await rt.apex.forward(market)
    except ApexError as exc:
        raise _error(exc) from None


@router.post("/apex/forward")
async def set_forward(body: ForwardBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.apex.set_forward(body.enabled)
    except ApexError as exc:
        raise _error(exc) from None


@router.post("/apex/forward/run")
async def forward_run(rt: RuntimeDep) -> dict[str, Any]:
    """One forward round now (markets outside their session are skipped)."""
    await rt.apex.forward_tick()
    return await rt.apex.forward()


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


@router.get("/apex/fitness/{market}")
async def fitness(market: str, rt: RuntimeDep) -> dict[str, Any]:
    """Data fitness of the series an analysis would use now (coverage, gaps, spikes, side)."""
    try:
        return await rt.apex.fitness(market)
    except ApexError as exc:
        raise _error(exc) from None


@router.post("/apex/data/{market}/repair")
async def repair(market: str, rt: RuntimeDep) -> dict[str, Any]:
    """Re-check every trading day without candles and download what is missing (free data)."""
    try:
        return await rt.apex.repair(market)
    except ApexError as exc:
        raise _error(exc) from None
