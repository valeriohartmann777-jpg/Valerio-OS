"""Connects the brain to Claude while JARVIS runs — no file editing, no restart.

``connect`` takes a pasted key, checks it with a free model call, stores it in the OS
keystore and rebuilds the brain's models through the AI router. ``check`` verifies a
saved key at startup, so a mistyped key shows up as *rejected* in the dashboard instead
of failing on the first question.

The key itself is never logged, emitted or returned — only its last four
characters (``key_hint``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING

from jarvis.core.brain import Brain, BrainStatus
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.llm.base import ModelError
from jarvis.llm.registry import ModelSet

if TYPE_CHECKING:
    from jarvis.ai.router import SmartRouter

log = logging.getLogger("jarvis.brain")

KEY_PREFIX = "sk-ant-"
ENV_NAME = "ANTHROPIC_API_KEY"
_QUOTES = "\"'\u201c\u201d\u2018\u2019"  # incl. smart quotes


def normalize_key(raw: str, env_name: str = ENV_NAME) -> str:
    """Accept what people actually paste: ``ANTHROPIC_API_KEY=sk-…``, quotes, spaces."""
    key = raw.strip().removeprefix("export ").strip()
    name, sep, rest = key.partition("=")
    if sep and name.strip().upper() == env_name:
        key = rest
    return key.strip().strip(_QUOTES).strip()


def check_format(key: str) -> None:
    if not key:
        raise ModelError("invalid_format", "Paste an API key first.")
    if not key.startswith(KEY_PREFIX) or len(key) < 20 or any(c.isspace() for c in key):
        raise ModelError(
            "invalid_format",
            "That doesn't look like an Anthropic API key.",
            suggestion=(
                "Keys start with sk-ant-. Copy it again from console.anthropic.com → API keys."
            ),
        )


class BrainConnector:
    """Connects the brain through the AI router (Settings → Brain and AI & Billing).

    ``connect`` verifies a pasted key with a free model check and stores it in the OS
    keystore (never in a file); the brain then uses whatever route the router allows —
    the Claude plan first, the API only after paid fallback is approved.
    """

    def __init__(
        self,
        *,
        router: SmartRouter,
        brain: Brain,
        bus: EventBus,
        models: Callable[[], ModelSet],
    ) -> None:
        self._router = router
        self._brain = brain
        self._bus = bus
        self._models = models
        self._lock = asyncio.Lock()

    async def connect(self, raw_key: str) -> BrainStatus:
        """Verify, store and activate a key. Raises ``ModelError`` and changes nothing
        when the key is malformed, rejected or cannot be stored."""
        key = normalize_key(raw_key)
        check_format(key)
        async with self._lock:  # one key change at a time
            await self._router.connect_key(key)
        self._brain.set_models(self._models())
        status = self._brain.status
        log.info("API key connected", extra={"key_hint": status.key_hint})
        message = (
            "Brain connected"
            if status.available
            else "API key saved — approve paid fallback or connect your Claude plan"
        )
        await self._announce(message, Severity.IMPORTANT, status)
        return status

    async def check(self) -> None:
        """Verify a saved key in the background (free model check). A rejected key shows
        up as such; network problems change nothing."""
        try:
            await self._router.check("api")
        except Exception as exc:  # never let a background check crash anything
            log.warning("could not verify the API key: %s", type(exc).__name__)
            return
        await self.refresh()

    async def refresh(self) -> None:
        """Rebuild the brain's models after a routing change; announce real changes."""
        # No lock: called from routing events, possibly while connect() holds it.
        before = self._brain.status
        self._brain.set_models(self._models())
        after = self._brain.status
        if asdict(before) != asdict(after):
            await self._announce(
                "Brain online" if after.available else (after.reason or "Brain offline"),
                Severity.INFO,
                after,
            )

    async def _announce(self, message: str, severity: Severity, status: BrainStatus) -> None:
        await self._bus.emit(
            EventType.BRAIN_CHANGED,
            message=message,
            source="brain",
            severity=severity,
            payload={"brain": asdict(status)},
        )
