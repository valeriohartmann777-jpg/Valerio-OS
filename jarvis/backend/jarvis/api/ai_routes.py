"""AI routing & billing endpoints (Settings → AI & Billing).

Paid API fallback is approved only through ``POST /ai/paid`` with ``confirm: true`` and
explicit caps; no agent or tool can call it. The API key is accepted once, verified with a
free model check, stored in the OS keystore and never returned — only its last four
characters appear anywhere.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.core.connector import check_format, normalize_key
from jarvis.llm.base import ModelError
from jarvis.runtime import Runtime

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]


def _invalid(exc: Exception) -> HTTPException:
    return HTTPException(422, {"code": "invalid", "message": str(exc)})


def _model_error(exc: ModelError) -> HTTPException:
    return HTTPException(
        422, {"code": exc.code, "message": exc.message, "suggestion": exc.suggestion}
    )


class StrategyBody(BaseModel):
    strategy: Literal["subscription_first", "plan_only", "api_only"]
    profile: Literal["economy", "balanced", "deep"] | None = None


class PlanBody(BaseModel):
    enabled: bool
    personal_use: bool = False


class TerminalBody(BaseModel):
    action: Literal["login", "install"]


class PaidBody(BaseModel):
    monthly_budget_usd: float = Field(gt=0, le=5000)
    per_mission_cap_usd: float = Field(gt=0, le=5000)
    warn_at: list[int] = Field(default_factory=lambda: [50, 80, 100])
    max_concurrent: int = Field(1, ge=1, le=8)
    stop_at_budget: bool = True
    confirm: Literal[True]


class KeyBody(BaseModel):
    api_key: str = Field(min_length=1, max_length=400)


class LocalBody(BaseModel):
    enabled: bool
    base_url: str = Field("http://127.0.0.1:11434", max_length=200)
    model: str = Field("", max_length=120)


class CurrencyBody(BaseModel):
    display: Literal["USD", "CHF"]
    usd_to_chf: float | None = Field(None, gt=0, lt=10)


@router.get("/ai/status")
async def status(rt: RuntimeDep) -> dict[str, Any]:
    return await rt.ai.snapshot()


@router.get("/ai/usage")
async def usage(rt: RuntimeDep) -> dict[str, Any]:
    return {
        "calls": await rt.ai.store.recent_usage(100),
        "events": await rt.ai.store.events(100),
        "approvals": await rt.ai.store.approvals(50),
    }


@router.post("/ai/strategy")
async def strategy(body: StrategyBody, rt: RuntimeDep) -> dict[str, Any]:
    await rt.ai.set_strategy(body.strategy, body.profile)
    return await rt.ai.snapshot()


@router.post("/ai/plan")
async def plan(body: PlanBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        if body.enabled:
            await rt.ai.enable_plan(body.personal_use)
        else:
            await rt.ai.disable_plan()
    except ValueError as exc:
        raise _invalid(exc) from exc
    return await rt.ai.snapshot()


@router.post("/ai/plan/check")
async def plan_check(rt: RuntimeDep) -> dict[str, Any]:
    await rt.ai.check("plan")
    return await rt.ai.snapshot()


@router.post("/ai/plan/test")
async def plan_test(rt: RuntimeDep) -> dict[str, Any]:
    """One tiny real call on the Claude plan (uses a little plan usage; never the API)."""
    result = await rt.ai.test_plan()
    return {"result": result, "status": await rt.ai.snapshot()}


@router.post("/ai/plan/terminal")
async def plan_terminal(body: TerminalBody, rt: RuntimeDep) -> dict[str, str]:
    command = await rt.ai.open_terminal(body.action)
    return {"command": command}


@router.post("/ai/paid")
async def paid(body: PaidBody, rt: RuntimeDep) -> dict[str, Any]:
    terms = body.model_dump(exclude={"confirm"})
    try:
        await rt.ai.approve_paid(terms, body.confirm)
    except ValueError as exc:
        raise _invalid(exc) from exc
    return await rt.ai.snapshot()


@router.post("/ai/paid/disable")
async def paid_disable(rt: RuntimeDep) -> dict[str, Any]:
    await rt.ai.disable_paid()
    return await rt.ai.snapshot()


@router.post("/ai/api-key")
async def api_key(body: KeyBody, rt: RuntimeDep) -> dict[str, Any]:
    key = normalize_key(body.api_key)
    try:
        check_format(key)
        await rt.ai.connect_key(key)
    except ModelError as exc:
        raise _model_error(exc) from exc
    await rt.refresh_brain()
    return await rt.ai.snapshot()


@router.delete("/ai/api-key")
async def api_key_delete(rt: RuntimeDep) -> dict[str, Any]:
    try:
        await rt.ai.remove_key()
    except ModelError as exc:
        raise _model_error(exc) from exc
    await rt.refresh_brain()
    return await rt.ai.snapshot()


@router.post("/ai/api/check")
async def api_check(rt: RuntimeDep) -> dict[str, Any]:
    await rt.ai.check("api")
    return await rt.ai.snapshot()


@router.post("/ai/local")
async def local(body: LocalBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        await rt.ai.set_local(body.enabled, body.base_url, body.model)
    except ValueError as exc:
        raise _invalid(exc) from exc
    return await rt.ai.snapshot()


@router.post("/ai/local/check")
async def local_check(rt: RuntimeDep) -> dict[str, Any]:
    await rt.ai.check("local")
    return await rt.ai.snapshot()


@router.post("/ai/currency")
async def currency(body: CurrencyBody, rt: RuntimeDep) -> dict[str, Any]:
    try:
        await rt.ai.set_currency(body.display, body.usd_to_chf)
    except ValueError as exc:
        raise _invalid(exc) from exc
    return await rt.ai.snapshot()
