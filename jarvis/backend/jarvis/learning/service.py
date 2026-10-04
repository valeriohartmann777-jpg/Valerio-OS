"""The learning loop: JARVIS studies trading on its own, within a daily budget.

Each round: make sure the market data is there → brief Claude (notes, earlier
results, the current out-of-sample bar) → Claude runs backtests, writes notes
and finishes the round → wait → next round.

Limits that code enforces, not the model:
- Money: the day's spend (from the API's usage) plus the worst case of the
  next call must stay within ``daily_budget_usd``; otherwise it waits for
  tomorrow.
- Progress: after ``stall_limit`` completed rounds without a new validated
  finding, learning stops itself and says so. Starting it again grants a fresh
  ``stall_limit`` rounds.
- Scope: the research model has three tools — run_backtest, write_note,
  finish_round — and optionally Anthropic's web search. Nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import ValidationError

from jarvis.events.bus import EventBus
from jarvis.events.types import Event, EventType, Severity
from jarvis.learning.evaluate import Outcome, evaluate, t_required
from jarvis.learning.journal import LearningJournal
from jarvis.learning.levels import LevelStudy, run_study
from jarvis.learning.market import Bars, DataError, Instrument, MarketData
from jarvis.learning.prompt import SYSTEM_PROMPT, TOOLS, briefing
from jarvis.learning.strategy import Strategy
from jarvis.llm.base import ModelError, ModelReply, ToolDefinition, ToolOutcome, Usage
from jarvis.settings import LearningSettings
from jarvis.storage.preferences import Preferences
from jarvis.util import utcnow

log = logging.getLogger("jarvis.learning")

ENABLED = "learning.enabled"
STALL_SINCE = "learning.stall_since"
FOCUS = "learning.focus"
MAX_FOCUS = 1500
_RETRYABLE = {"rate_limited", "overloaded", "timeout", "connection", "server_error"}
_SYNC_EVERY = 12 * 3600
_NOTE_REF = re.compile(r"^[Nn]?(\d+)$")
WEB_SEARCH_TOOL = "web_search_20250305"


class ResearchModel(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def label(self) -> str: ...

    async def complete(
        self,
        *,
        system: str,
        messages: list[Any],
        tools: list[ToolDefinition],
        server_tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply: ...

    def user_message(self, blocks: list[str]) -> Any: ...

    def tool_results(self, outcomes: list[ToolOutcome]) -> Any: ...


class LearningState(StrEnum):
    OFF = "off"
    PREPARING = "preparing"  # downloading market data
    RUNNING = "running"  # a round is in progress
    WAITING = "waiting"  # between rounds
    BUDGET = "budget"  # today's budget is used up
    STALLED = "stalled"  # stopped itself: no progress
    NEEDS_BRAIN = "needs_brain"  # no usable Anthropic key
    ERROR = "error"


@dataclass(frozen=True)
class LearningStatus:
    state: LearningState
    enabled: bool
    detail: str | None
    progress: float | None
    model: str
    budget_usd: float
    spent_today_usd: float
    stall_rounds: int
    stall_limit: int
    next_round_at: str | None
    round: int | None
    web_search: bool
    focus: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    data: dict[str, dict[str, Any]] = field(default_factory=dict)


class LearningService:
    def __init__(
        self,
        *,
        settings: LearningSettings,
        bus: EventBus,
        journal: LearningJournal,
        market: MarketData,
        preferences: Preferences,
        model_factory: Callable[[], ResearchModel | None],
        today: Callable[[], date] = lambda: datetime.now().astimezone().date(),
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        yesterday_utc: Callable[[], date] | None = None,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._journal = journal
        self._market = market
        self._prefs = preferences
        self._model_factory = model_factory
        self._today = today
        self._now = now
        self._yesterday_utc = yesterday_utc or (
            lambda: (datetime.now().astimezone() - timedelta(days=1)).date()
        )
        self._instruments = {
            name: Instrument(name, spec.symbol, spec.price_range, spec.code)
            for name, spec in settings.instruments.items()
        }
        self._state = LearningState.OFF
        self._detail: str | None = None
        self._progress: float | None = None
        self._next_round_at: str | None = None
        self._round: int | None = None
        self._web_search = settings.web_search
        self._spent_today = 0.0
        self._stall_rounds = 0
        self._counts: dict[str, int] = {}
        self._coverage: dict[str, dict[str, Any]] = {}
        self._synced_at = 0.0
        self._bars: dict[str, Bars] = {}
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._unsubscribe = bus.subscribe(EventType.BRAIN_CHANGED, self._on_brain)

    # Status ------------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._prefs.get(ENABLED, False))

    @property
    def focus(self) -> str:
        """What to research: the user's text from the Learning page, else the config's."""
        chosen = self._prefs.get(FOCUS)
        return str(chosen) if isinstance(chosen, str) else self._settings.focus.strip()

    async def set_focus(self, text: str) -> LearningStatus:
        """Empty text goes back to the default from config/learning.yaml."""
        clean = " ".join(text.split())[:MAX_FOCUS]
        self._prefs.update(**{FOCUS: clean or None})
        await self._emit("Research focus changed", Severity.INFO)
        return self.status

    @property
    def status(self) -> LearningStatus:
        return LearningStatus(
            state=self._state,
            enabled=self.enabled,
            detail=self._detail,
            progress=self._progress,
            model=self._settings.model,
            budget_usd=self._settings.daily_budget_usd,
            spent_today_usd=round(self._spent_today, 4),
            stall_rounds=self._stall_rounds,
            stall_limit=self._settings.stall_limit,
            next_round_at=self._next_round_at,
            round=self._round,
            web_search=self._web_search,
            focus=self.focus,
            counts=dict(self._counts),
            data=dict(self._coverage),
        )

    async def _refresh(self) -> None:
        self._spent_today = await self._journal.spent_on(self._today().isoformat())
        self._stall_rounds = await self._journal.rounds_without_progress(
            self._prefs.get(STALL_SINCE)
        )
        self._counts = await self._journal.counts()

    async def _set(
        self,
        state: LearningState,
        detail: str | None = None,
        *,
        message: str = "",
        severity: Severity = Severity.DEBUG,
        progress: float | None = None,
        next_round_at: datetime | None = None,
    ) -> None:
        await self._refresh()
        self._state, self._detail, self._progress = state, detail, progress
        self._next_round_at = next_round_at.isoformat() if next_round_at else None
        await self._emit(message or detail or f"Learning: {state}", severity)

    async def _emit(self, message: str, severity: Severity = Severity.DEBUG) -> None:
        await self._bus.emit(
            EventType.LEARNING_CHANGED,
            message=message,
            source="learning",
            severity=severity,
            payload={"learning": asdict(self.status)},
        )

    # Lifecycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        """At JARVIS start: resume if learning was on."""
        interrupted = await self._journal.interrupted()
        if interrupted:
            log.info("marked %d interrupted learning round(s)", interrupted)
        self._refresh_coverage()
        await self._refresh()
        if self.enabled:
            self._spawn()

    async def stop(self) -> None:
        """At JARVIS shutdown: stop the loop, keep the user's choice."""
        self._unsubscribe()
        await self._cancel()

    async def enable(self) -> LearningStatus:
        async with self._lock:
            # UTC like the journal's timestamps, so the two compare as text.
            self._prefs.update(**{ENABLED: True, STALL_SINCE: utcnow().isoformat()})
            self._web_search = self._settings.web_search
            if self._task is None or self._task.done():
                await self._set(
                    LearningState.PREPARING,
                    "Starting…",
                    message="Learning started",
                    severity=Severity.INFO,
                )
                self._spawn()
            self._wake.set()
        return self.status

    async def disable(self) -> LearningStatus:
        async with self._lock:
            self._prefs.update(**{ENABLED: False})
            await self._cancel()
            await self._set(LearningState.OFF, message="Learning stopped", severity=Severity.INFO)
        return self.status

    def _spawn(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="learning")

    async def _cancel(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._round = None
        self._bars.clear()

    async def _on_brain(self, _: Event) -> None:
        self._wake.set()  # a new key: try again now

    async def _wait(self, seconds: float) -> None:
        self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.0, seconds))

    # Loop --------------------------------------------------------------------------------

    async def _loop(self) -> None:
        while self.enabled:
            try:
                if not await self._one_cycle():
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let the loop die silently
                log.exception("learning cycle failed")
                await self._set(
                    LearningState.ERROR,
                    f"Something went wrong ({type(exc).__name__}). I'll try again in 10 minutes.",
                    severity=Severity.WARNING,
                )
                await self._wait(600)

    async def _one_cycle(self) -> bool:
        """One step of the loop. False ends the loop."""
        model = self._model_factory()
        if model is None:
            await self._set(
                LearningState.NEEDS_BRAIN,
                "Connect Claude under Settings → Brain — learning uses it.",
            )
            await self._wait(3600)
            return True
        if self._settings.model not in self._settings.prices:
            await self._set(
                LearningState.ERROR,
                f"No price for {self._settings.model} in config/learning.yaml — "
                "the budget needs it.",
                severity=Severity.WARNING,
            )
            await self._wait(3600)
            return True
        try:
            await self._prepare_data()
        except DataError as exc:
            await self._set(
                LearningState.ERROR,
                f"{exc} I'll try again in 30 minutes.",
                severity=Severity.WARNING,
            )
            await self._wait(1800)
            return True

        await self._refresh()
        if self._stall_rounds >= self._settings.stall_limit:
            self._prefs.update(**{ENABLED: False})
            await self._set(
                LearningState.STALLED,
                f"No new validated finding in {self._settings.stall_limit} rounds — "
                "learning stopped itself. Start it again to give it another "
                f"{self._settings.stall_limit} rounds.",
                severity=Severity.WARNING,
            )
            return False
        if not self._room_for_a_call():
            tomorrow = datetime.combine(self._today() + timedelta(days=1), datetime.min.time())
            resume = tomorrow.replace(tzinfo=self._now().tzinfo) + timedelta(minutes=1)
            await self._set(
                LearningState.BUDGET,
                f"Today's ${self._settings.daily_budget_usd:.2f} are used up — "
                "learning continues tomorrow.",
                next_round_at=resume,
                severity=Severity.INFO,
            )
            await self._wait((resume - self._now()).total_seconds())
            return True

        try:
            await self._run_round(model)
        except ModelError as exc:
            retry = exc.code in _RETRYABLE
            await self._set(
                LearningState.ERROR,
                f"{exc.message} "
                + ("I'll try again in 5 minutes." if retry else (exc.suggestion or "")),
                severity=Severity.WARNING,
            )
            await self._wait(300 if retry else 1800)
            return True
        finally:
            self._round = None
            self._bars.clear()  # minute history is large; load it again next round

        await self._refresh()
        if not self._room_for_a_call():
            return True  # the next cycle reports the budget and waits for tomorrow
        interval = self._settings.round_interval_minutes * 60
        await self._set(
            LearningState.WAITING, next_round_at=self._now() + timedelta(seconds=interval)
        )
        await self._wait(interval)
        return True

    def _room_for_a_call(self, messages: list[Any] | None = None) -> bool:
        reserve = self._call_reserve(SYSTEM_PROMPT, messages)
        return self._spent_today + reserve <= self._settings.daily_budget_usd

    # Data --------------------------------------------------------------------------------

    async def _prepare_data(self) -> None:
        if time.monotonic() - self._synced_at < _SYNC_EVERY and self._synced_at:
            return
        start, end = self._settings.data_start, self._yesterday_utc()
        names = list(self._instruments)
        last_report = [0.0]
        for index, name in enumerate(names):

            async def progress(done: int, total: int, index: int = index, name: str = name) -> None:
                share = (index + done / max(1, total)) / len(names)
                if time.monotonic() - last_report[0] >= 1.0 or done == total:
                    last_report[0] = time.monotonic()
                    await self._set(
                        LearningState.PREPARING,
                        f"Downloading {name} minute data ({share:.0%})",
                        progress=share,
                    )

            await self._market.sync(self._instruments[name], start, end, progress)
        self._synced_at = time.monotonic()
        self._refresh_coverage()

    def _refresh_coverage(self) -> None:
        coverage: dict[str, dict[str, Any]] = {}
        for name, instrument in self._instruments.items():
            found = self._market.coverage(instrument)
            if found:
                first, last, bars = found
                coverage[name] = {
                    "first": first.isoformat(),
                    "last": last.isoformat(),
                    "bars": bars,
                }
        self._coverage = coverage

    async def _minute_bars(self, name: str) -> Bars:
        if name not in self._bars:
            instrument = self._instruments[name]
            self._bars[name] = await asyncio.to_thread(
                self._market.load, instrument, self._settings.data_start, self._yesterday_utc()
            )
        return self._bars[name]

    # Round -------------------------------------------------------------------------------

    async def _run_round(self, model: ResearchModel) -> None:
        day = self._today().isoformat()
        round_id, number = await self._journal.start_round(day)
        self._round = number
        await self._set(LearningState.RUNNING, f"Round {number}: reading notes and results")
        messages: list[Any] = [model.user_message([await self._briefing(number)])]
        tests = validated = searches = studies = 0
        summary = focus = ""
        try:
            for _ in range(self._settings.max_steps_per_round):
                await self._refresh()
                if not self._room_for_a_call(messages):
                    summary = summary or "Stopped early: today's budget is used up."
                    break
                reply = await self._complete(model, messages, searches)
                searches += reply.usage.web_searches
                await self._journal.add_usage(
                    round_id,
                    cost=self._cost(reply.usage),
                    input_tokens=reply.usage.input_tokens
                    + reply.usage.cache_read_tokens
                    + reply.usage.cache_write_tokens,
                    output_tokens=reply.usage.output_tokens,
                    searches=reply.usage.web_searches,
                )
                messages.append(reply.assistant_message)
                if reply.stop_reason == "pause_turn":
                    continue  # a web search is still running server-side
                if not reply.tool_calls:
                    if reply.stop_reason == "max_tokens":
                        messages.append(
                            model.user_message(["You were cut off. Be brief and use a tool."])
                        )
                        continue
                    summary = summary or reply.text[:400]
                    break
                outcomes: list[ToolOutcome] = []
                finished = False
                for call in reply.tool_calls:
                    if call.name == "run_backtest":
                        if tests >= self._settings.tests_per_round:
                            outcomes.append(
                                ToolOutcome(
                                    call.id,
                                    "This round's backtests are used up. Record what you "
                                    "learned and call finish_round.",
                                    is_error=True,
                                )
                            )
                            continue
                        tests += 1
                        await self._set(LearningState.RUNNING, f"Round {number}: backtest {tests}")
                        text, ok, passed = await self._backtest(call.input, round_id)
                        validated += passed
                        outcomes.append(ToolOutcome(call.id, text, is_error=not ok))
                    elif call.name == "study_levels":
                        if studies >= self._settings.studies_per_round:
                            outcomes.append(
                                ToolOutcome(
                                    call.id,
                                    "This round's level studies are used up.",
                                    is_error=True,
                                )
                            )
                            continue
                        studies += 1
                        await self._set(
                            LearningState.RUNNING, f"Round {number}: level study {studies}"
                        )
                        text, ok = await self._study(call.input, round_id)
                        outcomes.append(ToolOutcome(call.id, text, is_error=not ok))
                    elif call.name == "write_note":
                        text, ok = await self._note(call.input, round_id)
                        outcomes.append(ToolOutcome(call.id, text, is_error=not ok))
                    elif call.name == "finish_round":
                        summary = str(call.input.get("summary", ""))[:400]
                        focus = str(call.input.get("next_focus", ""))[:300]
                        finished = True
                        outcomes.append(ToolOutcome(call.id, "Round finished."))
                    else:
                        outcomes.append(
                            ToolOutcome(call.id, f"Unknown tool {call.name}.", is_error=True)
                        )
                if finished:
                    break
                messages.append(model.tool_results(outcomes))
        except ModelError as exc:
            await self._journal.finish_round(round_id, "failed", error=exc.message)
            raise
        except asyncio.CancelledError:
            await self._journal.finish_round(round_id, "interrupted")
            raise
        await self._journal.finish_round(round_id, "completed", summary=summary, next_focus=focus)
        await self._refresh()
        found = f", {validated} validated" if validated else ""
        studied = f"{studies} level stud{'ies' if studies != 1 else 'y'}, " if studies else ""
        await self._emit(
            f"Learning round {number}: {studied}{tests} test{'s' * (tests != 1)}{found}"
            + (f" — {summary}" if summary else ""),
            Severity.INFO,
        )

    async def _complete(
        self, model: ResearchModel, messages: list[Any], searches_used: int
    ) -> ModelReply:
        left = self._settings.searches_per_round - searches_used
        server_tools = (
            [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": left}]
            if self._web_search and left > 0
            else []
        )
        try:
            return await model.complete(
                system=SYSTEM_PROMPT, messages=messages, tools=TOOLS, server_tools=server_tools
            )
        except ModelError as exc:
            if server_tools and exc.code in ("bad_request", "permission") and _mentions_search(exc):
                log.warning("web search isn't available for this key; continuing without it")
                self._web_search = False
                return await model.complete(
                    system=SYSTEM_PROMPT, messages=messages, tools=TOOLS, server_tools=[]
                )
            raise

    async def _briefing(self, number: int) -> str:
        oos = await self._journal.oos_evaluations()
        data = {
            name: f"{info['first']} to {info['last']}, {info['bars']:,} bars"
            for name, info in self._coverage.items()
        }
        in_sample_end = self._settings.oos_start - timedelta(days=1)
        return briefing(
            round_number=number,
            today=self._today().isoformat(),
            budget_left=max(0.0, self._settings.daily_budget_usd - self._spent_today),
            data=data,
            in_sample=(self._settings.data_start.isoformat(), in_sample_end.isoformat()),
            oos_checks=oos,
            next_bar=t_required(oos + 1, self._settings.gates.oos_alpha),
            notes=await self._journal.notes(),
            validated=await self._journal.validated_for_prompt(),
            tests=await self._journal.prompt_tests(20),
            focus=await self._journal.last_focus(),
            tests_per_round=self._settings.tests_per_round,
            searches=self._settings.searches_per_round if self._web_search else 0,
            research_focus=self.focus,
            studies=await self._journal.prompt_studies(15),
            studies_per_round=self._settings.studies_per_round,
        )

    async def _backtest(self, raw: dict[str, Any], round_id: str) -> tuple[str, bool, int]:
        name = str(raw.get("name") or "unnamed")[:80]
        stored = raw if len(json.dumps(raw)) <= 8000 else {"name": name, "truncated": True}
        try:
            strategy = Strategy.model_validate(raw)
        except ValidationError as exc:
            reason = _validation_message(exc)
            number = await self._journal.add_test(
                round_id, name, stored, Outcome("invalid", reason)
            )
            return f"T{number} is not a valid strategy: {reason}", False, 0
        bars = await self._minute_bars(strategy.instrument)
        before = await self._journal.oos_evaluations()
        outcome = await asyncio.to_thread(
            evaluate, strategy, bars, self._settings, oos_evaluations_before=before
        )
        number = await self._journal.add_test(
            round_id, strategy.name, strategy.model_dump(mode="json", exclude_none=True), outcome
        )
        market = f"{strategy.instrument} {strategy.style} {strategy.timeframe}"
        if outcome.status == "validated":
            await self._emit(
                f"Validated finding: T{number} {strategy.name} ({market}) held up out-of-sample",
                Severity.IMPORTANT,
            )
        else:
            await self._emit(f"T{number} {strategy.name} ({market}): {outcome.reason}")
        checks = before + (1 if outcome.out_of_sample is not None else 0)
        result: dict[str, Any] = {
            "test": f"T{number}",
            "result": outcome.status,
            "why": outcome.reason,
            "next_out_of_sample_bar": round(
                t_required(checks + 1, self._settings.gates.oos_alpha), 2
            ),
        }
        if outcome.in_sample is not None:
            result["in_sample"] = outcome.in_sample.brief()
        if outcome.skipped_small_stop:
            result["signals_skipped_for_tiny_stops"] = outcome.skipped_small_stop
        return json.dumps(result), True, int(outcome.status == "validated")

    async def _study(self, raw: dict[str, Any], round_id: str) -> tuple[str, bool]:
        name = str(raw.get("name") or "unnamed")[:80]
        stored = raw if len(json.dumps(raw)) <= 8000 else {"name": name, "truncated": True}
        try:
            study = LevelStudy.model_validate(raw)
        except ValidationError as exc:
            reason = _validation_message(exc)
            number = await self._journal.add_study(
                round_id, name, stored, {"error": reason}, ok=False
            )
            return f"S{number} is not a valid study: {reason}", False
        bars = await self._minute_bars(study.instrument)
        result = await asyncio.to_thread(
            run_study, study, bars, self._settings.data_start, self._settings.oos_start
        )
        number = await self._journal.add_study(
            round_id, study.name, study.model_dump(mode="json", exclude_none=True), result
        )
        held, chance, z = result["held_rate"], result["expected_rate"], result["edge_z"]
        summary = (
            f"held {held:.0%} vs {chance:.0%} by chance (z {z:+.1f})"
            if held is not None and chance is not None and z is not None
            else "too few touches to judge"
        )
        await self._emit(
            f"S{number} {study.name} ({study.instrument} {study.side}): "
            f"{result['touches']} touches, {summary}"
        )
        return json.dumps({"study": f"S{number}", **result}), True

    async def _note(self, raw: dict[str, Any], round_id: str) -> tuple[str, bool]:
        topic = str(raw.get("topic") or "").strip()[:60]
        text = str(raw.get("text") or "").strip()[:700]
        if not topic or not text:
            return "A note needs a topic and a text.", False
        sources = [
            str(s)[:300]
            for s in (raw.get("sources") or [])[:5]
            if isinstance(s, str) and s.startswith(("http://", "https://"))
        ]
        replaces = None
        if raw.get("replaces"):
            match = _NOTE_REF.match(str(raw["replaces"]).strip())
            if not match or not await self._journal.note_exists(int(match.group(1))):
                return f"There is no note {raw['replaces']} to replace.", False
            replaces = int(match.group(1))
        number = await self._journal.add_note(
            topic, text, sources, round_id=round_id, replaces=replaces
        )
        await self._emit(f"Learned: {topic} — {text[:140]}")
        return f"Saved as N{number}" + (f" (replaces N{replaces})." if replaces else "."), True

    # Money -------------------------------------------------------------------------------

    def _cost(self, usage: Usage) -> float:
        price = self._settings.prices[self._settings.model]
        tokens = (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_read_tokens * price.cache_read
            + usage.cache_write_tokens * price.cache_write
        )
        return tokens / 1_000_000 + usage.web_searches * self._settings.web_search_usd

    def _call_reserve(self, system: str, messages: list[Any] | None = None) -> float:
        """Worst case of the next model call: every input token uncached and
        written to the cache, the full output allowance, all searches."""
        price = self._settings.prices[self._settings.model]
        chars = len(system) + len(json.dumps(messages or [], default=str)) + 6000  # + tools
        input_tokens = chars / 3
        searches = self._settings.searches_per_round if self._web_search else 0
        return (
            input_tokens * max(price.input, price.cache_write)
            + self._settings.max_tokens * price.output
        ) / 1_000_000 + searches * self._settings.web_search_usd


def _mentions_search(exc: ModelError) -> bool:
    text = f"{exc.message} {exc.detail or ''}".lower()
    return "web_search" in text or "web search" in text


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:4]:
        where = ".".join(str(p) for p in error["loc"])
        message = str(error["msg"]).removeprefix("Value error, ")
        parts.append(f"{where}: {message}" if where else message)
    return "; ".join(parts)
