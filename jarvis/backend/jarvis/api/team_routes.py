"""Team ULTRON (JARVIS 2.0): the HQ projection — where each robot is and why."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from jarvis.runtime import Runtime

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]


@router.get("/team/hq")
async def hq(rt: RuntimeDep) -> dict[str, Any]:
    """Nine robots and five rooms, derived from persisted tasks (no simulated activity)."""
    return await rt.team_hq.snapshot()
