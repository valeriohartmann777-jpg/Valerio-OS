"""Connects the brain to Claude while JARVIS runs — no file editing, no restart.

``connect`` takes a pasted key, checks it against the provider, stores it in
``jarvis/.env`` (owner-only) and swaps the brain's models. ``check`` verifies a
key that came from the environment at startup, so a mistyped key shows up as
*rejected* in the dashboard instead of failing on the first question.

The key itself is never logged, emitted or returned — only its last four
characters (``key_hint``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from pydantic import SecretStr

from jarvis.core.brain import Brain, BrainStatus
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.llm.base import ModelError
from jarvis.llm.registry import ModelSet, anthropic_models, build_models
from jarvis.settings import ModelSettings, write_dotenv_value

log = logging.getLogger("jarvis.brain")

Verifier = Callable[[str, list[str]], Awaitable[None]]

KEY_PREFIX = "sk-ant-"
ENV_NAME = "ANTHROPIC_API_KEY"
# Errors that say the key itself is unusable (vs. a network hiccup).
_KEY_PROBLEMS = {"authentication", "permission", "model_not_found"}
_QUOTES = "\"'\u201c\u201d\u2018\u2019"  # incl. smart quotes


def normalize_key(raw: str) -> str:
    """Accept what people actually paste: ``ANTHROPIC_API_KEY=sk-…``, quotes, spaces."""
    key = raw.strip().removeprefix("export ").strip()
    name, sep, rest = key.partition("=")
    if sep and name.strip().upper() == ENV_NAME:
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


async def _verify_with_anthropic(key: str, models: list[str]) -> None:
    from jarvis.llm.anthropic_provider import verify_key

    await verify_key(key, models)


class BrainConnector:
    def __init__(
        self,
        *,
        settings: ModelSettings,
        brain: Brain,
        bus: EventBus,
        env_file: Path,
        verifier: Verifier | None = None,
        build: Callable[[ModelSettings], ModelSet] = build_models,
    ) -> None:
        self._settings = settings
        self._brain = brain
        self._bus = bus
        self._env_file = env_file
        self._verify = verifier or _verify_with_anthropic
        self._build = build
        self._lock = asyncio.Lock()

    @property
    def env_file(self) -> Path:
        return self._env_file

    async def connect(self, raw_key: str) -> BrainStatus:
        """Verify, persist and activate a key. Raises ``ModelError`` and changes
        nothing when the key is malformed, rejected or cannot be stored."""
        key = normalize_key(raw_key)
        check_format(key)
        model_ids = anthropic_models(self._settings)
        if not model_ids:
            raise ModelError(
                "not_configured",
                "No Claude model is configured.",
                suggestion="Set the fast and reasoning models in config/models.yaml.",
            )
        async with self._lock:
            await self._verify(key, model_ids)
            try:
                write_dotenv_value(self._env_file, ENV_NAME, key)
            except OSError as exc:
                raise ModelError(
                    "save_failed",
                    f"The key works, but I couldn't save it to {self._env_file}.",
                    suggestion="Check that the folder is writable, then try again.",
                    detail=str(exc),
                ) from exc
            self._settings = self._settings.model_copy(update={"anthropic_api_key": SecretStr(key)})
            models = self._build(self._settings)
            self._brain.set_models(models)
        status = self._brain.status
        model = models.for_mode("fast")
        log.info("API key connected", extra={"key_hint": status.key_hint})
        await self._announce(
            f"Brain connected — {model.label if model else 'Claude'}", Severity.IMPORTANT, status
        )
        return status

    async def check(self) -> None:
        """Verify the key loaded at startup. A key the provider rejects takes the
        brain offline with the reason; network problems leave it as is."""
        secret = self._settings.anthropic_api_key
        if secret is None or not self._brain.available:
            return
        try:
            await self._verify(secret.get_secret_value(), anthropic_models(self._settings))
        except ModelError as exc:
            if exc.code not in _KEY_PROBLEMS:
                log.warning("could not verify the API key: %s", exc.message)
                return
            async with self._lock:
                if self._settings.anthropic_api_key is not secret:
                    return  # a new key was connected meanwhile
                hint = self._brain.status.key_hint
                reason = f"{exc.message} {exc.suggestion or ''}".strip()
                self._brain.set_models(ModelSet(None, None, reason, key_hint=hint))
            log.warning("reasoning offline: %s", exc.message, extra={"key_hint": hint})
            await self._announce(exc.message, Severity.WARNING, self._brain.status)
            return
        except Exception as exc:  # never let a background check crash anything
            log.warning("could not verify the API key: %s", type(exc).__name__)
            return
        log.info("API key verified", extra={"key_hint": self._brain.status.key_hint})

    async def _announce(self, message: str, severity: Severity, status: BrainStatus) -> None:
        await self._bus.emit(
            EventType.BRAIN_CHANGED,
            message=message,
            source="brain",
            severity=severity,
            payload={"brain": asdict(status)},
        )
