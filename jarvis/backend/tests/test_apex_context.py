"""APEX Ultimate U2: bias, path events, zone interactions and point-in-time macro context.

Hand-built bars and generated test data only. The tests check definitions: the path outcomes
form one exclusive partition with ambiguity kept apart, nothing after the moment counts, a
release at 08:30 New York cannot reach an 08:29 analysis (in winter and in summer time),
and a missing related-market feed never corroborates anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from jarvis.apex.context import (
    OUTCOMES,
    bias,
    combine,
    daily_bars,
    first_touch,
    interaction_scenarios,
    market_context,
)
from jarvis.apex.data import SYNTHETIC_LABEL, Series, synthetic_bars
from jarvis.apex.engine import analyze
from jarvis.apex.macro import parse_calendar
from jarvis.apex.service import market_contexts
from jarvis.learning.market import Bars
from jarvis.runtime import Runtime
from jarvis.settings import load_settings
from tests.conftest import make_settings

SETTINGS = load_settings(environ={})
CFG = SETTINGS.apex
CONTEXTS = market_contexts(SETTINGS)
NY = ZoneInfo("America/New_York")
T = int(datetime(2026, 3, 18, 15, 0, tzinfo=UTC).timestamp())


# -- path events -----------------------------------------------------------------------------------


def test_first_touch_keeps_same_minute_touches_ambiguous() -> None:
    high = np.array([100.5, 101.2, 100.8])
    low = np.array([99.6, 99.5, 98.0])
    assert first_touch(high, low, 101.0, 98.5) == "UPPER_FIRST"
    assert first_touch(high, low, 102.0, 98.5) == "LOWER_FIRST"
    assert first_touch(high, low, 103.0, 97.0) == "NEITHER"
    assert first_touch(np.array([100.2, 101.5]), np.array([99.8, 98.0]), 101.0, 98.5) == "AMBIGUOUS"


@pytest.mark.parametrize("market", ["XAUUSD", "NQ"])
def test_path_shares_are_one_partition_and_ignore_later_bars(market: str) -> None:
    ctx = CONTEXTS[market]
    full = synthetic_bars(market, T + 86400, ctx.spec.tick).between(T - 80 * 86400, T + 86400)
    later = full.t >= T
    o, h, lo, c = (
        np.where(later, x * 0.8, x) for x in (full.open, full.high, full.low, full.close)
    )
    lifted = Bars(full.t, o, h, lo, c, full.volume, 1)
    a = analyze(Series(market, "fixture", SYNTHETIC_LABEL, True, full), ctx, CFG, T)
    b = analyze(Series(market, "fixture", SYNTHETIC_LABEL, True, lifted), ctx, CFG, T)
    for key in ("paths", "bias", "interactions", "path_cards"):
        assert a[key] == b[key], key
    paths = a["paths"]
    assert paths["available"] and set(paths["counts"]) == set(OUTCOMES)
    assert paths["samples"] == sum(paths["counts"].values()) >= CFG.min_samples
    assert abs(sum(paths["shares"].values()) - 1.0) < 1e-3  # exclusive and exhaustive
    assert paths["status"] == "NOT_VALIDATED" and paths["calibrated"] is False
    assert paths["barriers"]["upper"] > paths["price"] > paths["barriers"]["lower"]
    inter = a["interactions"]
    assert inter["samples"] == sum(inter["counts"].values())
    assert inter["status"] in ("NOT_VALIDATED", "INSUFFICIENT_DATA")


def test_interaction_counts_are_one_per_episode() -> None:
    zm = {
        "support": [{"episodes": [{"kind": "rejection"}, {"kind": "break"}], "transitions": []}],
        "resistance": [
            {
                "episodes": [{"kind": "sweep_reclaim"}, {"kind": "pending"}],
                "transitions": [{"reason": "fakeout: closed back"}],
            }
        ],
        "others": [],
    }
    out = interaction_scenarios(zm, min_samples=2)
    assert out["counts"] == {
        "REJECT_AND_ROTATE": 1,
        "SWEEP_RECLAIM": 1,
        "BREAK_AND_ACCEPT": 1,
        "WEAK_REACTION": 0,
        "UNRESOLVED": 1,
    }
    assert out["fakeouts_after_breaks"] == 1 and sum(out["shares"].values()) == pytest.approx(1.0)


# -- bias ------------------------------------------------------------------------------------------


def trending(sign: float, days: int = 40) -> Bars:
    """A trend with cycles on three scales, each rising (falling) at the end of the series."""
    t = T - days * 86400 + np.arange(days * 1440, dtype=np.int64) * 60
    x = (t - t[0]) / 86400.0 - days
    wave = (
        10 * np.sin(2 * np.pi * x / 6) + 4 * np.sin(2 * np.pi * x) + 1.5 * np.sin(2 * np.pi * x * 6)
    )
    close = 1000 + sign * (6.0 * (x + days) + wave)
    open_ = np.r_[close[0], close[:-1]]
    high, low = np.maximum(open_, close) + 0.2, np.minimum(open_, close) - 0.2
    return Bars(t, open_, high, low, close, np.ones(len(t)), 1)


def test_bias_reads_every_timeframe_and_says_when_they_disagree() -> None:
    up = bias(trending(+1), T)
    assert up["state"] == "BULLISH" and up["invalidation"] and up["is_signal"] is False
    assert {up["timeframes"][k]["trend"] for k in ("D1", "H4", "H1")} <= {"up", "range"}
    down = bias(trending(-1), T)
    assert down["state"] == "BEARISH"
    assert combine(["up", "down", "range"]) == "CONFLICTED"
    assert combine(["range", "range", "unknown"]) == "NEUTRAL"
    days = daily_bars(trending(+1, days=5), T)
    assert len(days) <= 5 and np.all(np.diff(days.t) > 0)


# -- macro context ---------------------------------------------------------------------------------


CALENDAR = """event,scheduled,timezone,importance,category,actual,released
US CPI m/m,2026-03-12 08:30,America/New_York,high,inflation,0.3%,2026-03-12 08:30
US Nonfarm Payrolls,2026-01-09 08:30,America/New_York,high,labour,256K,2026-01-09 08:30
US Jobless Claims,2026-03-12 08:30,America/New_York,medium,labour,,
Broken row,,America/New_York,high,,,
"""


def ny(y: int, m: int, d: int, hh: int, mm: int) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=NY).timestamp())


def test_calendar_times_are_dst_correct_and_refused_rows_say_why() -> None:
    rows, refused = parse_calendar(CALENDAR, "owner file")
    cpi = next(r for r in rows if r["event"] == "US CPI m/m")
    nfp = next(r for r in rows if r["event"] == "US Nonfarm Payrolls")
    assert cpi["scheduled_utc"] == int(datetime(2026, 3, 12, 12, 30, tzinfo=UTC).timestamp())  # EDT
    assert nfp["scheduled_utc"] == int(datetime(2026, 1, 9, 13, 30, tzinfo=UTC).timestamp())  # EST
    assert "assumed" in cpi["source"] and cpi["schedule_known_utc"] < cpi["scheduled_utc"]
    assert len(refused) == 1 and "required" in refused[0]["reason"]


@pytest.mark.parametrize("day", [(2026, 3, 12), (2026, 1, 9)])
def test_a_release_at_0830_cannot_reach_an_0829_analysis(day: tuple[int, int, int]) -> None:
    rows, _ = parse_calendar(CALENDAR, "owner file")
    before = market_context(
        "XAUUSD", ny(*day, 8, 29), rows, before_minutes=30, after_minutes=15, calendar_loaded=True
    )
    event = next(e for e in before["events"] if e["importance"] == "high")
    assert event["actual"] is None and event["minutes_away"] == 1.0
    assert before["blackout"]["active"] and before["macro"]["status"] == "CONTRADICTED"
    after = market_context(
        "XAUUSD", ny(*day, 8, 46), rows, before_minutes=30, after_minutes=15, calendar_loaded=True
    )
    released = next(e for e in after["events"] if e["importance"] == "high")
    assert released["actual"] is not None and not after["blackout"]["active"]
    assert after["macro"]["status"] == "CONFIRMED"


def test_missing_calendar_and_related_markets_are_unavailable_never_support() -> None:
    out = market_context("NQ", T, [], before_minutes=30, after_minutes=15, calendar_loaded=False)
    assert out["macro"]["status"] == "UNAVAILABLE"
    assert {m["status"] for m in out["related_markets"]} == {"UNAVAILABLE"}
    assert {m["market"] for m in out["related_markets"]} >= {"ES futures", "VIX"}


@pytest.fixture
async def rt(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path)
    apex = settings.apex.model_copy(update={"provider": "fixture", "history_days": 40})
    runtime = Runtime(settings.model_copy(update={"apex": apex}))
    runtime.apex._now = lambda: T  # type: ignore[method-assign]
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


async def test_importing_a_calendar_changes_context_not_the_analysis(rt: Runtime) -> None:
    before = await rt.apex.analyze("XAUUSD")
    assert before["context"]["macro"]["status"] == "UNAVAILABLE"
    out = await rt.apex.import_macro(CALENDAR, "owner file")
    assert out == {"parsed": 3, "added": 3, "refused": out["refused"]} and len(out["refused"]) == 1
    again = await rt.apex.import_macro(CALENDAR, "owner file")
    assert again["added"] == 0  # the same events are stored once
    ctx = await rt.apex.context("XAUUSD", T)
    assert ctx["macro"]["status"] == "CONFIRMED"  # 18 March: no release in the window
    after = await rt.apex.analyze("XAUUSD", as_of=T)
    for key in ("zone_map", "paths", "bias"):
        assert after[key] == before[key]
