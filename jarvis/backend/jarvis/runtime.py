"""Composition root: the only place where services are constructed and wired."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from jarvis import __version__
from jarvis.agents.base import AgentRegistry
from jarvis.agents.catalog import AGENT_SPECS
from jarvis.agents.workers import OperatorAgent, SentinelAgent
from jarvis.bots.mt5 import Mt5Paths
from jarvis.bots.service import BotLabService, Tester
from jarvis.bots.store import BotStore
from jarvis.briefing.service import BriefingService
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
from jarvis.memory.store import MemoryStore
from jarvis.missions.engine import MissionEngine
from jarvis.missions.planner import DeterministicPlanner
from jarvis.missions.repository import MissionRepository
from jarvis.permissions.service import PermissionService
from jarvis.quantlab.service import QuantLabService
from jarvis.quantlab.store import QuantLabStore
from jarvis.settings import PROJECT_ROOT, Settings
from jarvis.storage.audit import AuditLog
from jarvis.storage.database import Database
from jarvis.storage.preferences import Preferences
from jarvis.tools.bots import register_bot_tools
from jarvis.tools.briefing import register_briefing_tools
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.files import FileAccess, register_file_tools
from jarvis.tools.learning import register_learning_tools
from jarvis.tools.media import register_media_tools
from jarvis.tools.memory import register_memory_tools
from jarvis.tools.quantlab import register_quantlab_tools
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system import create_backend, register_system_tools
from jarvis.tools.system.apps import AppCatalog
from jarvis.tools.system.backend import SystemBackend
from jarvis.tools.training import register_training_tools
from jarvis.tools.web import register_web_tools
from jarvis.training.service import TrainingService
from jarvis.training.store import TrainingStore
from jarvis.util import utcnow
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
        bot_model: Callable[[], ResearchModel | None] | None = None,
        bot_tester: Callable[[Mt5Paths], Tester] | None = None,
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
        # Before the brain is built: it fixes its tool list then.
        self.memory = MemoryStore(self.db, self.bus)
        register_memory_tools(self.tools, self.memory)
        self.learning_journal = LearningJournal(self.db)
        register_learning_tools(
            self.tools,
            self.learning_journal,
            lambda: self.learning.status,
            lambda: self.training.status,
        )
        register_briefing_tools(self.tools, lambda: self.briefing)
        register_training_tools(self.tools, lambda: self.training)
        register_bot_tools(self.tools, lambda: self.bots)
        register_quantlab_tools(self.tools, lambda: self.quantlab)
        self.market = market or MarketData(settings.data_dir / "market")
        self.preferences = Preferences(settings.data_dir / "preferences.json")
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
            memory=self.memory,
        )
        self.connector = BrainConnector(
            settings=settings.models,
            brain=self.brain,
            bus=self.bus,
            env_file=settings.env_file,
            verifier=key_verifier,
        )
        self._key_check: asyncio.Task[None] | None = None
        self._housekeeping: asyncio.Task[None] | None = None
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

        self._research: tuple[str, ResearchModel] | None = None
        self.learning = LearningService(
            settings=settings.learning,
            bus=self.bus,
            journal=self.learning_journal,
            market=self.market,
            preferences=self.preferences,
            model_factory=research_model or self._research_model,
        )

        self.briefing = BriefingService(
            settings=settings.briefing,
            learning=settings.learning,
            market=self.market,
            journal=self.learning_journal,
            bus=self.bus,
            preferences=self.preferences,
            # Follow-up questions about the briefing reach the brain with it in view.
            on_sent=lambda text: self.brain.remember("(morning briefing requested)", text),
            model_line=lambda: self.training.briefing_line(),
        )

        self._bot_research: tuple[str, ResearchModel] | None = None
        self.bots = BotLabService(
            settings=settings.bots,
            prices=settings.learning.prices,
            store=BotStore(self.db),
            bus=self.bus,
            preferences=self.preferences,
            model_factory=bot_model or self._bot_model,
            tester_factory=bot_tester,
        )

        self.quantlab = QuantLabService(
            store=QuantLabStore(self.db),
            bus=self.bus,
            root=settings.data_dir / "quantlab",
            fixtures=PROJECT_ROOT / "docs" / "quantlab-handoff" / "fixtures",
            code_revision=self.build,
        )

        self.training = TrainingService(
            settings=settings.training,
            learning=settings.learning,
            briefing=settings.briefing,
            market=self.market,
            store=TrainingStore(self.db, settings.data_dir / "training"),
            bus=self.bus,
            preferences=self.preferences,
        )

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

    def _bot_model(self) -> ResearchModel | None:
        """Claude for the Bot Lab: the brain's key, the Bot Lab's model settings."""
        secret = self.connector.settings.anthropic_api_key
        if secret is None or not self.brain.available:
            return None
        key = secret.get_secret_value()
        if self._bot_research is None or self._bot_research[0] != key:
            import anthropic

            from jarvis.llm.anthropic_provider import AnthropicChatModel

            research = self.settings.bots.research
            model = AnthropicChatModel(
                client=anthropic.AsyncAnthropic(api_key=key),
                model=research.model,
                max_tokens=research.max_tokens,
                effort=research.effort,
                timeout_seconds=research.timeout_seconds,
                refusal_fallback=self.settings.models.refusal_fallback,
            )
            self._bot_research = (key, model)
        return self._bot_research[1]

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    async def start(self) -> None:
        await self.db.connect()
        self.events.attach(self.bus)
        await self.memory.load()
        # The chat on screen is still the brain's context after a restart or update.
        since = utcnow() - timedelta(hours=self.settings.models.history_restore_hours)
        self.brain.restore(
            await self.events.exchanges(since.isoformat(), self.settings.models.history_turns)
        )
        self._housekeeping = asyncio.create_task(self._housekeep(), name="housekeeping")
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
        await self.briefing.start()
        await self.training.start()
        await self.bots.start()
        await self.quantlab.start()

    async def _housekeep(self) -> None:
        """Once a day: drop old activity (the conversation stays)."""
        while True:
            days = self.settings.storage.event_retention_days
            try:
                removed = await self.events.prune((utcnow() - timedelta(days=days)).isoformat())
                if removed:
                    log.info("removed %d activity events older than %d days", removed, days)
            except Exception:  # housekeeping must never take JARVIS down
                log.exception("housekeeping failed")
            await asyncio.sleep(24 * 3600)

    async def stop(self) -> None:
        if self._housekeeping is not None:
            self._housekeeping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._housekeeping
        await self.quantlab.stop()
        await self.bots.stop()
        await self.training.stop()
        await self.briefing.stop()
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
