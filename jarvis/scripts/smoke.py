"""Exercise the REAL system backend (Windows or macOS) without the UI.

    macOS:    backend/.venv/bin/python scripts/smoke.py textedit
    Windows:  backend\\.venv\\Scripts\\python scripts\\smoke.py notepad

Prints the environment snapshot, then runs observe → launch → observe →
verify for one application exactly as the Operator/Sentinel chain does.
"""

from __future__ import annotations

import asyncio
import sys
import time

from jarvis.core.trace import TraceContext
from jarvis.permissions.models import PermissionLevel
from jarvis.settings import load_settings
from jarvis.tools.system import create_backend
from jarvis.tools.system.apps import AppCatalog
from jarvis.tools.system.tools import OpenApplicationArgs, OpenApplicationTool

STARTED = time.perf_counter()


def step(message: str) -> None:
    print(f"[{time.perf_counter() - STARTED:5.1f}s] {message}", flush=True)


async def main(name: str) -> int:
    step("loading settings")
    settings = load_settings()
    step(f"creating backend for {sys.platform}")
    backend = create_backend(settings)
    if backend.simulated:
        print(
            "No real system backend on this machine (only Windows and macOS are supported)."
        )
        return 2
    step(f"backend: {backend.name}")
    catalog = AppCatalog(settings.apps)
    ctx = TraceContext.new()

    step("reading windows and processes")
    snapshot = await backend.snapshot()
    foreground = snapshot.foreground
    print(f"windows: {len(snapshot.windows)}   processes: {len(snapshot.processes)}")
    if foreground:
        print(
            f"front: {foreground.title!r} ({foreground.process_name}, pid {foreground.pid})"
        )

    tool = OpenApplicationTool(
        backend, catalog, verify_timeout=settings.runtime.launch_verify_timeout_seconds
    )
    args = OpenApplicationArgs(name=name)
    step(f"resolving {name!r}")
    if error := await tool.precheck(args, ctx):
        print(f"refused before launch: [{error.code}] {error.message}")
        return 1

    action = tool.describe_action(args)
    print(f"\naction: {action.title} — {action.target} (level {int(action.level)})")
    for effect in action.effects:
        print(f"  - {effect}")
    needs_approval = action.level > PermissionLevel.SAFE_ACTION
    if (
        needs_approval
        and input("JARVIS would ask for approval. Proceed? [y/N] ").lower() != "y"
    ):
        return 1

    step("launching and observing (up to 10 s)")
    result = await tool.execute(args, ctx)
    print(f"\nsuccess: {result.success}   outcome: {result.data.get('outcome')}")
    for line in result.observations:
        print(f"  · {line}")
    if result.error:
        print(
            f"error: [{result.error.code}] {result.error.message} {result.error.detail or ''}"
        )

    step("sentinel re-observes")
    verification = await tool.verify(args, result, ctx)
    print(f"\nsentinel: {verification.status} — {verification.summary}")
    for line in verification.evidence:
        print(f"  · {line}")
    return 0 if verification.verified else 1


if __name__ == "__main__":
    default = "textedit" if sys.platform == "darwin" else "notepad"
    raise SystemExit(asyncio.run(main(" ".join(sys.argv[1:]) or default)))
