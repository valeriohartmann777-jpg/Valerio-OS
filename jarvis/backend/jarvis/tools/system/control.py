"""Window control: hide (minimise) and quit running applications.

Bringing an app to the front is ``open_application`` (it activates a running
app). Quitting is graceful — like ⌘Q or the window's close button — so apps
with unsaved work still ask; it is level 2 and needs approval by default.
JARVIS never hides or quits itself, and never quits the desktop shell.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import ActionDescriptor, PermissionLevel
from jarvis.tools.base import Tool, ToolError, ToolResult, Verification
from jarvis.tools.system.apps import AppCatalog, matching_processes, normalize
from jarvis.tools.system.backend import ActionError, EnvironmentSnapshot, SystemBackend

# Quitting these would take down the desktop itself.
NEVER_QUIT = frozenset(
    {
        "finder.app",
        "dock.app",
        "loginwindow.app",
        "systemuiserver.app",
        "controlcenter.app",
        "windowserver",
        "explorer.exe",
        "dwm.exe",
    }
)


class AppNameArgs(BaseModel):
    name: str = Field(min_length=1, max_length=80, description="Application name, e.g. 'Spotify'.")


@dataclass(frozen=True, slots=True)
class RunningApp:
    name: str
    processes: frozenset[str]


def find_running(
    catalog: AppCatalog, snapshot: EnvironmentSnapshot, query: str
) -> RunningApp | None:
    """The running app the user means: catalog name/alias, or a window's app name."""
    target = catalog.resolve(query)
    if target is not None and matching_processes(snapshot, target.processes):
        return RunningApp(target.name, target.processes)
    wanted = normalize(query)
    for window in snapshot.windows:
        label = catalog.window_label(window)
        if normalize(label) == wanted:
            return RunningApp(label, frozenset({window.process_name}))
    return None


def visible_windows(snapshot: EnvironmentSnapshot, processes: frozenset[str]) -> int:
    return sum(1 for w in snapshot.windows if w.process_name in processes and not w.minimized)


def is_running(snapshot: EnvironmentSnapshot, processes: frozenset[str]) -> bool:
    return bool(matching_processes(snapshot, processes))


class _AppControlTool(Tool[AppNameArgs]):
    input_model = AppNameArgs
    side_effects = True
    verb: str
    poll_interval = 0.2

    def __init__(self, backend: SystemBackend, catalog: AppCatalog, *, settle: float) -> None:
        self._backend = backend
        self._catalog = catalog
        self._settle = settle

    def _display(self, args: AppNameArgs) -> str:
        target = self._catalog.resolve(args.name)
        return target.name if target else args.name

    def _refuse(self, app: RunningApp) -> ToolError | None:
        """Tool-specific refusals (e.g. quitting the desktop shell)."""
        return None

    async def precheck(self, args: AppNameArgs, ctx: TraceContext) -> ToolError | None:
        snapshot = await self._backend.snapshot()
        app = find_running(self._catalog, snapshot, args.name)
        if app is None:
            return ToolError(code="not_running", message=f"{self._display(args)} isn't running.")
        pids = {p.pid for p in snapshot.processes if p.name in app.processes}
        pids |= {w.pid for w in snapshot.windows if w.process_name in app.processes}
        if any(self._catalog.is_self_pid(pid) for pid in pids):
            return ToolError(code="self", message=f"I won't {self.verb} myself.")
        return self._refuse(app)

    async def _resolve(self, args: AppNameArgs) -> RunningApp | ToolResult:
        app = find_running(self._catalog, await self._backend.snapshot(), args.name)
        if app is None:
            return self._failure(
                args, ToolError(code="not_running", message=f"{self._display(args)} isn't running.")
            )
        return app

    def _failure(self, args: AppNameArgs, error: ToolError) -> ToolResult:
        return ToolResult.failure(
            self.name, error, target=self._display(args), simulated=self._backend.simulated
        )

    def _action_failure(self, args: AppNameArgs, exc: ActionError) -> ToolResult:
        return self._failure(
            args,
            ToolError(
                code=exc.code, message=exc.message, suggestion=exc.suggestion, detail=exc.detail
            ),
        )

    async def _wait_until(
        self, done: Callable[[EnvironmentSnapshot], bool]
    ) -> tuple[EnvironmentSnapshot, bool]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settle
        while True:
            snapshot = await self._backend.snapshot()
            if done(snapshot):
                return snapshot, True
            if loop.time() >= deadline:
                return snapshot, False
            await asyncio.sleep(self.poll_interval)


