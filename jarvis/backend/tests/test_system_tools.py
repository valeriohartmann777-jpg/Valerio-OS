from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.trace import TraceContext
from jarvis.events.types import EventType
from jarvis.permissions.models import PermissionLevel
from jarvis.runtime import Runtime
from jarvis.settings import AppCatalogSettings, AppEntry
from jarvis.tools.base import Tool, ToolResult
from jarvis.tools.system.apps import AppCatalog, LaunchOutcome, analyze_launch
from jarvis.tools.system.backend import EnvironmentSnapshot, ProcessInfo, WindowInfo
from jarvis.tools.system.simulated import SimulatedSystemBackend
from jarvis.tools.system.tools import NoArgs, OpenApplicationArgs, OpenApplicationTool
from tests.conftest import Recorder

NOTEPAD = frozenset({"notepad.exe"})


def snap(windows: list[WindowInfo], processes: list[ProcessInfo]) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(windows=tuple(windows), processes=tuple(processes))


def window(handle: int, pid: int, name: str, *, fg: bool = False) -> WindowInfo:
    return WindowInfo(
        handle=handle, title=f"{name} window", pid=pid, process_name=name, is_foreground=fg
    )


# --- catalog & launch analysis (platform independent) -------------------------


def test_catalog_aliases_unknown_and_denylist() -> None:
    catalog = AppCatalog(
        AppCatalogSettings(
            applications={
                "notepad": AppEntry(
                    name="Notepad",
                    aliases=["editor"],
                    launch=["notepad.exe"],
                    processes=["Notepad.exe"],
                )
            },
            denylist=["diskpart"],
        )
    )
    known = catalog.resolve("Editor")
    assert known is not None and known.known and known.processes == NOTEPAD
    unknown = catalog.resolve("blender")
    assert unknown is not None and not unknown.known
    assert unknown.level is PermissionLevel.MODIFICATION
    assert unknown.launch == ("blender.exe",)
    assert catalog.resolve("..\\evil") is None
    assert catalog.resolve("two words") is None
    assert catalog.is_denied("DiskPart.exe")
    assert catalog.display_name("notepad.exe") == "Notepad"
    assert catalog.display_name("code.exe") == "Code"


def test_analyze_new_window() -> None:
    before = snap([window(1, 10, "explorer.exe")], [ProcessInfo(10, "explorer.exe")])
    after = snap(
        [window(1, 10, "explorer.exe"), window(2, 20, "notepad.exe", fg=True)],
        [ProcessInfo(10, "explorer.exe"), ProcessInfo(20, "notepad.exe")],
    )
    evidence = analyze_launch(before, after, NOTEPAD)
    assert evidence.outcome is LaunchOutcome.WINDOW_OPENED
    assert evidence.confirmed
    assert evidence.window is not None and evidence.window.pid == 20


def test_analyze_existing_window_brought_to_front() -> None:
    before = snap([window(2, 20, "notepad.exe")], [ProcessInfo(20, "notepad.exe")])
    after = snap([window(2, 20, "notepad.exe", fg=True)], [ProcessInfo(20, "notepad.exe")])
    evidence = analyze_launch(before, after, NOTEPAD)
    assert evidence.outcome is LaunchOutcome.BROUGHT_TO_FRONT and evidence.confirmed


def test_analyze_process_without_window_and_nothing() -> None:
    empty = snap([], [])
    started = snap([], [ProcessInfo(20, "notepad.exe")])
    assert analyze_launch(empty, started, NOTEPAD).outcome is LaunchOutcome.PROCESS_STARTED
    assert not analyze_launch(empty, started, NOTEPAD).confirmed
    assert analyze_launch(empty, empty, NOTEPAD).outcome is LaunchOutcome.NOT_DETECTED


def test_analyze_already_running_in_background() -> None:
    running = snap([window(2, 20, "notepad.exe")], [ProcessInfo(20, "notepad.exe")])
    assert analyze_launch(running, running, NOTEPAD).outcome is LaunchOutcome.ALREADY_RUNNING


