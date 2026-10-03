"""Composition root: the only place where services are constructed and wired."""

from __future__ import annotations

import logging
import time

from jarvis import __version__
from jarvis.agents.base import AgentRegistry
from jarvis.agents.catalog import AGENT_SPECS
from jarvis.agents.workers import OperatorAgent, SentinelAgent
from jarvis.build import build_id
from jarvis.core.brain import Brain
from jarvis.core.context import EnvironmentContextService, platform_label
from jarvis.core.jarvis import JarvisCore
from jarvis.core.persona import build_system_prompt
from jarvis.core.responses import ResponseComposer
from jarvis.core.router import RuleBasedRouter
from jarvis.core.state import StateService
from jarvis.events.bus import EventBus
from jarvis.events.store import EventStore
from jarvis.events.types import EventType, Severity
from jarvis.llm.registry import ModelSet, build_models
from jarvis.missions.engine import MissionEngine
from jarvis.missions.planner import DeterministicPlanner
from jarvis.missions.repository import MissionRepository
from jarvis.permissions.service import PermissionService
from jarvis.settings import Settings
from jarvis.storage.audit import AuditLog
from jarvis.storage.database import Database
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system import create_backend, register_system_tools
from jarvis.tools.system.apps import AppCatalog
from jarvis.tools.system.backend import SystemBackend

log = logging.getLogger("jarvis.runtime")


class Runtime:
    def __init__(
        self,
        settings: Settings,
        *,
        backend: SystemBackend | None = None,
        models: ModelSet | None = None,
    ) -> None:
        self.settings = settings
        self.started_at = time.time()
        self.version = __version__
        self.build = build_id()

        self.bus = EventBus()
        self.db = Database(settings.database_path)
        self.events = EventStore(self.db)
        self.audit = AuditLog(self.db)
        self.state = StateService(self.bus, settle_seconds=settings.runtime.settle_seconds)
        self.permissions = PermissionService(settings.permissions, self.bus)

        self.catalog = AppCatalog(settings.apps)
        self.backend = backend or create_backend(settings)
        self.tools = ToolRegistry()
        register_system_tools(self.tools, self.backend, self.catalog, settings)
        self.executor = ToolExecutor(
            self.tools,
            self.permissions,
            self.bus,
            self.audit,
            timeout_seconds=settings.runtime.tool_timeout_seconds,
        )

        self.agents = AgentRegistry(self.bus, AGENT_SPECS)
        self.operator = OperatorAgent(self.agents, self.executor)
        self.sentinel = SentinelAgent(self.agents, self.tools, self.bus)

        self.composer = ResponseComposer(settings.personality)
        self.mission_repository = MissionRepository(self.db)
        self.missions = MissionEngine(
            repository=self.mission_repository,
            bus=self.bus,
            state=self.state,
            agents=self.agents,
            operator=self.operator,
            sentinel=self.sentinel,
            permissions=self.permissions,
            composer=self.composer,
        )
        self.context = EnvironmentContextService(
            self.bus,
            self.backend,
            self.catalog,
            interval=settings.runtime.context_poll_seconds,
        )
        self.models = models or build_models(settings.models)
        self.brain = Brain(
            models=self.models,
            tools=self.tools,
            operator=self.operator,
            missions=self.missions,
            bus=self.bus,
            state=self.state,
            context=self.context,
            system_prompt=build_system_prompt(
                settings.personality, platform=platform_label(), simulated=self.backend.simulated
            ),
            history_turns=settings.models.history_turns,
            max_tool_rounds=settings.models.max_tool_rounds,
        )
        self.core = JarvisCore(
            bus=self.bus,
            state=self.state,
            router=RuleBasedRouter(),
            planner=DeterministicPlanner(self.catalog),
            missions=self.missions,
            operator=self.operator,
            composer=self.composer,
            brain=self.brain,
            tools=self.tools,
        )

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    async def start(self) -> None:
        await self.db.connect()
        self.events.attach(self.bus)
        interrupted = await self.mission_repository.fail_interrupted()
        if interrupted:
            log.warning("marked %d interrupted mission(s) as failed", interrupted)
        await self.context.start()
        await self.bus.emit(
            EventType.SYSTEM_ONLINE,
            message="JARVIS online"
            + (" — simulated environment" if self.backend.simulated else ""),
            source="system",
            severity=Severity.IMPORTANT,
            payload={"version": self.version, "system_backend": self.backend.name},
        )
        log.info(
            "JARVIS online",
            extra={
                "system_backend": self.backend.name,
                "database": str(self.settings.database_path),
                "fast_model": self.brain.status.fast_model,
                "reasoning_model": self.brain.status.reasoning_model,
            },
        )
        if not self.brain.available:
            log.warning("reasoning offline: %s", self.brain.status.reason)

    async def stop(self) -> None:
        await self.context.stop()
        await self.missions.shutdown()
        await self.state.close()
        await self.db.close()
