"""QuantLab Data Vault and Backtest Lab endpoints (local data first).

Backtests (``POST /quantlab/backtests``) read only local data: no route here reaches a
market-data provider. Imports are local file operations. The one paid path — Databento
batch acquisition — lives in ``quantlab_acquire_routes.py`` behind a quote, an explicit
approval (``confirm: true``) and the offline switch; no JARVIS or ULTRON tool calls it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from jarvis.quantlab.futures.service import ResearchError
from jarvis.quantlab.vault.instruments import markets
from jarvis.quantlab.vault.lab import LabError
from jarvis.quantlab.vault.service import VaultError
from jarvis.runtime import Runtime

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]


def _error(exc: VaultError | LabError | ResearchError) -> HTTPException:
    return HTTPException(exc.status, exc.as_dict())


class PathImport(BaseModel):
    path: str = Field(min_length=2, max_length=1000)


class Mapping(BaseModel):
    root: Literal["NQ", "MNQ", "ES", "MES"] | None = None
    timestamp: str | None = Field(None, max_length=120)
    date: str | None = Field(None, max_length=120)
    time: str | None = Field(None, max_length=120)
    timezone: str | None = Field(None, max_length=64)
    timestamp_marks: Literal["start", "end"] = "start"
    open: str | None = Field(None, max_length=120)
    high: str | None = Field(None, max_length=120)
    low: str | None = Field(None, max_length=120)
    close: str | None = Field(None, max_length=120)
    volume: str | None = Field(None, max_length=120)
    contract: str | None = Field(None, max_length=120)
    contract_mode: Literal["column", "single", "none"] | None = None
    single_contract: str | None = Field(None, max_length=20)
    price_scale: Literal["decimal", "fixed_1e9"] = "decimal"


class OfflineBody(BaseModel):
    enabled: bool


class BackupBody(BaseModel):
    location: str = Field(min_length=2, max_length=1000)
    confirm: bool = False


class RebuildBody(BaseModel):
    dataset_id: str | None = Field(None, max_length=40)


class AuditBody(BaseModel):
    repair: bool = False


class Advanced(BaseModel):
    start: str | None = Field(None, min_length=10, max_length=10)
    end: str | None = Field(None, min_length=10, max_length=10)
    cost_profile: Literal["intraday_conservative", "intraday_stress"] | None = None
    kind: Literal["backtest", "validation"] | None = None
    contracts: int | None = Field(None, ge=1, le=100)
    account_capital: float | None = Field(None, gt=0, le=1e9)
    direction: Literal["long", "short", "both"] | None = None
    commission_per_contract_side: float | None = Field(None, ge=0, le=100)
    exchange_fees_per_contract_side: float | None = Field(None, ge=0, le=100)
    slippage_ticks: int | None = Field(None, ge=0, le=50)
    limit_fill: Literal["trade_through", "touch"] | None = None


class BacktestBody(BaseModel):
    strategy_id: str | None = Field(None, max_length=60)
    version_id: str | None = Field(None, max_length=60)
    symbol: Literal["NQ", "MNQ", "ES", "MES"]
    timeframe: str = Field(min_length=1, max_length=4)
    advanced: Advanced = Field(default_factory=Advanced)


# -- Data Vault ---------------------------------------------------------------------------


@router.get("/quantlab/data/vault")
async def vault_overview(rt: RuntimeDep) -> dict[str, Any]:
    return await rt.vault.overview()


@router.get("/quantlab/data/coverage")
async def coverage(
    rt: RuntimeDep,
    symbol: Annotated[str, Query(min_length=1, max_length=20)],
    schema: Annotated[str | None, Query(max_length=20)] = None,
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
) -> dict[str, Any]:
    return await rt.vault.coverage(symbol, schema, start, end)


def _header(request: Request, name: str, limit: int) -> str | None:
    value = request.headers.get(name)
    if value is None:
        return None
    from urllib.parse import unquote

    return unquote(value)[:limit]


@router.post("/quantlab/data/imports")
async def import_upload(request: Request, rt: RuntimeDep) -> dict[str, Any]:
    """Stage an uploaded file (raw body, name in ``x-filename``): preview, nothing stored yet."""
    filename = _header(request, "x-filename", 255) or "upload"

    async def body() -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            yield chunk

    try:
        return await rt.vault.stage_upload(filename, body())
    except VaultError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/imports/path")
async def import_path(body: PathImport, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.vault.stage_path(body.path)
    except VaultError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/imports/{import_id}/commit")
async def import_commit(import_id: str, body: Mapping, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.vault.commit_import(import_id, body.model_dump(exclude_none=True))
    except VaultError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/imports")
async def imports(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.vault.imports()


@router.post("/quantlab/data/rebuild-derived")
async def rebuild_derived(body: RebuildBody, rt: RuntimeDep) -> dict[str, Any]:
    return await rt.vault.rebuild_derived(body.dataset_id)


@router.post("/quantlab/data/vault/audit")
async def vault_audit(body: AuditBody, rt: RuntimeDep) -> dict[str, Any]:
    return await rt.vault.audit(repair=body.repair)


@router.post("/quantlab/data/vault/offline")
async def vault_offline(body: OfflineBody, rt: RuntimeDep) -> dict[str, Any]:
    return await rt.vault.set_offline(body.enabled)


@router.post("/quantlab/data/vault/backups")
async def vault_backup(body: BackupBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.vault.backup(body.location, body.confirm)
    except VaultError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/vault/restore")
async def vault_restore(body: BackupBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.vault.restore(body.location, body.confirm)
    except VaultError as exc:
        raise _error(exc) from None


# -- One-time Databento batch acquisition (the only paid path) ----------------------------


class AcquireBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset: str = Field("GLBX.MDP3", min_length=1, max_length=40)
    data_schema: str = Field("ohlcv-1m", alias="schema", min_length=1, max_length=20)
    stype_in: str = Field("continuous", min_length=1, max_length=20)
    symbols: list[str] = Field(min_length=1, max_length=4)
    start: str = Field(min_length=10, max_length=10)
    end: str = Field(min_length=10, max_length=10)
    include_definitions: bool = True

    def request(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "schema": self.data_schema,
            "stype_in": self.stype_in,
            "symbols": self.symbols,
            "start": self.start,
            "end": self.end,
            "include_definitions": self.include_definitions,
        }


class ApproveBody(BaseModel):
    acquisition_id: str = Field(min_length=3, max_length=40)
    request_hash: str = Field(min_length=64, max_length=64)
    max_usd: float = Field(gt=0, le=100_000)
    confirm: bool = False


class StartBody(BaseModel):
    acquisition_id: str = Field(min_length=3, max_length=40)


@router.post("/quantlab/data/quotes")
@router.post("/quantlab/data/acquisitions/prepare")
async def acquisition_prepare(body: AcquireBody, rt: RuntimeDep) -> dict[str, Any]:
    """Free: coverage diff + Databento metadata.get_cost for exactly the missing ranges."""
    try:
        return await rt.acquisitions.prepare(body.request())
    except VaultError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/acquisitions/approve")
async def acquisition_approve(body: ApproveBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.acquisitions.approve(
            body.acquisition_id, body.request_hash, body.max_usd, body.confirm
        )
    except VaultError as exc:
        raise _error(exc) from None


# The brief's ".../execute": named "start" because QuantLab exposes no route that reads as
# execution (Q-022 guard in tests/test_quantlab.py).
@router.post("/quantlab/data/acquisitions/start")
async def acquisition_start(body: StartBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.acquisitions.execute(body.acquisition_id)
    except VaultError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/acquisitions/{acquisition_id}/resume")
async def acquisition_resume(acquisition_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.acquisitions.resume(acquisition_id)
    except VaultError as exc:
        raise _error(exc) from None


@router.post("/quantlab/data/acquisitions/{acquisition_id}/abandon")
async def acquisition_abandon(acquisition_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.acquisitions.abandon(acquisition_id)
    except VaultError as exc:
        raise _error(exc) from None


@router.get("/quantlab/data/acquisitions")
async def acquisitions(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.acquisitions.acquisitions()


@router.get("/quantlab/data/acquisitions/{acquisition_id}")
async def acquisition(acquisition_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.acquisitions.get(acquisition_id)
    except VaultError as exc:
        raise _error(exc) from None


# -- Backtest Lab ---------------------------------------------------------------------------


@router.get("/quantlab/instruments")
async def instruments() -> list[dict[str, Any]]:
    return markets()


@router.get("/quantlab/strategies")
async def strategies(rt: RuntimeDep) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = (await rt.backtests.options())["strategies"]
    return options


@router.get("/quantlab/backtest/options")
async def backtest_options(rt: RuntimeDep) -> dict[str, Any]:
    return await rt.backtests.options()


@router.get("/quantlab/backtest/readiness")
async def backtest_readiness(
    rt: RuntimeDep,
    symbol: Annotated[str, Query(min_length=1, max_length=10)],
    version_id: Annotated[str | None, Query(max_length=60)] = None,
    start: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
    end: Annotated[str | None, Query(min_length=10, max_length=10)] = None,
) -> dict[str, Any]:
    try:
        return await rt.backtests.readiness(symbol, version_id, start, end)
    except LabError as exc:
        raise _error(exc) from None


@router.post("/quantlab/backtests")
async def backtest_start(body: BacktestBody, rt: RuntimeDep) -> dict[str, Any]:
    """Local only: never a provider request, never a data fee."""
    try:
        return await rt.backtests.launch(body.model_dump())
    except (LabError, ResearchError) as exc:
        raise _error(exc) from None


@router.get("/quantlab/backtests/{run_id}")
async def backtest_run(run_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        return await rt.research.run(run_id)
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/backtests/{run_id}/trades")
async def backtest_trades(
    run_id: str,
    rt: RuntimeDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    try:
        return await rt.research.trades(run_id, offset, limit)
    except ResearchError as exc:
        raise _error(exc) from None


@router.get("/quantlab/backtests/{run_id}/validation")
async def backtest_validation(run_id: str, rt: RuntimeDep) -> dict[str, Any]:
    try:
        row = await rt.research.run(run_id)
    except ResearchError as exc:
        raise _error(exc) from None
    summary = row.get("summary") or {}
    return {
        "status": row["status"],
        "verdict": summary.get("verdict"),
        "tests": summary.get("tests") or [],
        "audit": summary.get("audit"),
        "fitness": summary.get("fitness"),
        "timeframe": summary.get("timeframe"),
        "binding": summary.get("binding"),
    }