# --- tools through the executor (simulated backend) ---------------------------


async def run(rt: Runtime, tool: str, args: dict[str, Any] | None = None) -> ToolResult:
    return await rt.executor.run(tool, args or {}, ctx=TraceContext.new(), reason="test")


async def test_open_application_observes_and_verifies(runtime: Runtime, recorder: Recorder) -> None:
    result = await run(runtime, "open_application", {"name": "notepad"})
    assert result.success and result.simulated
    assert result.data["outcome"] == "window_opened"
    assert result.observed_result == "Notepad window detected — “Untitled - Notepad”"
    assert any("New window" in o for o in result.observations)
    assert recorder.types()[-2:] == [EventType.TOOL_STARTED, EventType.TOOL_COMPLETED]

    tool = runtime.tools.get("open_application")
    assert isinstance(tool, OpenApplicationTool)
    verification = await tool.verify(
        OpenApplicationArgs(name="notepad"), result, TraceContext.new()
    )
    assert verification.status == "verified"

    assert isinstance(runtime.backend, SimulatedSystemBackend)
    runtime.backend.close_all("notepad.exe")
    verification = await tool.verify(
        OpenApplicationArgs(name="notepad"), result, TraceContext.new()
    )
    assert verification.status == "failed"


async def test_unknown_and_denied_apps_fail_before_approval(
    runtime: Runtime, recorder: Recorder
) -> None:
    missing = await run(runtime, "open_application", {"name": "blender"})
    denied = await run(runtime, "open_application", {"name": "diskpart"})
    assert missing.error is not None and missing.error.code == "app_not_found"
    assert missing.error.suggestion
    assert denied.error is not None and denied.error.code == "denied"
    assert not recorder.of(EventType.PERMISSION_REQUESTED)
    assert not recorder.of(EventType.TOOL_STARTED)


async def test_invalid_arguments_are_structured(runtime: Runtime) -> None:
    result = await run(runtime, "open_application", {})
    assert not result.success and result.error is not None
    assert result.error.code == "invalid_arguments"


async def test_read_only_tools(runtime: Runtime) -> None:
    info = await run(runtime, "get_system_info")
    assert info.success and 0 <= info.data["memory_percent"] <= 100
    active = await run(runtime, "get_active_window")
    assert active.success and active.data["window"]["title"] == "JARVIS"
    apps = await run(runtime, "list_running_apps")
    assert apps.success and apps.data["apps"][0]["focused"]


async def test_audit_log_records_every_execution(runtime: Runtime) -> None:
    await run(runtime, "open_application", {"name": "notepad"})
    await run(runtime, "open_application", {"name": "diskpart"})
    entries = await runtime.audit.recent()
    assert [e.approval for e in entries] == ["not_requested", "auto"]
    assert entries[1].success and entries[1].simulated
    assert entries[1].arguments == {"name": "notepad"}


class _Slow(Tool[NoArgs]):
    name = "slow"
    description = "Sleeps."
    permission_level = PermissionLevel.READ
    input_model = NoArgs

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")


class _Broken(Tool[NoArgs]):
    name = "broken"
    description = "Crashes."
    permission_level = PermissionLevel.READ
    input_model = NoArgs

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        raise ValueError("secret internals")


async def test_timeouts_and_crashes_become_structured_errors(runtime: Runtime) -> None:
    runtime.tools.register(_Slow())
    runtime.tools.register(_Broken())
    runtime.executor._timeout = 0.05
    slow = await run(runtime, "slow")
    broken = await run(runtime, "broken")
    assert slow.error is not None and slow.error.code == "timeout"
    assert broken.error is not None and broken.error.code == "internal_error"
    assert "secret internals" not in broken.error.message
    assert broken.error.detail and "secret internals" in broken.error.detail
