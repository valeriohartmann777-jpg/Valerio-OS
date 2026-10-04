"""The morning briefing: key levels, research notes, text, schedule, API, tool."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jarvis.api.app import create_app
from jarvis.briefing.levels import LevelRules, key_levels
from jarvis.briefing.service import (
    Briefing,
    BriefingError,
    BriefingService,
    annotate,
    compose,
    de_number,
)
from jarvis.core.trace import TraceContext
from jarvis.events.bus import EventBus
from jarvis.events.types import EventType
from jarvis.learning.journal import LearningJournal
from jarvis.learning.market import Bars, DayCandles, Instrument, MarketData, Progress
from jarvis.runtime import Runtime
from jarvis.settings import load_settings
from jarvis.storage.database import Database
from jarvis.storage.preferences import Preferences
from tests.conftest import Recorder, make_settings
from tests.learning_data import NY, session_bars, weekdays

NQ_RULES = LevelRules(round_step=100, major_step=500, merge_within=3, regular_session=True)
GOLD_RULES = LevelRules(round_step=10, major_step=50, merge_within=0.8, regular_session=False)
ZURICH = ZoneInfo("Europe/Zurich")


def around_the_clock(days: list[date], price: float, seed: int = 3, noise: float = 2.0) -> Bars:
    return session_bars(days, price=price, start="00:00", end="23:59", noise=noise, seed=seed)


def ny(day: date, hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return int(datetime(day.year, day.month, day.day, h, m, tzinfo=NY).timestamp())


# Levels -------------------------------------------------------------------------------


def test_nq_levels_come_from_the_regular_session_overnight_and_last_week() -> None:
    days = weekdays(date(2024, 3, 4), date(2024, 3, 13))  # Mon … Wed of the next week
    bars = around_the_clock(days, 18000.0)
    now = ny(date(2024, 3, 13), "07:30")  # Wednesday morning, before the open
    bars = bars.between(0, now)
    level_map = key_levels(bars, "NQ", NQ_RULES, each_side=50)
    labels = {label: lvl.price for lvl in level_map.above + level_map.below for label in lvl.labels}

    rth = (bars.t >= ny(date(2024, 3, 12), "09:30")) & (bars.t < ny(date(2024, 3, 12), "16:00"))
    assert labels["Hoch RTH Vortag"] == pytest.approx(bars.high[rth].max(), abs=3)
    assert labels["Tief RTH Vortag"] == pytest.approx(bars.low[rth].min(), abs=3)
    overnight = bars.t >= ny(date(2024, 3, 12), "18:00")
    assert level_map.ranges["Overnight"] == (bars.low[overnight].min(), bars.high[overnight].max())
    last_week = (bars.t >= ny(date(2024, 3, 3), "18:00")) & (bars.t < ny(date(2024, 3, 8), "18:00"))
    assert level_map.ranges["Vorwoche"] == (bars.low[last_week].min(), bars.high[last_week].max())
    assert level_map.price == bars.close[-1]
    assert all(lvl.price > level_map.price for lvl in level_map.above)
    assert all(lvl.price <= level_map.price for lvl in level_map.below)
    assert [lvl.price for lvl in level_map.above] == sorted(lvl.price for lvl in level_map.above)
    assert any("Runde Zahl" in label for label in labels)


def test_gold_uses_the_whole_day_and_the_asia_range() -> None:
    days = weekdays(date(2024, 3, 4), date(2024, 3, 8))
    bars = around_the_clock(days, 2150.0, noise=0.3).between(0, ny(date(2024, 3, 8), "04:00"))
    level_map = key_levels(bars, "XAUUSD", GOLD_RULES, each_side=50)
    asia = (bars.t >= ny(date(2024, 3, 7), "19:00")) & (bars.t < ny(date(2024, 3, 8), "03:00"))
    assert level_map.ranges["Asia"] == (bars.low[asia].min(), bars.high[asia].max())
    previous = (bars.t >= ny(date(2024, 3, 6), "18:00")) & (bars.t < ny(date(2024, 3, 7), "18:00"))
    labels = {label for lvl in level_map.above + level_map.below for label in lvl.labels}
    assert {"Hoch Vortag", "Tief Vortag", "POC Vortag"} <= labels
    high = next(lvl for lvl in level_map.above + level_map.below if "Hoch Vortag" in lvl.labels)
    assert high.price == pytest.approx(bars.high[previous].max(), abs=0.8)


def test_only_intact_swings_and_merged_confluence() -> None:
    day = date(2024, 3, 5)
    start = ny(day, "00:00")
    # Up to 110 by 02:00, down to 100, up to 120 (110 is taken out), back to 112.
    path = np.concatenate(
        [np.linspace(100, 110, 120), np.linspace(110, 100, 120), np.linspace(100, 120, 240),
         np.linspace(120, 112, 240)]
    )  # fmt: skip
    t = start + np.arange(len(path), dtype=np.int64) * 60
    bars = Bars(t, path, path + 0.05, path - 0.05, path, np.ones(len(path)))
    rules = LevelRules(round_step=1000, major_step=5000, merge_within=0.5, regular_session=False)
    level_map = key_levels(bars, "XAUUSD", rules, each_side=50)
    swing_highs = [lvl.price for lvl in level_map.above if "Swing-Hoch (15m)" in lvl.labels]
    assert swing_highs == [pytest.approx(120.05)]  # the 110 swing was broken; 120 holds
    # Levels within merge_within become one level with all their reasons.
    rules = LevelRules(round_step=10, major_step=50, merge_within=0.5, regular_session=False)
    merged = key_levels(bars, "XAUUSD", rules, each_side=50)
    top = next(lvl for lvl in merged.above if lvl.price == pytest.approx(120.0, abs=0.1))
    assert set(top.labels) == {"Runde Zahl", "Swing-Hoch (15m)"}
    assert set(top.kinds) == {"round", "swing_high"}


# Research notes and text ------------------------------------------------------------------


def _study(number: int, level: str, side: str, z: float, touches: int = 300) -> dict[str, Any]:
    return {
        "number": number,
        "ok": True,
        "spec": {"instrument": "NQ", "level": level, "side": side},
        "result": {"edge_z": z, "touches": touches, "held_rate": 0.58, "expected_rate": 0.41},
    }


def test_levels_carry_what_the_research_measured() -> None:
    days = weekdays(date(2024, 3, 4), date(2024, 3, 13))
    bars = around_the_clock(days, 18000.0).between(0, ny(date(2024, 3, 13), "07:30"))
    level_map = key_levels(bars, "NQ", NQ_RULES, each_side=50)
    studies = [
        _study(3, "prev_low", "support", 2.6),
        _study(4, "prev_high", "support", 3.0),  # wrong side for a high above the price
        _study(5, "round_above(100)", "resistance", 0.4),
    ]
    annotate(level_map, studies)
    previous_low = next(lv for lv in level_map.above + level_map.below if "prev_low" in lv.kinds)
    if previous_low in level_map.below:  # support: the support study applies
        assert previous_low.note == "Forschung S3: hält öfter als Zufall (58 % vs. 41 %, z 2,6)"
    else:  # price fell below it: now resistance, and no resistance study exists
        assert previous_low.note is None
    above_round = [lv for lv in level_map.above if "round" in lv.kinds]
    assert above_round and above_round[0].note is not None
    assert "kein Vorteil gemessen" in above_round[0].note


def test_german_numbers_and_the_briefing_text() -> None:
    assert de_number(21345.25, 2) == "21.345,25"
    assert de_number(2645.3, 2) == "2.645,30"
    days = weekdays(date(2024, 3, 4), date(2024, 3, 13))
    level_map = key_levels(
        around_the_clock(days, 18000.0).between(0, ny(date(2024, 3, 13), "07:30")),
        "NQ",
        NQ_RULES,
    )
    briefing = Briefing(text="", maps={"NQ": level_map}, errors={"XAUUSD": "HTTP 503"})
    text = compose(
        briefing, datetime(2024, 3, 13, 8, 0, tzinfo=ZURICH), {"NQ": 2}, "Forschung bisher: …"
    )
    assert text.startswith("Guten Morgen — Briefing für Mittwoch, 13. März 2024")
    assert "\nNQ " in text and "Widerstände:" in text and "Unterstützungen:" in text
    assert "Gold: Kursdaten gerade nicht verfügbar (HTTP 503)" in text
    assert text.rstrip().endswith("Forschung bisher: …")


# Service -------------------------------------------------------------------------------


class FakeMarket(MarketData):
    def __init__(self, bars: dict[str, Bars]) -> None:
        super().__init__(Path("/nonexistent"))
        self.bars = bars
        self.synced: list[str] = []

    async def sync(
        self, instrument: Instrument, start: date, end: date, progress: Progress | None = None
    ) -> int:
        self.synced.append(instrument.name)
        return 0

    def load(self, instrument: Instrument, start: date, end: date) -> Bars:
        return self.bars[instrument.name]

    async def today(self, instrument: Instrument) -> DayCandles:
        f = np.empty(0)
        return DayCandles(np.empty(0, dtype=np.int64), f, f, f, f, f)


def market() -> FakeMarket:
    days = weekdays(date(2024, 3, 4), date(2024, 3, 13))
    cut = ny(date(2024, 3, 13), "07:30")
    return FakeMarket(
        {
            "NQ": around_the_clock(days, 18000.0).between(0, cut),
            "XAUUSD": around_the_clock(days, 2150.0, noise=0.3).between(0, cut),
        }
    )


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


async def make_service(tmp_path: Path, clock: Clock) -> tuple[BriefingService, Recorder, list[str]]:
    settings = load_settings(environ={})
    bus = EventBus()
    db = Database(":memory:")
    await db.connect()
    sent: list[str] = []
    service = BriefingService(
        settings=settings.briefing,
        learning=settings.learning,
        market=market(),
        journal=LearningJournal(db),
        bus=bus,
        preferences=Preferences(tmp_path / "prefs.json"),
        on_sent=sent.append,
        now=clock,
    )
    return service, Recorder(bus), sent


async def test_the_schedule(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 5, 7, 59, tzinfo=ZURICH))  # Monday
    service, _, _ = await make_service(tmp_path, clock)
    assert not service.due()
    assert service.status.next_at == "2026-10-05T08:00:00+02:00"
    clock.moment = datetime(2026, 10, 5, 8, 0, tzinfo=ZURICH)
    assert service.due()
    clock.moment = datetime(2026, 10, 5, 10, 59, tzinfo=ZURICH)  # catching up
    assert service.due()
    await service.send()
    assert not service.due() and service.status.last_sent == "2026-10-05"
    assert service.status.next_at == "2026-10-06T08:00:00+02:00"
    clock.moment = datetime(2026, 10, 6, 11, 0, tzinfo=ZURICH)  # too late for Tuesday
    assert not service.due()
    clock.moment = datetime(2026, 10, 10, 9, 0, tzinfo=ZURICH)  # Saturday
    assert not service.due() and service.status.next_at == "2026-10-12T08:00:00+02:00"

    await service.set_preferences(at="07:15", enabled=True)
    assert service.status.time == "07:15"
    with pytest.raises(BriefingError, match="not a time"):
        await service.set_preferences(at="7 Uhr")
    await service.set_preferences(enabled=False)
    clock.moment = datetime(2026, 10, 12, 9, 0, tzinfo=ZURICH)
    assert not service.due() and service.status.next_at is None


async def test_sending_posts_the_briefing_in_the_chat(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 5, 8, 0, tzinfo=ZURICH))
    service, events, sent = await make_service(tmp_path, clock)
    text = await service.send()
    [message] = events.of(EventType.JARVIS_MESSAGE)
    assert message.payload["kind"] == "briefing" and message.payload["text"] == text
    assert message.payload["speech"].startswith("Guten Morgen")
    assert "NQ " in text and "Gold " in text and "Widerstände:" in text
    assert sent == [text]
    assert service.status.last_error is None


async def test_the_loop_catches_up_after_a_late_start(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 5, 9, 30, tzinfo=ZURICH))
    service, events, _ = await make_service(tmp_path, clock)
    service._check_seconds = 0.01
    await service.start()
    try:
        for _ in range(300):
            if events.of(EventType.JARVIS_MESSAGE):
                break
            await __import__("asyncio").sleep(0.01)
    finally:
        await service.stop()
    assert len(events.of(EventType.JARVIS_MESSAGE)) == 1  # once, not every minute


def test_briefing_api_and_tool(tmp_path: Path) -> None:
    runtime = Runtime(make_settings(tmp_path), market=market())
    with TestClient(create_app(runtime=runtime)) as client:
        status = client.get("/briefing").json()
        assert status["enabled"] is False  # tests keep it off
        changed = client.post("/briefing/preferences", json={"enabled": True, "time": "07:45"})
        assert changed.json()["time"] == "07:45" and changed.json()["enabled"] is True
        assert client.post("/briefing/preferences", json={"time": "late"}).status_code == 422
        text = client.post("/briefing/send").json()["text"]
        assert text.startswith("Guten Morgen")
        assert client.get("/conversation").json()[-1]["payload"]["kind"] == "briefing"

        result = client.portal.call(  # type: ignore[union-attr]
            lambda: runtime.executor.run(
                "market_levels", {"instrument": "NQ"}, ctx=TraceContext.new(), reason="test"
            )
        )
        assert result.success and "NQ" in result.data["levels"]
        assert "XAUUSD" not in result.data["levels"]
