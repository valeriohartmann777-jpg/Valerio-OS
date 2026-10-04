"""hide_application / quit_application on the simulated desktop."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jarvis.core.trace import TraceContext
from jarvis.runtime import Runtime
from jarvis.tools.base import ToolResult
from jarvis.tools.system.control import AppNameArgs
from jarvis.tools.system.simulated import SimulatedSystemBackend
from tests.conftest import eventually, make_settings


async def run(rt: Runtime, tool: str, args: dict[str, Any]) -> ToolResult:
    return await rt.executor.run(tool, args, ctx=TraceContext.new(), reason="test")


async def open_notepad(rt: Runtime) -> None:
    assert (await run(rt, "open_application", {"name": "notepad"})).success


async def test_hide_keeps_the_app_running(runtime: Runtime) -> None:
    await open_notepad(runtime)
    result = await run(runtime, "hide_application", {"name": "Notepad"})
    assert result.success, result.error
    assert result.observed_result == "Notepad is hidden"
    snapshot = await runtime.backend.snapshot()
    assert not [w for w in snapshot.windows if w.process_name == "notepad.exe"]
    assert [p for p in snapshot.processes if p.name == "notepad.exe"]
    tool = runtime.tools.get("hide_application")
    assert tool is not None
    verification = await tool.verify(AppNameArgs(name="Notepad"), result, TraceContext.new())
    assert verification is not None and verification.verified


async def test_quit_needs_approval_and_is_verified(runtime: Runtime) -> None:
    await open_notepad(runtime)
    task = asyncio.create_task(run(runtime, "quit_application", {"name": "notepad"}))
    await eventually(runtime.permissions.pending)
    [request] = runtime.permissions.pending()
    assert request.action.level == 2 and request.action.target == "Notepad"
    await runtime.permissions.approve(request.id)
    result = await task
    assert result.success and result.data["quit"] is True
    tool = runtime.tools.get("quit_application")
    assert tool is not None
    verification = await tool.verify(AppNameArgs(name="notepad"), result, TraceContext.new())
    assert verification is not None and verification.verified


async def test_an_app_asking_to_save_is_reported_honestly(runtime: Runtime) -> None:
    await open_notepad(runtime)
    assert isinstance(runtime.backend, SimulatedSystemBackend)
    runtime.backend.refuse_quit.add("notepad.exe")
    tool = runtime.tools.get("quit_application")
    assert tool is not None
    args = AppNameArgs(name="notepad")
    result = await tool.execute(args, TraceContext.new())  # approval tested above
    assert result.success and result.data["quit"] is False
    assert "may be asking to save" in (result.observed_result or "")
    verification = await tool.verify(args, result, TraceContext.new())
    assert verification is not None and verification.status == "unverifiable"


async def test_refusals_happen_before_any_approval(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"ui_pid": 1024})}
    )
    rt = Runtime(settings)  # the simulated JARVIS window runs as pid 1024
    await rt.start()
    try:
        cases = {
            ("hide_application", "spotify"): "not_running",
            ("quit_application", "explorer"): "protected",
            ("hide_application", "jarvis"): "self",
            ("quit_application", "JARVIS"): "self",
        }
        for (tool, name), code in cases.items():
            result = await run(rt, tool, {"name": name})
            assert result.error is not None and result.error.code == code, (tool, name)
        assert rt.permissions.pending() == []
    finally:
        await rt.stop()
