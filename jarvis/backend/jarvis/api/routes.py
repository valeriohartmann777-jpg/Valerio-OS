"""HTTP + WebSocket endpoints. Thin: translate requests into service calls."""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal

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
    ApiKeyRequest,
    ApproveRequest,
    BrainView,
    BriefingPreferences,
    BriefingView,
    ChatAccepted,
    ChatRequest,
    Health,
    LearningFocus,
    LearningView,
    LevelPolicy,
    MemoryCreate,
    MemoryView,
    MissionCreate,
    RejectRequest,
    SettingsView,
    Snapshot,
    SystemStatus,
    VoicePreferences,
    VoiceView,
)
from jarvis.briefing.service import BriefingError
from jarvis.events.types import Event, Severity
from jarvis.llm.base import ModelError
from jarvis.memory.store import MemoryRefused
from jarvis.missions.engine import MissionError
from jarvis.missions.models import Mission
from jarvis.permissions.models import LEVEL_LABELS, PermissionRequest
from jarvis.permissions.service import ApprovalError, StrongConfirmationRequired
from jarvis.runtime import Runtime
from jarvis.storage.audit import AuditEntry
from jarvis.tools.base import ToolSpec
from jarvis.voice.elevenlabs import VoiceError

router = APIRouter()


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


def brain_view(rt: Runtime) -> BrainView:
    status = rt.brain.status
    return BrainView(
        available=status.available,
        fast_model=status.fast_model,
        reasoning_model=status.reasoning_model,
        reason=status.reason,
        key_hint=status.key_hint,
    )


def voice_view(rt: Runtime) -> VoiceView:
    return VoiceView.model_validate(asdict(rt.voice.status))


def memory_views(rt: Runtime) -> list[MemoryView]:
    return [MemoryView.model_validate(asdict(m)) for m in rt.memory.items]


def learning_view(rt: Runtime) -> LearningView:
    return LearningView.model_validate(asdict(rt.learning.status))


def _voice_error(exc: VoiceError, status: int = 422) -> HTTPException:
    return HTTPException(
        status, {"code": exc.code, "message": exc.message, "suggestion": exc.suggestion}
    )


