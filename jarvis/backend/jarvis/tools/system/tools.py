"""System tools: observe the desktop and open applications."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import ActionDescriptor, PermissionLevel
from jarvis.tools.base import Tool, ToolError, ToolResult, Verification
from jarvis.tools.system.apps import (
    AppCatalog,
    AppTarget,
    LaunchEvidence,
    LaunchOutcome,
    analyze_launch,
    matching_processes,
    matching_windows,
)
from jarvis.tools.system.backend import (
    EnvironmentSnapshot,
    LaunchError,
    SystemBackend,
    WindowInfo,
)
from jarvis.tools.system.metrics import read_metrics


class NoArgs(BaseModel):
    pass


def _window_data(window: WindowInfo | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return {"title": window.title, "process": window.process_name, "pid": window.pid}


class GetSystemInfoTool(Tool[NoArgs]):
    name = "get_system_info"
    description = "Report host, CPU, memory and uptime."
    permission_level = PermissionLevel.READ
    input_model = NoArgs

    def __init__(self, backend: SystemBackend) -> None:
        self._backend = backend

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        metrics = read_metrics()
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=f"CPU {metrics.cpu_percent:.0f}% · memory {metrics.memory_percent:.0f}%",
            data={**metrics.as_dict(), "system_backend": self._backend.name},
            simulated=False,  # metrics are always real, even with a simulated desktop
        )


class GetActiveWindowTool(Tool[NoArgs]):
    name = "get_active_window"
    description = "Report the window that currently has focus."
    permission_level = PermissionLevel.READ
    input_model = NoArgs

    def __init__(self, backend: SystemBackend, catalog: AppCatalog) -> None:
        self._backend = backend
        self._catalog = catalog

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        window = await self._backend.active_window()
        if window is None:
            return ToolResult(
                success=True,
                tool=self.name,
                action=self.name,
                summary="No active window",
                data={"window": None},
                simulated=self._backend.simulated,
            )
        app = self._catalog.display_name(window.process_name)
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            target=app,
            summary=f"{app} — {window.title}",
            data={"app": app, "window": _window_data(window)},
            simulated=self._backend.simulated,
        )


class ListRunningAppsTool(Tool[NoArgs]):
    name = "list_running_apps"
    description = "List applications that currently have visible windows."
    permission_level = PermissionLevel.READ
    input_model = NoArgs

    def __init__(self, backend: SystemBackend, catalog: AppCatalog) -> None:
        self._backend = backend
        self._catalog = catalog

    async def execute(self, args: NoArgs, ctx: TraceContext) -> ToolResult:
        snapshot = await self._backend.snapshot()
        grouped: dict[str, list[WindowInfo]] = defaultdict(list)
        for window in snapshot.windows:
            grouped[window.process_name].append(window)
        apps = sorted(
            (
                {
                    "app": self._catalog.display_name(process),
                    "process": process,
                    "windows": [w.title for w in windows],
                    "focused": any(w.is_foreground for w in windows),
                }
                for process, windows in grouped.items()
            ),
            key=lambda a: (not a["focused"], str(a["app"]).lower()),
        )
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            summary=f"{len(apps)} applications with windows",
            data={"apps": apps, "process_count": len(snapshot.processes)},
            simulated=self._backend.simulated,
        )


class OpenApplicationArgs(BaseModel):
    name: str = Field(min_length=1, max_length=80, description="Application name, e.g. 'notepad'.")


class OpenApplicationTool(Tool[OpenApplicationArgs]):
    """Launch an application and prove it opened by observing windows/processes."""

    name = "open_application"
    description = "Open a desktop application and confirm that its window appeared."
    permission_level = PermissionLevel.SAFE_ACTION
    input_model = OpenApplicationArgs
    side_effects = True

    poll_interval = 0.2
    already_running_grace = 2.5

    def __init__(
        self, backend: SystemBackend, catalog: AppCatalog, *, verify_timeout: float
    ) -> None:
        self._backend = backend
        self._catalog = catalog
        self._timeout = verify_timeout

    def describe_action(self, args: OpenApplicationArgs) -> ActionDescriptor:
        app = self._catalog.resolve(args.name)
        if app is None:
            return ActionDescriptor(
                title="Open application", target=args.name, summary="", level=self.permission_level
            )
        summary = f"Launch {app.name}." if app.known else f"Ask Windows to run “{app.launch[0]}”."
        return ActionDescriptor(
            title="Open application",
            target=app.name,
            summary=summary,
            effects=["Starts a new process on this computer", *app.effects],
            level=max(app.level, self.permission_level),
        )

    async def precheck(self, args: OpenApplicationArgs, ctx: TraceContext) -> ToolError | None:
        if self._catalog.is_denied(args.name):
            return ToolError(
                code="denied",
                message=f"System utilities like {args.name} are blocked for safety.",
            )
        app = self._catalog.resolve(args.name)
        if app is None:
            return ToolError(
                code="invalid_name", message=f"“{args.name}” isn't an application name I can use."
            )
        if await self._launch_target(app) is None:
            return ToolError(
                code="app_not_found",
                message=f"{app.name} isn't installed, or Windows can't locate it.",
                suggestion="If it is installed, add it to config/apps.yaml with its executable.",
            )
        return None

    async def execute(self, args: OpenApplicationArgs, ctx: TraceContext) -> ToolResult:
        app = self._catalog.resolve(args.name)
        target = await self._launch_target(app) if app else None
        if app is None or target is None:
            return self._failure(
                args.name, ToolError(code="app_not_found", message="Windows can't locate it.")
            )

        before = await self._backend.snapshot()
        was_running = bool(matching_windows(before, app.processes))
        observations = [
            f"Before launch: {len(matching_windows(before, app.processes))} {app.name} window(s)"
        ]
        try:
            await self._backend.launch(target)
        except LaunchError as exc:
            return self._failure(
                app.name,
                ToolError(code=exc.code, message=exc.message, detail=exc.detail),
                observations,
            )
        observations.append(f"Launch requested: {target}")

        evidence = await self._observe(before, app, was_running)
        observations.extend(self._describe(evidence, app))
        if evidence.outcome is LaunchOutcome.NOT_DETECTED:
            return self._failure(
                app.name,
                ToolError(
                    code="not_observed",
                    message="Windows accepted the request, but no window or process appeared.",
                    suggestion="It may need longer to start, or something blocked it.",
                ),
                observations,
            )
        window = evidence.window
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            target=app.name,
            summary=f"{app.name}: {evidence.outcome.value.replace('_', ' ')}",
            observed_result=self._observed_result(evidence, app),
            observations=observations,
            data={
                "app": app.name,
                "app_key": app.key,
                "launch_target": target,
                "outcome": evidence.outcome.value,
                "window": _window_data(window),
            },
            simulated=self._backend.simulated,
        )

    async def verify(
        self, args: OpenApplicationArgs, result: ToolResult, ctx: TraceContext
    ) -> Verification:
        app = self._catalog.resolve(args.name)
        if not result.success or app is None:
            return Verification(
                status="failed", method="result", summary="The action reported failure."
            )
        snapshot = await self._backend.snapshot()
        windows = matching_windows(snapshot, app.processes)
        processes = matching_processes(snapshot, app.processes)
        evidence = [f"{len(windows)} matching window(s)", f"{len(processes)} matching process(es)"]
        if windows:
            window = next((w for w in windows if w.is_foreground), windows[0])
            if window.is_foreground:
                evidence.append(f"“{window.title}” has focus")
            return Verification(
                status="verified",
                method="window_enumeration",
                summary=f"{app.name} is running — “{window.title}”",
                evidence=evidence,
            )
        if processes:
            return Verification(
                status="unverifiable",
                method="process_list",
                summary=f"{app.name} process is running, but no visible window was found.",
                evidence=evidence,
            )
        return Verification(
            status="failed",
            method="window_enumeration",
            summary=f"No {app.name} window or process found.",
            evidence=evidence,
        )

    async def _launch_target(self, app: AppTarget) -> str | None:
        for candidate in app.launch:
            if resolved := await self._backend.resolve(candidate):
                return resolved
        return None

    async def _observe(
        self, before: EnvironmentSnapshot, app: AppTarget, was_running: bool
    ) -> LaunchEvidence:
        loop = asyncio.get_running_loop()
        window = min(self._timeout, self.already_running_grace) if was_running else self._timeout
        deadline = loop.time() + window
        while True:
            after = await self._backend.snapshot()
            evidence = analyze_launch(before, after, app.processes)
            if evidence.confirmed or loop.time() >= deadline:
                return evidence
            await asyncio.sleep(self.poll_interval)

    @staticmethod
    def _describe(evidence: LaunchEvidence, app: AppTarget) -> list[str]:
        lines = []
        for window in evidence.new_windows:
            lines.append(f"New window: “{window.title}” ({window.process_name}, pid {window.pid})")
        for process in evidence.new_processes:
            lines.append(f"New process: {process.name} (pid {process.pid})")
        if evidence.outcome is LaunchOutcome.BROUGHT_TO_FRONT and evidence.window:
            lines.append(f"Existing window brought to front: “{evidence.window.title}”")
        if evidence.outcome is LaunchOutcome.NOT_DETECTED:
            lines.append(f"No {app.name} window or process detected")
        return lines

    @staticmethod
    def _observed_result(evidence: LaunchEvidence, app: AppTarget) -> str:
        window = evidence.window
        title = f" — “{window.title}”" if window else ""
        match evidence.outcome:
            case LaunchOutcome.WINDOW_OPENED:
                return f"{app.name} window detected{title}"
            case LaunchOutcome.BROUGHT_TO_FRONT:
                return f"{app.name} brought to front{title}"
            case LaunchOutcome.ALREADY_RUNNING:
                return f"{app.name} was already running{title}"
            case LaunchOutcome.PROCESS_STARTED:
                return f"{app.name} process started; no window yet"
            case _:
                return f"{app.name} not detected"

    def _failure(
        self, target: str, error: ToolError, observations: list[str] | None = None
    ) -> ToolResult:
        return ToolResult.failure(
            self.name,
            error,
            target=target,
            observations=observations,
            simulated=self._backend.simulated,
        )
