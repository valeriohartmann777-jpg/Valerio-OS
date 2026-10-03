"""HTTP + WebSocket endpoints. Thin: translate requests into service calls."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from jarvis.agents.base import AgentState
from jarvis.api.schemas import (
    ApproveRequest,
    ChatAccepted,
    ChatRequest,
    Health,
    LevelPolicy,
    MissionCreate,
    RejectRequest,
    SettingsView,
    Snapshot,
    SystemStatus,
)
from jarvis.events.types import Event, Severity
from jarvis.missions.engine import MissionError
from jarvis.missions.models import Mission
from jarvis.permissions.models import LEVEL_LABELS, PermissionRequest
from jarvis.permissions.service import ApprovalError, StrongConfirmationRequired
from jarvis.runtime import Runtime
from jarvis.storage.audit import AuditEntry
from jarvis.tools.base import ToolSpec

router = APIRouter()


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


async def build_snapshot(rt: Runtime) -> Snapshot:
    return Snapshot(
        version=rt.version,
        state=rt.state.snapshot(),
        system_backend=rt.backend.name,
        simulated=rt.backend.simulated,
        context=rt.context.current,
        agents=rt.agents.list(),
        missions=await rt.missions.list(limit=20),
        approvals=rt.permissions.pending(),
        activity=await rt.events.recent(limit=150),
    )


@router.get("/health")
async def health(rt: RuntimeDep) -> Health:
    return Health(version=rt.version, uptime_seconds=round(rt.uptime_seconds, 1))


@router.get("/system/status")
async def system_status(rt: RuntimeDep) -> SystemStatus:
    missions = await rt.missions.list(limit=20)
    return SystemStatus(
        state=rt.state.snapshot(),
        version=rt.version,
        uptime_seconds=round(rt.uptime_seconds, 1),
        system_backend=rt.backend.name,
        simulated=rt.backend.simulated,
        clients=rt.bus.stream_count,
        pending_approvals=len(rt.permissions.pending()),
        active_missions=sum(1 for m in missions if not m.is_terminal),
    )


@router.get("/snapshot")
async def snapshot(rt: RuntimeDep) -> Snapshot:
    return await build_snapshot(rt)


@router.post("/chat", status_code=202)
async def chat(body: ChatRequest, rt: RuntimeDep) -> ChatAccepted:
    ctx = rt.core.submit(body.text.strip())
    return ChatAccepted(trace_id=ctx.trace_id)


@router.get("/missions")
async def list_missions(
    rt: RuntimeDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[Mission]:
    return await rt.missions.list(limit=limit)


@router.post("/missions", status_code=202)
async def create_mission(body: MissionCreate, rt: RuntimeDep) -> Mission:
    try:
        return await rt.core.create_mission(body.goal)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/missions/{mission_id}")
async def get_mission(mission_id: str, rt: RuntimeDep) -> Mission:
    mission = await rt.missions.get(mission_id)
    if mission is None:
        raise HTTPException(404, "Mission not found")
    return mission


@router.post("/missions/{mission_id}/{action}")
async def control_mission(mission_id: str, action: str, rt: RuntimeDep) -> Mission:
    controls = {"pause": rt.missions.pause, "resume": rt.missions.resume, "stop": rt.missions.stop}
    if action not in controls:
        raise HTTPException(404, f"Unknown mission action {action!r}")
    try:
        return await controls[action](mission_id)
    except MissionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/agents")
async def agents(rt: RuntimeDep) -> list[AgentState]:
    return rt.agents.list()


@router.get("/tools")
async def tools(rt: RuntimeDep) -> list[ToolSpec]:
    return rt.tools.specs()


@router.get("/permissions")
async def pending_permissions(rt: RuntimeDep) -> list[PermissionRequest]:
    return rt.permissions.pending()


@router.post("/permissions/{request_id}/approve")
async def approve(request_id: str, body: ApproveRequest, rt: RuntimeDep) -> PermissionRequest:
    try:
        return await rt.permissions.approve(
            request_id, strong_confirmation=body.strong_confirmation
        )
    except StrongConfirmationRequired as exc:
        raise HTTPException(409, str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/permissions/{request_id}/reject")
async def reject(request_id: str, body: RejectRequest, rt: RuntimeDep) -> PermissionRequest:
    try:
        return await rt.permissions.reject(request_id, note=body.note or "Rejected by user.")
    except ApprovalError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/activity")
async def activity(
    rt: RuntimeDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    min_severity: Severity = Severity.INFO,
) -> list[Event]:
    events = await rt.events.recent(limit=limit)
    return [e for e in events if e.at_least(min_severity)]


@router.get("/audit")
async def audit(
    rt: RuntimeDep, limit: Annotated[int, Query(ge=1, le=1000)] = 100
) -> list[AuditEntry]:
    return await rt.audit.recent(limit=limit)


@router.get("/settings")
async def settings_view(rt: RuntimeDep) -> SettingsView:
    s = rt.settings
    return SettingsView(
        version=rt.version,
        system_backend=rt.backend.name,
        simulated=rt.backend.simulated,
        personality_name=s.personality.name,
        personality_traits=s.personality.traits,
        permission_levels=[
            LevelPolicy(level=int(level), label=LEVEL_LABELS[level], policy=policy)
            for level, policy in sorted(s.permissions.levels.items())
        ],
        tool_overrides=s.permissions.tool_overrides,
        disabled_categories=s.permissions.disabled_categories,
        approval_timeout_seconds=s.permissions.approval_timeout_seconds,
        models={
            role: f"{cfg.provider}:{cfg.model}" if cfg.model else cfg.provider
            for role, cfg in (
                ("fast", s.models.fast),
                ("reasoning", s.models.reasoning),
                ("vision", s.models.vision),
                ("embedding", s.models.embedding),
            )
        },
        known_apps=sorted(app.name for app in rt.catalog.known_apps()),
        config_dir=str(s.root_dir / "config"),
        database_path=str(s.database_path),
    )


@router.websocket("/events")
async def events(ws: WebSocket) -> None:
    rt: Runtime = ws.app.state.runtime
    stream = rt.bus.open_stream()  # open first so nothing is missed while snapshotting
    await ws.accept()

    async def pump() -> None:
        async for event in stream:
            await ws.send_json({"kind": "event", "data": event.model_dump(mode="json")})

    async def listen() -> None:
        while True:
            message = await ws.receive_json()
            if isinstance(message, dict) and message.get("kind") == "ping":
                await ws.send_json({"kind": "pong"})

    tasks: list[asyncio.Task[None]] = []
    try:
        snapshot = await build_snapshot(rt)
        await ws.send_json({"kind": "snapshot", "data": snapshot.model_dump(mode="json")})
        tasks = [asyncio.create_task(pump()), asyncio.create_task(listen())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        stream.close()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