class HideApplicationTool(_AppControlTool):
    name = "hide_application"
    description = (
        "Hide a running application's windows (macOS: like ⌘H; Windows: minimise). "
        "It keeps running; open_application brings it back."
    )
    permission_level = PermissionLevel.SAFE_ACTION
    verb = "hide"

    def describe_action(self, args: AppNameArgs) -> ActionDescriptor:
        app = self._display(args)
        return ActionDescriptor(
            title="Hide application",
            target=app,
            summary=f"Hide {app}.",
            effects=["Its windows disappear from the screen; it keeps running"],
            level=self.permission_level,
            category="windows",
        )

    def step_titles(self, args: AppNameArgs) -> tuple[str, str]:
        app = self._display(args)
        return f"Hide {app}", f"Verify {app} is hidden"

    async def execute(self, args: AppNameArgs, ctx: TraceContext) -> ToolResult:
        app = await self._resolve(args)
        if isinstance(app, ToolResult):
            return app
        try:
            count = await self._backend.hide_app(app.processes)
        except ActionError as exc:
            return self._action_failure(args, exc)
        _, hidden = await self._wait_until(lambda s: visible_windows(s, app.processes) == 0)
        observed = (
            f"{app.name} is hidden" if hidden else f"Asked {app.name} to hide, but windows remain"
        )
        return ToolResult(
            success=hidden,
            tool=self.name,
            action=self.name,
            target=app.name,
            summary=observed,
            observed_result=observed,
            observations=[f"Asked {count} instance(s)/window(s) to hide"],
            error=None
            if hidden
            else ToolError(code="still_visible", message=f"{app.name} still shows windows."),
            data={"app": app.name, "processes": sorted(app.processes)},
            simulated=self._backend.simulated,
        )

    async def verify(
        self, args: AppNameArgs, result: ToolResult, ctx: TraceContext
    ) -> Verification:
        if not result.success:
            return Verification(status="failed", method="result", summary=result.summary)
        processes = frozenset(result.data.get("processes", []))
        shown = visible_windows(await self._backend.snapshot(), processes)
        if shown == 0:
            return Verification(
                status="verified",
                method="window_enumeration",
                summary=f"No {result.target} windows are visible",
            )
        return Verification(
            status="failed",
            method="window_enumeration",
            summary=f"{shown} {result.target} window(s) are still visible.",
        )


class QuitApplicationTool(_AppControlTool):
    name = "quit_application"
    description = (
        "Quit a running application gracefully, like ⌘Q / closing its windows. Apps with "
        "unsaved work still ask the user. Needs the user's approval."
    )
    permission_level = PermissionLevel.MODIFICATION
    verb = "quit"

    def describe_action(self, args: AppNameArgs) -> ActionDescriptor:
        app = self._display(args)
        return ActionDescriptor(
            title="Quit application",
            target=app,
            summary=f"Quit {app}.",
            effects=[
                "Closes all its windows and ends it",
                "Unsaved work: the app asks before discarding anything",
            ],
            level=self.permission_level,
            category="windows",
        )

    def step_titles(self, args: AppNameArgs) -> tuple[str, str]:
        app = self._display(args)
        return f"Quit {app}", f"Verify {app} has quit"

    def _refuse(self, app: RunningApp) -> ToolError | None:
        if app.processes & NEVER_QUIT:
            return ToolError(
                code="protected", message=f"{app.name} is part of the desktop; I won't quit it."
            )
        return None

    async def execute(self, args: AppNameArgs, ctx: TraceContext) -> ToolResult:
        app = await self._resolve(args)
        if isinstance(app, ToolResult):
            return app
        try:
            count = await self._backend.quit_app(app.processes)
        except ActionError as exc:
            return self._action_failure(args, exc)
        _, gone = await self._wait_until(lambda s: not is_running(s, app.processes))
        observed = (
            f"{app.name} has quit"
            if gone
            else f"Asked {app.name} to quit — it is still open (it may be asking to save)"
        )
        return ToolResult(
            success=True,
            tool=self.name,
            action=self.name,
            target=app.name,
            summary=observed,
            observed_result=observed,
            observations=[f"Asked {count} instance(s)/window(s) to quit"],
            data={"app": app.name, "processes": sorted(app.processes), "quit": gone},
            simulated=self._backend.simulated,
        )

    async def verify(
        self, args: AppNameArgs, result: ToolResult, ctx: TraceContext
    ) -> Verification:
        if not result.success:
            return Verification(status="failed", method="result", summary=result.summary)
        processes = frozenset(result.data.get("processes", []))
        if not is_running(await self._backend.snapshot(), processes):
            return Verification(
                status="verified",
                method="process_list",
                summary=f"{result.target} is no longer running",
            )
        return Verification(
            status="unverifiable",
            method="process_list",
            summary=f"{result.target} is still running — it may be waiting for you to save.",
        )
