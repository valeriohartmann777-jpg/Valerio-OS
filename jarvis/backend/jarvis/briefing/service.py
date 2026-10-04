"""The morning briefing: on weekdays at a set time JARVIS posts today's key
levels for NQ and gold in the chat, each annotated with what its own research
measured about that kind of level.

Started after the set time (the Mac was asleep, JARVIS was closed)? The
briefing still comes until ``catch_up_until``. It is checked every minute
rather than slept towards, because timers don't run while a Mac sleeps.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

import numpy as np

from jarvis.briefing.levels import KIND_EXPRESSIONS, KeyLevel, LevelMap, LevelRules, key_levels
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType, Severity
from jarvis.learning.journal import LearningJournal
from jarvis.learning.market import Bars, DataError, Instrument, MarketData
from jarvis.settings import BriefingSettings, LearningSettings
from jarvis.storage.preferences import Preferences

log = logging.getLogger("jarvis.briefing")

ENABLED = "briefing.enabled"
TIME = "briefing.time"
LAST_SENT = "briefing.last_sent"
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]  # fmt: skip
NAMES = {"NQ": "NQ", "XAUUSD": "Gold"}


class BriefingError(ValueError):
    pass


@dataclass(frozen=True)
class BriefingStatus:
    enabled: bool
    time: str
    weekdays: list[int]
    catch_up_until: str
    next_at: str | None
    last_sent: str | None
    last_error: str | None


@dataclass
class Briefing:
    text: str
    maps: dict[str, LevelMap] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def _clock(text: str) -> time:
    match = _HHMM.match(text)
    if not match:
        raise BriefingError(f"{text!r} is not a time like 08:00")
    return time(int(match.group(1)), int(match.group(2)))


def de_number(value: float, decimals: int) -> str:
    """21345.25 → 21.345,25"""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _percent(value: float) -> str:
    return f"{round(value * 100)} %"


class BriefingService:
    def __init__(
        self,
        *,
        settings: BriefingSettings,
        learning: LearningSettings,
        market: MarketData,
        journal: LearningJournal,
        bus: EventBus,
        preferences: Preferences,
        on_sent: Callable[[str], None] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        check_seconds: float = 60.0,
    ) -> None:
        self._settings = settings
        self._learning = learning
        self._market = market
        self._journal = journal
        self._bus = bus
        self._prefs = preferences
        self._on_sent = on_sent
        self._now = now
        self._check_seconds = check_seconds
        self._last_error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._sending = asyncio.Lock()

    # Settings ------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        value = self._prefs.get(ENABLED)
        return self._settings.enabled if value is None else bool(value)

    @property
    def time(self) -> str:
        value = self._prefs.get(TIME)
        return value if isinstance(value, str) and _HHMM.match(value) else self._settings.time

    @property
    def status(self) -> BriefingStatus:
        next_at = self._next_at()
        return BriefingStatus(
            enabled=self.enabled,
            time=self.time,
            weekdays=list(self._settings.weekdays),
            catch_up_until=self._settings.catch_up_until,
            next_at=next_at.isoformat() if next_at else None,
            last_sent=self._prefs.get(LAST_SENT),
            last_error=self._last_error,
        )

    async def set_preferences(
        self, *, enabled: bool | None = None, at: str | None = None
    ) -> BriefingStatus:
        if at is not None:
            _clock(at)
            self._prefs.update(**{TIME: at})
        if enabled is not None:
            self._prefs.update(**{ENABLED: enabled})
        self._wake.set()
        return self.status

    # Schedule -----------------------------------------------------------------------

    def _sent_today(self, now: datetime) -> bool:
        last: object = self._prefs.get(LAST_SENT)
        return last == now.date().isoformat()

    def due(self) -> bool:
        now = self._now()
        if not self.enabled or now.weekday() not in self._settings.weekdays:
            return False
        start, until = _clock(self.time), _clock(self._settings.catch_up_until)
        return not self._sent_today(now) and start <= now.time() < max(until, start)

    def _next_at(self) -> datetime | None:
        if not self.enabled or not self._settings.weekdays:
            return None
        now = self._now()
        at = _clock(self.time)
        for offset in range(8):
            day = now.date() + timedelta(days=offset)
            if day.weekday() not in self._settings.weekdays:
                continue
            moment = datetime.combine(day, at, tzinfo=now.tzinfo)
            if offset == 0 and (self._sent_today(now) or moment < now):
                if self.due():
                    return now
                continue
            return moment
        return None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="briefing")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            if self.due():
                try:
                    await self.send()
                except Exception as exc:  # never let the schedule die
                    log.exception("morning briefing failed")
                    self._last_error = f"{type(exc).__name__}: {exc}"
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._check_seconds)

    # Content ------------------------------------------------------------------------

    async def build(self, only: str | None = None) -> Briefing:
        """Levels for the configured markets (or one), as text and data."""
        briefing = Briefing(text="")
        studies = await self._journal.studies(limit=200)
        for name, rules in self._settings.instruments.items():
            if only and name != only:
                continue
            spec = self._learning.instruments.get(name)
            if spec is None:
                continue
            instrument = Instrument(name, spec.symbol, spec.price_range, spec.code)
            try:
                bars = await self._bars(instrument)
                level_map = key_levels(
                    bars,
                    name,
                    LevelRules(
                        round_step=rules.round_step,
                        major_step=rules.major_step,
                        merge_within=rules.merge_within,
                        regular_session=rules.regular_session,
                    ),
                    each_side=self._settings.levels_each_side,
                )
            except (DataError, ValueError) as exc:
                briefing.errors[name] = str(exc) or "no data"
                continue
            annotate(level_map, studies)
            briefing.maps[name] = level_map
        briefing.text = compose(
            briefing,
            self._now(),
            {n: r.decimals for n, r in self._settings.instruments.items()},
            await self._research_line(),
        )
        return briefing

    async def send(self) -> str:
        """Post the briefing in the chat now."""
        async with self._sending:
            briefing = await self.build()
            now = self._now()
            self._prefs.update(**{LAST_SENT: now.date().isoformat()})
            self._last_error = "; ".join(f"{k}: {v}" for k, v in briefing.errors.items()) or None
            await self._bus.emit(
                EventType.JARVIS_MESSAGE,
                message=briefing.text.split("\n", 1)[0],
                source="briefing",
                severity=Severity.IMPORTANT,
                payload={
                    "text": briefing.text,
                    "success": True,
                    "error": None,
                    "kind": "briefing",
                    "speech": "Guten Morgen. Das Briefing für NQ und Gold steht im Chat.",
                },
            )
            if self._on_sent is not None:
                self._on_sent(briefing.text)
            return briefing.text

    async def _bars(self, instrument: Instrument) -> Bars:
        today = datetime.now(UTC).date()
        start = today - timedelta(days=self._settings.history_days)
        end = today - timedelta(days=1)
        await self._market.sync(instrument, start, end)
        history = await asyncio.to_thread(self._market.load, instrument, start, end)
        live = await self._market.today(instrument)
        newer = live.t > (history.t[-1] if len(history) else 0)
        bars = Bars(
            np.concatenate([history.t, live.t[newer]]),
            np.concatenate([history.open, live.o[newer]]),
            np.concatenate([history.high, live.h[newer]]),
            np.concatenate([history.low, live.low[newer]]),
            np.concatenate([history.close, live.c[newer]]),
            np.concatenate([history.volume, live.v[newer]]),
        )
        if len(bars) == 0:
            raise DataError(f"No recent {instrument.name} data.")
        return bars

    async def _research_line(self) -> str:
        counts = await self._journal.counts()
        studies = [s for s in await self._journal.studies(limit=500) if s["ok"]]
        strong = sum(1 for s in studies if ((s["result"] or {}).get("edge_z") or 0) >= 2)
        validated = counts.get("validated", 0)
        return (
            f"Forschung bisher: {len(studies)} Level-Studie{'' if len(studies) == 1 else 'n'}, "
            f"{strong} mit messbarem Vorteil, {validated} "
            f"{'bestätigtes Setup' if validated == 1 else 'bestätigte Setups'}. "
            "Statistik aus der Vergangenheit, keine Handelsempfehlung."
        )


def annotate(level_map: LevelMap, studies: list[dict[str, Any]]) -> None:
    """Attach the most informative matching level study to each level."""
    for side, levels in (("resistance", level_map.above), ("support", level_map.below)):
        for level in levels:
            level.note = _study_note(level, side, level_map.instrument, studies)


def _study_note(
    level: KeyLevel, side: str, instrument: str, studies: list[dict[str, Any]]
) -> str | None:
    prefixes = tuple(p for kind in level.kinds for p in KIND_EXPRESSIONS.get(kind, ()))
    if not prefixes:
        return None
    matching = [
        s
        for s in studies
        if s["ok"]
        and (s["spec"] or {}).get("instrument") == instrument
        and (s["spec"] or {}).get("side") == side
        and str((s["spec"] or {}).get("level", "")).replace(" ", "").startswith(prefixes)
        and (s["result"] or {}).get("edge_z") is not None
    ]
    if not matching:
        return None
    best = max(matching, key=lambda s: s["result"].get("touches", 0))
    result = best["result"]
    z = float(result["edge_z"])
    held, expected = result.get("held_rate"), result.get("expected_rate")
    if held is None or expected is None:
        return None
    verdict = "hält öfter als Zufall" if z >= 2 else "kein Vorteil gemessen"
    return (
        f"Forschung S{best['number']}: {verdict} "
        f"({_percent(held)} vs. {_percent(expected)}, z {de_number(z, 1)})"
    )


def compose(briefing: Briefing, now: datetime, decimals: dict[str, int], research: str) -> str:
    lines = [
        f"Guten Morgen — Briefing für {WEEKDAYS[now.weekday()]}, "
        f"{now.day}. {MONTHS[now.month - 1]} {now.year}"
    ]
    for name in [*briefing.maps, *briefing.errors]:
        label = NAMES.get(name, name)
        lines.append("")
        if name in briefing.errors:
            lines.append(f"{label}: Kursdaten gerade nicht verfügbar ({briefing.errors[name]})")
            continue
        level_map = briefing.maps[name]
        places = decimals.get(name, 2)
        as_of = datetime.fromtimestamp(level_map.as_of, UTC).astimezone(now.tzinfo)
        lines.append(f"{label} {de_number(level_map.price, places)} (Stand {as_of:%H:%M})")
        sides = (("Widerstände", level_map.above), ("Unterstützungen", level_map.below))
        for title, levels in sides:
            if not levels:
                continue
            lines.append(f"{title}:")
            for level in levels:
                text = f"• {de_number(level.price, places)} — {' + '.join(level.labels)}"
                if level.note:
                    text += f" · {level.note}"
                lines.append(text)
        if level_map.ranges:
            spans = [
                f"{key} {de_number(low, places)} bis {de_number(high, places)}"
                for key, (low, high) in level_map.ranges.items()
            ]
            lines.append("Bereiche: " + " · ".join(spans))
    lines += ["", research]
    return "\n".join(lines)
