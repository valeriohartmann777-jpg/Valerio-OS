"""Health that means something: is the process alive, is it ready, what can it do.

- ``GET /health/live`` — the process answers (no dependencies checked).
- ``GET /health/ready`` — every base component answered a real request just now: database
  (a query), schema (latest migration applied), data folder (a write), APEX (markets loaded).
  ``ready`` → 200, ``degraded`` → 503 with the failing checks named.
- ``GET /system/capabilities`` — what this backend offers and from which sources, so the UI
  can tell "app online" from "data fresh" from "validated signal".

``GET /health`` (older clients, the desktop supervisor) is unchanged in ``routes.py``.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from jarvis.runtime import Runtime
from jarvis.storage.database import MIGRATIONS

router = APIRouter()


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(_runtime)]


@router.get("/health/live")
async def live(rt: RuntimeDep) -> dict[str, Any]:
    return {
        "status": "live",
        "pid": os.getpid(),
        "uptime_seconds": round(rt.uptime_seconds, 1),
        "build": rt.build,
    }


async def _check(name: str, probe: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        detail = await probe()
        ok = True
    except Exception as exc:  # a failed check is reported, never raised
        detail, ok = f"{type(exc).__name__}: {exc}"[:300], False
    return {
        "name": name,
        "ok": ok,
        "detail": detail,
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


@router.get("/health/ready")
async def ready(rt: RuntimeDep) -> JSONResponse:
    async def database() -> str:
        row = await rt.db.fetch_one("SELECT 1 AS one")
        if not row or row["one"] != 1:
            raise RuntimeError("the database didn't answer")
        return "query answered"

    async def schema() -> str:
        row = await rt.db.fetch_one("SELECT MAX(version) AS v FROM schema_version")
        current = int(row["v"]) if row and row["v"] is not None else 0
        if current != len(MIGRATIONS):
            raise RuntimeError(f"schema {current}, expected {len(MIGRATIONS)}")
        return f"schema {current} (latest)"

    async def data_folder() -> str:
        folder = rt.settings.data_dir
        probe = folder / f".ready-{secrets.token_hex(4)}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return str(folder)

    async def apex() -> str:
        markets = sorted(rt.apex.contexts)
        if not markets:
            raise RuntimeError("no APEX market configured (config/apex.yaml)")
        return f"{', '.join(markets)} ({'fixture' if rt.apex.fixture else 'local data'})"

    checks = [
        await _check("database", database),
        await _check("schema", schema),
        await _check("data_folder", data_folder),
        await _check("apex", apex),
    ]
    ok = all(c["ok"] for c in checks)
    body = {
        "status": "ready" if ok else "degraded",
        "build": rt.build,
        "uptime_seconds": round(rt.uptime_seconds, 1),
        "checks": checks,
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/system/capabilities")
async def capabilities(rt: RuntimeDep) -> dict[str, Any]:
    markets = []
    for name, ctx in rt.apex.contexts.items():
        markets.append(
            {
                "market": name,
                "sources": ["fixture"] if rt.apex.fixture else ctx.spec.sources,
                "tick": ctx.spec.tick,
                "point_value": ctx.spec.point_value,
            }
        )
    return {
        "version": rt.version,
        "build": rt.build,
        "system_backend": rt.backend.name,
        "simulated": rt.backend.simulated,
        "apex": {
            "provider": rt.settings.apex.provider,
            "markets": markets,
            "orders": False,  # research only: no order path exists
        },
        "events": {"websocket": "/events"},
    }