async def build_snapshot(rt: Runtime) -> Snapshot:
    return Snapshot(
        version=rt.version,
        build=rt.build,
        brain=brain_view(rt),
        voice=voice_view(rt),
        learning=learning_view(rt),
        memories=memory_views(rt),
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
    return Health(
        version=rt.version,
        build=rt.build,
        uptime_seconds=round(rt.uptime_seconds, 1),
        pid=os.getpid(),
        system_backend=rt.backend.name,
    )


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


@router.post("/brain/key")
async def connect_brain(body: ApiKeyRequest, rt: RuntimeDep) -> BrainView:
    """Verify an Anthropic API key, store it in jarvis/.env and switch the brain on."""
    try:
        await rt.connector.connect(body.api_key)
    except ModelError as exc:
        raise HTTPException(
            422, {"code": exc.code, "message": exc.message, "suggestion": exc.suggestion}
        ) from exc
    return brain_view(rt)


@router.get("/voice")
async def voice(rt: RuntimeDep) -> VoiceView:
    return voice_view(rt)


@router.post("/voice/key")
async def connect_voice(body: ApiKeyRequest, rt: RuntimeDep) -> VoiceView:
    """Verify an ElevenLabs key, store it in jarvis/.env and switch voice on."""
    try:
        await rt.voice.connect(body.api_key)
    except VoiceError as exc:
        raise _voice_error(exc) from exc
    return voice_view(rt)


@router.post("/voice/preferences")
async def voice_preferences(body: VoicePreferences, rt: RuntimeDep) -> VoiceView:
    await rt.voice.set_preferences(wake_word=body.wake_word, speak_replies=body.speak_replies)
    return voice_view(rt)


@router.post("/voice/listen", status_code=202)
async def voice_listen(rt: RuntimeDep) -> VoiceView:
    """Push-to-talk: record one spoken request now."""
    try:
        await rt.voice.listen()
    except VoiceError as exc:
        raise _voice_error(exc, 409) from exc
    return voice_view(rt)


@router.post("/voice/stop")
async def voice_stop(rt: RuntimeDep) -> VoiceView:
    rt.voice.stop_activity()
    return voice_view(rt)


@router.post("/voice/test")
async def voice_test(rt: RuntimeDep) -> VoiceView:
    try:
        await rt.voice.say("Hello. This is how I sound.")
    except VoiceError as exc:
        raise _voice_error(exc, 409) from exc
    return voice_view(rt)


@router.get("/briefing")
async def briefing(rt: RuntimeDep) -> BriefingView:
    return BriefingView.model_validate(asdict(rt.briefing.status))


@router.post("/briefing/preferences")
async def briefing_preferences(body: BriefingPreferences, rt: RuntimeDep) -> BriefingView:
    try:
        status = await rt.briefing.set_preferences(enabled=body.enabled, at=body.time)
    except BriefingError as exc:
        raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from exc
    return BriefingView.model_validate(asdict(status))


@router.post("/briefing/send")
async def briefing_send(rt: RuntimeDep) -> dict[str, str]:
    return {"text": await rt.briefing.send()}


@router.get("/memory")
async def memories(rt: RuntimeDep) -> list[MemoryView]:
    return memory_views(rt)


@router.post("/memory")
async def add_memory(body: MemoryCreate, rt: RuntimeDep) -> list[MemoryView]:
    try:
        await rt.memory.remember(body.text, body.kind, source="dashboard")
    except MemoryRefused as exc:
        raise HTTPException(422, {"code": "refused", "message": str(exc)}) from exc
    return memory_views(rt)


@router.delete("/memory/{number}")
async def forget_memory(number: int, rt: RuntimeDep) -> list[MemoryView]:
    try:
        await rt.memory.forget(number)
    except MemoryRefused as exc:
        raise HTTPException(404, {"code": "not_found", "message": str(exc)}) from exc
    return memory_views(rt)


@router.get("/learning")
async def learning(rt: RuntimeDep) -> LearningView:
    return learning_view(rt)


@router.post("/learning/start")
async def learning_start(rt: RuntimeDep) -> LearningView:
    await rt.learning.enable()
    return learning_view(rt)


@router.post("/learning/stop")
async def learning_stop(rt: RuntimeDep) -> LearningView:
    await rt.learning.disable()
    return learning_view(rt)


@router.post("/learning/focus")
async def learning_focus(body: LearningFocus, rt: RuntimeDep) -> LearningView:
    await rt.learning.set_focus(body.text)
    return learning_view(rt)


@router.get("/learning/studies")
async def learning_studies(
    rt: RuntimeDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[dict[str, Any]]:
    return await rt.learning_journal.studies(limit=limit)


@router.get("/learning/tests")
async def learning_tests(
    rt: RuntimeDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
    status: Annotated[
        Literal["invalid", "rejected", "oos_failed", "validated"] | None, Query()
    ] = None,
) -> list[dict[str, Any]]:
    return await rt.learning_journal.tests(limit=limit, before=before, status=status)


@router.get("/learning/notes")
async def learning_notes(rt: RuntimeDep) -> list[dict[str, Any]]:
    return await rt.learning_journal.notes()


@router.get("/learning/rounds")
async def learning_rounds(
    rt: RuntimeDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[dict[str, Any]]:
    return await rt.learning_journal.rounds(limit=limit)


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


@router.get("/conversation")
async def conversation(
    rt: RuntimeDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: Annotated[str | None, Query(max_length=40)] = None,
) -> list[Event]:
    """The chat history: commands (typed or spoken) and JARVIS's replies."""
    if before is not None:
        try:  # clients send JSON timestamps ("…Z"); stored ones are isoformat ("…+00:00")
            before = datetime.fromisoformat(before).isoformat()
        except ValueError as exc:
            raise HTTPException(422, "before must be an ISO timestamp") from exc
    return await rt.events.conversation(limit=limit, before=before)


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
        brain=brain_view(rt),
        known_apps=sorted(app.name for app in rt.catalog.known_apps()),
        file_roots=[rt.files.display(root) for root in rt.files.roots],
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
