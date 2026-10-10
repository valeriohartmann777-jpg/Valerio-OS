"""QuantLab Data Hub endpoints: provider connection, catalog, quotes, approvals, cache.

The API key goes in through ``POST /quantlab/connections/databento`` and never
comes back out: responses carry a four-character hint at most. A billable
download can only start from ``POST /quantlab/data/requests`` with an explicit
``confirm: true`` for a specific open quote — a route the Origin guard limits to
the JARVIS app, and one no JARVIS or ULTRON tool calls.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from jarvis.quantlab.hub.service import HubError
from jarvis.runtime import Runtime

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]

PRODUCTS = [
    {"root": "NQ", "name": "E-mini Nasdaq-100", "engine": True},
    {"root": "MNQ", "name": "Micro E-mini Nasdaq-100", "engine": True},
    {"root": "ES", "name": "E-mini S&P 500", "engine": True},
    {"root": "MES", "name": "Micro E-mini S&P 500", "engine": True},
    {"root": "YM", "name": "E-mini Dow", "engine": False},
    {"root": "RTY", "name": "E-mini Russell 2000", "engine": False},
    {"root": "CL", "name": "Crude Oil", "engine": False},
    {"root": "GC", "name": "Gold", "engine": False},
]


class ConnectBody(BaseModel):
    # SecretStr: a validation error never echoes the key back.
    api_key: SecretStr


class QuoteBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset: str = Field(min_length=1, max_length=40)
    data_schema: str = Field(alias="schema", min_length=1, max_length=40)
    stype_in: str = Field(min_length=1, max_length=20)
    symbols: list[str] = Field(min_length=1, max_length=5)
    start: str = Field(min_length=10, max_length=10)
    end: str = Field(min_length=10, max_length=10)
    include_definitions: bool = True


class ApprovalBody(BaseModel):
    quote_id: str = Field(min_length=3, max_length=40)
    max_budget_usd: float = Field(ge=0, le=10_000)
    confirm: Literal[True]


class DatasetBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset: str = Field(min_length=1, max_length=40)
    data_schema: str = Field("ohlcv-1m", alias="schema")
    stype_in: str = Field(min_length=1, max_length=20)
    symbol: str = Field(min_length=1, max_length=40)
    start: str = Field(min_length=10, max_length=10)
    end: str = Field(min_length=10, max_length=10)


class CapsBody(BaseModel):
    per_request_usd: float = Field(ge=0, le=10_000)
    per_month_usd: float = Field(ge=0, le=100_000)


def _error(exc: HubError) -> HTTPException:
    return HTTPException(exc.status, exc.as_dict())


@router.get("/quantlab/connections/databento/status")
async def hub_status(rt: RuntimeDep) -> dict[str, Any]:
    return await rt.hub.status()


@router.post("/quantlab/connections/databento")
async def hub_connect(body: ConnectBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.connect(body.api_key.get_secret_value())
    except HubError as exc:
        raise _error(exc) from None


@router.post("/quantlab/connections/databento/test")
async def hub_test(rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.test()
    except HubError as exc:
        raise _error(exc) from None


@router.delete("/quantlab/connections/databento")
async def hub_disconnect(rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.disconnect()
    except HubError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/catalog")
async def hub_catalog(rt: RuntimeDep, dataset: str | None = None) -> dict[str, Any]:
    status = await rt.hub.status()
    out: dict[str, Any] = {"datasets": status["datasets"], "products": PRODUCTS, "info": None}
    if dataset:
        try:
            out["info"] = await rt.hub.dataset_info(dataset)
        except HubError as exc:
            raise _error(exc) from None
    return out


@router.get("/quantlab/data/instruments")
async def hub_instruments(
    rt: RuntimeDep,
    dataset: str,
    symbols: Annotated[str, Query(max_length=200)],
    stype_in: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    try:
        return await rt.hub.resolve(
            dataset, [s for s in symbols.split(",") if s.strip()], stype_in, start, end
        )
    except HubError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/quote")
async def hub_quote(body: QuoteBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.quote(body.model_dump(by_alias=True))
    except HubError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/quotes")
async def hub_quotes(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.hub.quotes()


@router.get("/quantlab/data/quotes/{quote_id}")
async def hub_quote_view(quote_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.quote_view(quote_id)
    except HubError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/quotes/{quote_id}/reject")
async def hub_reject(quote_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.reject(quote_id)
    except HubError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/requests")
async def hub_approve(body: ApprovalBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.approve(body.quote_id, body.max_budget_usd)
    except HubError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/jobs")
async def hub_jobs(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.hub.jobs()


@router.get("/quantlab/data/jobs/{job_id}")
async def hub_job(job_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.job(job_id)
    except HubError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/jobs/{job_id}/cancel")
async def hub_cancel(job_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.cancel(job_id)
    except HubError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/cache")
async def hub_cache(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.hub.cache_summary()


@router.get("/quantlab/data/datasets")
async def hub_datasets(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.hub.datasets()


@router.post("/quantlab/data/datasets")
async def hub_build(body: DatasetBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.build_dataset(body.model_dump(by_alias=True))
    except HubError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/datasets/{dataset_id}")
async def hub_dataset(dataset_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.dataset(dataset_id)
    except HubError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/datasets/{dataset_id}/preview")
async def hub_preview(dataset_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.hub.preview(dataset_id)
    except HubError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/settings")
async def hub_caps(rt: RuntimeDep) -> dict[str, float]:
    return await rt.hub.caps()


@router.post("/quantlab/data/settings")
async def hub_set_caps(body: CapsBody, rt: RuntimeDep) -> dict[str, float]:
    try:
        return await rt.hub.set_caps(body.per_request_usd, body.per_month_usd)
    except HubError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/audit")
async def hub_audit(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.hub.audit_log()
