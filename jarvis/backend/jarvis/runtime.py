"""Composition root: the only place where services are constructed and wired."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path

from jarvis import __version__
from jarvis.agents.base import AgentRegistry
from jarvis.agents.catalog import AGENT_SPECS
from jarvis.agents.workers import OperatorAgent, SentinelAgent
from jarvis.build import build_id
from jarvis.core.brain import Brain
from jarvis.core.connector import BrainConnector, Verifier
from jarvis.core.context import EnvironmentContextService, platform_label
from jarvis.core.jarvis import JarvisCore
from jarvis.core.persona import build_system_prompt
from jarvis.core.responses import ResponseComposer
from jarvis.core.router import RuleBasedRouter
from jarvis.core.state import StateService
from jarvis.events.bus import EventBus
from jarvis.events.store import EventStore
from jarvis.events.types import EventType, Severity
from jarvis.learning.journal import LearningJournal
from jarvis.learning.market import MarketData
from jarvis.learning.service import LearningService, ResearchModel
from jarvis.llm.registry import ModelSet, build_models
from jarvis.missions.engine import MissionEngine
from jarvis.missions.planner import DeterministicPlanner
from jarvis.missions.repository import MissionRepository
from jarvis.permissions.service import PermissionService
from jarvis.settings import Settings
from jarvis.storage.audit import AuditLog
from jarvis.storage.database import Database
from jarvis.storage.preferences import Preferences
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.files import FileAccess, register_file_tools
from jarvis.tools.learning import register_learning_tools
from jarvis.tools.media import register_media_tools
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system import create_backend, register_system_tools
from jarvis.tools.system.apps import AppCatalog
from jarvis.tools.system.backend import SystemBackend
from jarvis.tools.web import register_web_tools
from jarvis.voice.audio import AudioDevice
from jarvis.voice.service import ProviderFactory, VoiceService, elevenlabs_provider
from jarvis.voice.wakeword import WakeDetector

log = logging.getLogger("jarvis.runtime")


class Runtime:
    def __init__(
        self,
        settings: Settings,
        *,
        backend: SystemBackend | None = None,
        models: ModelSet | None = None,
        key_verifier: Verifier | None = None,
        voice_audio: Callable[[], AudioDevice] | None = None,
        voice_detector: Callable[[Path], WakeDetector] | None = None,
        voice_provider: ProviderFactory | None = None,
        research_model: Callable[[], ResearchModel | None] | None = None,
        market: MarketData | None = None,
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

        self.catalog = AppCatalog(settings.apps, ui_pid=settings.runtime.ui_pid)
        self.backend = backend or create_backend(settings)
        self.tools = ToolRegistry()
        register_system_tools(self.tools, self.backend, self.catalog, settings)
        register_web_tools(
            self.tools,
            self.backend,
            self.catalog,
            settings.web,
            verify_timeout=settings.runtime.launch_verify_timeout_seconds,
        )
        register_media_tools(self.tools, self.backend)
        self.files = FileAccess(settings.files)
        register_file_tools(
            self.tools,
            self.files,
            self.backend,
            self.catalog,
            verify_timeout=settings.runtime.launch_verify_timeout_seconds,
        )
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
        # Injected models (tests) are used as-is; models built from a key get
        # that key verified in the background at startup.
        self._verify_key_on_start = models is None
        self.brain = Brain(
            models=models or build_models(settings.models),
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
        self.connector = BrainConnector(
            settings=settings.models,
            brain=self.brain,
            bus=self.bus,
            env_file=settings.env_file,
            verifier=key_verifier,
        )
        self._key_check: asyncio.Task[None] | None = None
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

        self.preferences = Preferences(settings.data_dir / "preferences.json")
        self.voice = VoiceService(
            settings=settings.voice,
            bus=self.bus,
            state=self.state,
            submit=lambda text: self.core.submit(text, source="voice"),
            preferences=self.preferences,
            env_file=settings.env_file,
            models_dir=settings.data_dir / "models",
            audio_factory=voice_audio or _sounddevice,
            detector_factory=voice_detector or _open_wake_word,
            provider_factory=voice_provider or elevenlabs_provider,
        )

        self.learning_journal = LearningJournal(self.db)
        self._research: tuple[str, ResearchModel] | None = None
        self.learning = LearningService(
            settings=settings.learning,
            bus=self.bus,
            journal=self.learning_journal,
            market=market or MarketData(settings.data_dir / "market"),
            preferences=self.preferences,
            model_factory=research_model or self._research_model,
        )
        register_learning_tools(self.tools, self.learning_journal, lambda: self.learning.status)

    def _research_model(self) -> ResearchModel | None:
        """Claude for learning, with the key the brain currently uses."""
        secret = self.connector.settings.anthropic_api_key
        if secret is None or not self.brain.available:
            return None
        key = secret.get_secret_value()
        if self._research is None or self._research[0] != key:
            import anthropic

            from jarvis.llm.anthropic_provider import AnthropicChatModel

            learning = self.settings.learning
            model = AnthropicChatModel(
                client=anthropic.AsyncAnthropic(api_key=key),
                model=learning.model,
                max_tokens=learning.max_tokens,
                effort=learning.effort,
                timeout_seconds=learning.timeout_seconds,
                refusal_fallback=self.settings.models.refusal_fallback,
            )
            self._research = (key, model)
        return self._research[1]

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
            log.warning(
                "reasoning offline: %s (looked for ANTHROPIC_API_KEY in the environment and %s)",
                self.brain.status.reason,
                self.settings.env_file,
            )
        elif self._verify_key_on_start:
            self._key_check = asyncio.create_task(self.connector.check(), name="key-check")
        await self.voice.start()
        await self.learning.start()

    async def stop(self) -> None:
        await self.learning.stop()
        await self.voice.stop()
        if self._key_check is not None:
            self._key_check.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._key_check
        await self.context.stop()
        await self.missions.shutdown()
        await self.state.close()
        await self.db.close()


def _sounddevice() -> AudioDevice:
    from jarvis.voice.audio import SoundDeviceAudio

    return SoundDeviceAudio()


def _open_wake_word(models: Path) -> WakeDetector:
    from jarvis.voice.wakeword import OpenWakeWord

    return OpenWakeWord(models)
