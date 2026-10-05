"""Trains JARVIS's support/resistance model in the background — locally, free.

Every ``check_minutes`` it looks for a trading day of market data it hasn't
learned from yet, downloading what's missing first. If there is one, it
rebuilds the data set from all history, trains and judges the model (see
``model.py``) in a worker thread with ``threads`` CPU threads, and keeps the
new model. "Train now" on the Learning page does the same on request.

No Claude, no API costs: this runs entirely on this computer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

from threadpoolctl import threadpool_limits

from jarvis.briefing.levels import LevelRules
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.learning.market import DataError, Instrument, MarketData
from jarvis.learning.strategy import clock_minutes
from jarvis.settings import BriefingSettings, LearningSettings, TrainingSettings
from jarvis.storage.preferences import Preferences
from jarvis.training.dataset import Dataset, TouchRules, build
from jarvis.training.live import level_odds
from jarvis.training.model import Periods, Trained, train
from jarvis.training.store import Bundle, TrainingStore
from jarvis.util import wait_wall

log = logging.getLogger("jarvis.training")

ENABLED = "training.enabled"
_RETRY_AFTER_FAILURE = 2 * 3600


class TrainingState(StrEnum):
    OFF = "off"
    WAITING = "waiting"  # up to date; checks for new data now and then
    PREPARING = "preparing"  # downloading market data
    TRAINING = "training"
    ERROR = "error"


@dataclass(frozen=True)
class Market:
    name: str
    instrument: Instrument
    levels: LevelRules
    window: tuple[int, int]  # New York minutes


@dataclass(frozen=True)
class TrainingStatus:
    state: TrainingState
    enabled: bool
    detail: str | None
    progress: float | None
    next_check_at: str | None
    model: dict[str, Any] | None  # the model in use
    report: dict[str, Any] | None  # the latest finished run's report
    report_run: int | None
    history: list[dict[str, Any]] = field(default_factory=list)


def touch_rules(settings: TrainingSettings) -> TouchRules:
    return TouchRules(
        timeframe=settings.timeframe_minutes,
        horizon_minutes=settings.horizon_minutes,
        tolerance=settings.tolerance_atr,
        hold=settings.hold_atr,
        breach=settings.breach_atr,
        history_days=settings.history_days,
        round_steps=settings.round_steps_each_side,
    )


def markets(
    settings: TrainingSettings, learning: LearningSettings, briefing: BriefingSettings
) -> list[Market]:
    out = []
    for name, window in settings.sessions.items():
        spec, rules = learning.instruments.get(name), briefing.instruments.get(name)
        if spec is None or rules is None:
            continue
        out.append(
            Market(
                name,
                Instrument(name, spec.symbol, spec.price_range, spec.code),
                LevelRules(
                    round_step=rules.round_step,
                    major_step=rules.major_step,
                    merge_within=rules.merge_within,
                    regular_session=rules.regular_session,
                ),
                (clock_minutes(window.start), clock_minutes(window.end)),
            )
        )
    return out


class TrainingService:
    def __init__(
        self,
        *,
        settings: TrainingSettings,
        learning: LearningSettings,
        briefing: BriefingSettings,
        market: MarketData,
        store: TrainingStore,
        bus: EventBus,
        preferences: Preferences,
        today_utc: Callable[[], date] = lambda: datetime.now(UTC).date(),
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._settings = settings
        self._learning = learning
        self._market = market
        self._store = store
        self._bus = bus
        self._prefs = preferences
        self._today_utc = today_utc
        self._now = now
        self.markets = markets(settings, learning, briefing)
        self.rules = touch_rules(settings)
        self._state = TrainingState.OFF
        self._detail: str | None = None
        self._progress: float | None = None
        self._next_check_at: str | None = None
        self._bundle: Bundle | None = None
        self._report: tuple[int, str, dict[str, Any]] | None = None
        self._history: list[dict[str, Any]] = []
        self._requested = False
        self._failed_at: float | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    # Status ------------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        value = self._prefs.get(ENABLED)
        return self._settings.enabled if value is None else bool(value)

    @property
    def bundle(self) -> Bundle | None:
        """The model in use, if any."""
        return self._bundle

    @property
    def status(self) -> TrainingStatus:
        model = None
        if self._bundle is not None:
            report = self._bundle.trained.report
            model = {
                "run": self._bundle.run,
                "data_until": self._bundle.data_until,
                "status": report.get("status"),
                "chosen": report.get("chosen"),
                "usable_for": report.get("usable_for", []),
            }
        return TrainingStatus(
            state=self._state,
            enabled=self.enabled,
            detail=self._detail,
            progress=self._progress,
            next_check_at=self._next_check_at,
            model=model,
            report=self._report[2] if self._report else None,
            report_run=self._report[0] if self._report else None,
            history=list(self._history),
        )

    async def _refresh(self) -> None:
        self._report = await self._store.latest_report()
        self._history = await self._store.runs(limit=30)

    async def _set(
        self,
        state: TrainingState,
        detail: str | None = None,
        *,
        progress: float | None = None,
        message: str = "",
        severity: Severity = Severity.DEBUG,
        next_check: datetime | None = None,
    ) -> None:
        self._state, self._detail, self._progress = state, detail, progress
        self._next_check_at = next_check.isoformat() if next_check else None
        await self._emit(message or detail or f"Training: {state}", severity)

    async def _emit(self, message: str, severity: Severity = Severity.DEBUG) -> None:
        await self._bus.emit(
            EventType.TRAINING_CHANGED,
            message=message,
            source="training",
            severity=severity,
            payload={"training": asdict(self.status)},
        )

    # Lifecycle ---------------------------------------------------------------------------

    async def start(self) -> None:
        interrupted = await self._store.interrupted()
        if interrupted:
            log.info("marked %d interrupted training run(s)", interrupted)
        self._bundle = await asyncio.to_thread(self._store.load)
        await self._refresh()
        if not self.enabled:
            self._state = TrainingState.OFF
        self._task = asyncio.create_task(self._loop(), name="training")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def set_enabled(self, enabled: bool) -> TrainingStatus:
        self._prefs.update(**{ENABLED: enabled})
        if not enabled and not self._running:
            await self._set(
                TrainingState.OFF, message="Training turned off", severity=Severity.INFO
            )
        self._wake.set()
        return self.status

    async def train_now(self) -> TrainingStatus:
        if self._running:
            return self.status
        self._requested = True
        self._wake.set()
        return self.status

    def briefing_line(self) -> str | None:
        """One German sentence about the model for the morning briefing."""
        if self._report is None:
            return None
        report = self._report[2]
        status = report.get("status")
        if status == "confirmed":
            markets = " und ".join("Gold" if m == "XAUUSD" else m for m in report["usable_for"])
            return (
                f"Eigenes Modell: auf ungesehenen Monaten besser als die einfache Basis "
                f"({markets}). Frag mich in der Session, ob ein Level hält."
            )
        if status == "too_little_data":
            return "Eigenes Modell: noch zu wenige Daten für ein Urteil."
        return "Eigenes Modell: bisher kein belegter Vorteil gegenüber der einfachen Basis."

    async def odds(self, name: str) -> dict[str, Any]:
        """The trained model's odds for the levels near ``name``'s price now."""
        item = next((m for m in self.markets if m.name == name), None)
        if item is None:
            raise ValueError(f"no market {name!r}")
        days = max(21, self._settings.history_days + 5)
        bars = await self._market.recent(item.instrument, days)
        return await asyncio.to_thread(
            level_odds, self._bundle, bars, item.name, item.levels, item.window, self.rules
        )

    # Loop --------------------------------------------------------------------------------

    def _due(self) -> bool:
        if self._requested:
            return True
        if not self.enabled or not self.markets:
            return False
        if (
            self._failed_at is not None
            and time.monotonic() - self._failed_at < _RETRY_AFTER_FAILURE
        ):
            return False
        if self._report is None:
            return True
        yesterday = (self._today_utc() - timedelta(days=1)).isoformat()
        if self._report[1] < yesterday:
            return True
        # Trained, but the model file is gone or from another version.
        return self._bundle is None and bool(self._report[2].get("chosen"))

    async def _loop(self) -> None:
        while True:
            if self._due():
                try:
                    await self._run()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # never let the loop die silently
                    log.exception("training failed")
                    self._failed_at = time.monotonic()
                    await self._set(
                        TrainingState.ERROR,
                        f"Training failed ({type(exc).__name__}). I'll try again later.",
                        severity=Severity.WARNING,
                    )
            seconds = self._settings.check_minutes * 60
            if self._state not in (TrainingState.ERROR,):
                if self.enabled:
                    await self._set(
                        TrainingState.WAITING,
                        self._waiting_detail(),
                        next_check=self._now() + timedelta(seconds=seconds),
                    )
                elif self._state != TrainingState.OFF:
                    await self._set(TrainingState.OFF)
            self._wake.clear()
            await wait_wall(self._wake, seconds)

    def _waiting_detail(self) -> str:
        if self._report is None:
            return "Not trained yet."
        return f"Up to date with market data until {self._report[1]}."

    async def _run(self) -> None:
        manual, self._requested = self._requested, False
        self._running = True
        try:
            end = await self._prepare()
            if end is None:
                return
            last = self._report[1] if self._report else None
            lost = self._bundle is None and bool(self._report and self._report[2].get("chosen"))
            if not manual and last is not None and end.isoformat() <= last and not lost:
                return  # no new trading day yet
            await self._train(end)
        finally:
            self._running = False

    async def _prepare(self) -> date | None:
        """Download what's missing; the last day of data every market has."""
        start = self._learning.data_start
        yesterday = self._today_utc() - timedelta(days=1)
        problem = None
        for k, item in enumerate(self.markets):
            await self._set(
                TrainingState.PREPARING,
                f"Getting {item.name} market data",
                progress=k / len(self.markets),
            )
            try:
                await self._market.sync(item.instrument, start, yesterday)
            except DataError as exc:
                problem = str(exc)  # train on what is cached, if it's enough
        lasts = []
        for item in self.markets:
            found = await asyncio.to_thread(self._market.coverage, item.instrument)
            if found is None:
                self._failed_at = time.monotonic()
                await self._set(
                    TrainingState.ERROR,
                    problem or f"No {item.name} market data yet.",
                    severity=Severity.WARNING,
                )
                return None
            lasts.append(found[1])
        return min(lasts)

    async def _train(self, end: date) -> None:
        run_id, number = await self._store.start_run()
        await self._set(
            TrainingState.TRAINING,
            "Building the data set",
            progress=0.0,
            message=f"Training run {number} started",
            severity=Severity.INFO,
        )
        loop = asyncio.get_running_loop()
        last_report = [0.0]

        def progress(text: str, share: float) -> None:
            moment = time.monotonic()
            if moment - last_report[0] < 1.0:
                return
            last_report[0] = moment
            asyncio.run_coroutine_threadsafe(self._progress_update(text, share), loop)

        started = time.monotonic()
        try:
            trained = await asyncio.to_thread(self._train_blocking, end, progress)
            if trained.model is not None:
                bundle = Bundle(trained, number, end.isoformat())
                await asyncio.to_thread(self._store.save, bundle)
                self._bundle = bundle
        except Exception as exc:
            await self._store.fail_run(
                run_id, error=f"{type(exc).__name__}: {exc}", seconds=time.monotonic() - started
            )
            await self._refresh()
            raise
        await self._store.finish_run(
            run_id,
            data_until=end.isoformat(),
            report=trained.report,
            seconds=time.monotonic() - started,
        )
        self._failed_at = None
        await self._refresh()
        verdict = trained.report.get("status", "")
        await self._emit(f"Training run {number} finished: {verdict}", Severity.INFO)

    async def _progress_update(self, text: str, share: float) -> None:
        if self._state == TrainingState.TRAINING:
            await self._set(TrainingState.TRAINING, text, progress=round(share, 3))

    def _train_blocking(self, end: date, progress: Callable[[str, float], None]) -> Trained:
        parts = []
        for k, item in enumerate(self.markets):
            progress(f"Finding {item.name} level touches", 0.1 * k / len(self.markets))
            bars = self._market.load(item.instrument, self._learning.data_start, end)
            parts.append(
                build(bars, item.name, item.levels, item.window, self.rules, market_index=k)
            )
        data = Dataset.concat(parts)
        periods = Periods(
            oos_start=self._learning.oos_start,
            holdout_start=self._learning.holdout_start,
            horizon_seconds=self._settings.horizon_minutes * 60,
        )
        with threadpool_limits(limits=self._settings.threads):
            return train(
                data,
                periods,
                min_holdout=self._settings.min_holdout_touches,
                progress=progress,
            )
