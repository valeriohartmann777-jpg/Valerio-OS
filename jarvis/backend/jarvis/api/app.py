"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jarvis import __version__
from jarvis.api.quantlab_hub_routes import router as hub_router
from jarvis.api.quantlab_mission_routes import router as mission_router
from jarvis.api.quantlab_research_routes import router as research_router
from jarvis.api.quantlab_source_routes import router as source_router
from jarvis.api.routes import router
from jarvis.api.security import OriginGuard
from jarvis.runtime import Runtime
from jarvis.settings import Settings, load_settings


def create_app(settings: Settings | None = None, *, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or (runtime.settings if runtime else load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = runtime or Runtime(settings)
        app.state.runtime = rt
        await rt.start()
        try:
            yield
        finally:
            await rt.stop()

    app = FastAPI(title="JARVIS", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["content-type", "x-filename", "x-note", "x-language", "x-link-source"],
    )
    app.add_middleware(OriginGuard, allowed_origins=settings.server.allowed_origins)
    app.include_router(router)
    app.include_router(hub_router)
    app.include_router(research_router)
    app.include_router(source_router)
    app.include_router(mission_router)
    return app
