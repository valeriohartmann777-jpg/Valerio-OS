"""The APEX data repair: every cause found in docs/apex/APEX_DATA_DIAGNOSIS.md, reproduced.

- 3.3b/c — empty answers and 404s used to be cached as "fetched" for good, so an outage froze
  the cache without an error. They are re-checked now, and a run of them fails loudly.
- 3.1 — GAPS reported "100 % missing" for zero missing minutes when the data was old.
- 3.2 — SPIKES counted weekend and daily-break reopens as one-minute moves.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import numpy as np
import pytest

from jarvis.apex.data import Series, assess, trading_minutes
from jarvis.learning.market import Bars, DataError, Instrument, MarketData
from tests.learning_data import candles_json

GOLD = Instrument("XAUUSD", "XAUUSD", (900.0, 8900.0), "XAU-USD")
TODAY = datetime.now(UTC).date()


class Gold:
    """Dukascopy's data API for gold: ``empty`` days answer 200 without candles, ``missing``
    days 404, ``down`` answers 503. Ask candles are 0.30 above the bid."""

    def __init__(self) -> None:
        self.empty: set[date] = set()
        self.missing: set[date] = set()
        self.down = False
        self.today_status = 200
        self.requests: list[str] = []

    def asked(self, day: date, side: str = "BID") -> int:
        return self.requests.count(
            f"/v1/candles/minute/XAU-USD/{side}/{day.year}/{day.month}/{day.day}"
        )

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        self.requests.append(path)
        if path == "/v1/instruments":
            return httpx2.Response(200, json=[{"code": "XAU-USD"}])
        if self.down:
            return httpx2.Response(503)
        parts = path.split("/")
        side = parts[5]
        lift = 0.3 if side == "ASK" else 0.0
        if len(parts) == 6:  # today so far
            if self.today_status != 200:
                return httpx2.Response(self.today_status)
            rows = [(0, 4000.0 + lift, 4001.0 + lift, 3999.0 + lift, 4000.5 + lift, 1.0)]
            return httpx2.Response(200, json=candles_json(TODAY, rows, multiplier=0.01))
        day = date(int(parts[6]), int(parts[7]), int(parts[8]))
        if day in self.missing:
            return httpx2.Response(404)
        if day in self.empty:
            return httpx2.Response(200, json=candles_json(day, []))
        rows = [
            (i, 4000.0 + lift, 4001.0 + lift, 3999.0 + lift, 4000.5 + lift, 1.0) for i in range(3)
        ]
        return httpx2.Response(200, json=candles_json(day, rows, multiplier=0.01))


def gold(tmp_path: Path, feed: Gold) -> MarketData:
    return MarketData(
        tmp_path, transport=httpx2.MockTransport(feed), retry_delays=(), requests_per_second=0
    )


def trading_days(start: date, end: date) -> list[date]:
    return [
        start + timedelta(days=k)
        for k in range((end - start).days + 1)
        if (start + timedelta(days=k)).weekday() != 5
    ]


# 3.3 b/c — the cache heals after an outage ---------------------------------------------------


@pytest.mark.parametrize("answer", ["empty", "missing"])
async def test_an_outage_fails_loudly_and_the_cache_heals_when_the_api_is_back(
    tmp_path: Path, answer: str
) -> None:
    feed = Gold()
    data = gold(tmp_path, feed)
    start, end = TODAY - timedelta(days=60), TODAY - timedelta(days=1)
    await data.sync(GOLD, start, TODAY - timedelta(days=28))  # the cache before the outage
    outage = set(trading_days(TODAY - timedelta(days=27), end))
    getattr(feed, answer).update(outage)

    with pytest.raises(DataError, match=r"no XAUUSD candles for \d+ trading days in a row"):
        await data.sync(GOLD, start, end)
    report = data.reports(GOLD)[-1]
    assert report["error"] and report["empty_run"] and report["with_data"] == 0
    assert report["asked"] == len(outage)
    assert report["answers"] == {"200" if answer == "empty" else "404": len(outage)}
    # nothing was marked: the days are asked again on the next update
    getattr(feed, answer).clear()
    await data.sync(GOLD, start, end)
    coverage = data.coverage(GOLD)
    assert coverage is not None and coverage[1] == end
    assert all(s["state"] in ("data", "weekend") for s in data.days(GOLD, start, end))


async def test_a_cache_poisoned_before_the_fix_is_re_checked(tmp_path: Path) -> None:
    """Old versions stored empty answers as fetched days. Those trading days are asked again
    while younger than 45 days, older ones on a repair."""
    feed = Gold()
    data = gold(tmp_path, feed)
    start, end = TODAY - timedelta(days=80), TODAY - timedelta(days=1)
    await data.sync(GOLD, start, end)
    young, old = TODAY - timedelta(days=12), TODAY - timedelta(days=70)
    young = young if young.weekday() != 5 else young - timedelta(days=1)
    old = old if old.weekday() != 5 else old - timedelta(days=1)
    _poison(tmp_path, [young, old])
    states = {s["day"]: s["state"] for s in data.days(GOLD, start, end)}
    assert states[young.isoformat()] == states[old.isoformat()] == "stored_empty"

    await data.sync(GOLD, start, end)
    assert feed.asked(young) == 2 and feed.asked(old) == 1  # young healed, old left alone
    await data.sync(GOLD, start, end, recheck=True)  # repair
    assert feed.asked(old) == 2
    states = {s["day"]: s["state"] for s in data.days(GOLD, start, end)}
    assert states[young.isoformat()] == states[old.isoformat()] == "data"


def _poison(root: Path, days: list[date]) -> None:
    """Remove the bars of ``days`` but keep them listed as fetched (the old behaviour)."""
    for day in days:
        path = root / "XAUUSD" / f"{day.year:04d}-{day.month:02d}.npz"
        with np.load(path) as f:
            stored = {k: f[k] for k in f.files}
        lo = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
        keep = (stored["t"] < lo) | (stored["t"] >= lo + 86400)
        for k in ("t", "o", "h", "l", "c", "v"):
            stored[k] = stored[k][keep]
        np.savez_compressed(path, **stored)


async def test_a_holiday_is_checked_again_at_most_every_12_hours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = Gold()
    data = gold(tmp_path, feed)
    holiday = TODAY - timedelta(days=20)
    holiday = holiday if holiday.weekday() not in (5, 6) else holiday - timedelta(days=2)
    feed.empty.add(holiday)
    start, end = TODAY - timedelta(days=30), TODAY - timedelta(days=1)
    await data.sync(GOLD, start, end)  # a single empty weekday: no error
    await data.sync(GOLD, start, end)
    assert feed.asked(holiday) == 1
    state = next(s for s in data.days(GOLD, start, end) if s["day"] == holiday.isoformat())
    assert state["state"] == "empty" and state["checks"] == 1

    later = time.time() + 13 * 3600
    monkeypatch.setattr(time, "time", lambda: later)
    await data.sync(GOLD, start, end)
    assert feed.asked(holiday) == 2
    # asked twice, still nothing: a holiday — no longer reported as a missing day
    state = next(s for s in data.days(GOLD, start, end) if s["day"] == holiday.isoformat())
    assert state["state"] == "no_trading" and state["checks"] == 2

    very_old = TODAY - timedelta(days=200)
    very_old = very_old if very_old.weekday() not in (5, 6) else very_old - timedelta(days=2)
    feed.empty.add(very_old)
    await data.sync(GOLD, very_old, very_old)
    await data.sync(GOLD, very_old, very_old)
    assert feed.asked(very_old) == 1  # older than 45 days: settled unless repaired


async def test_every_update_leaves_a_report(tmp_path: Path) -> None:
    feed = Gold()
    data = gold(tmp_path, feed)
    await data.sync(GOLD, TODAY - timedelta(days=9), TODAY - timedelta(days=1))
    report = data.reports(GOLD)[-1]
    asked = len(trading_days(TODAY - timedelta(days=9), TODAY - timedelta(days=1)))
    assert report["kind"] == "sync" and report["asked"] == report["with_data"] == asked
    assert report["answers"] == {"200": asked} and report["error"] is None
    assert report["code"] == "XAU-USD" and report["side"] == "BID"

    feed.down = True
    with pytest.raises(DataError, match="can't reach"):
        await data.sync(GOLD, TODAY - timedelta(days=40), TODAY - timedelta(days=1))
    assert "can't reach" in data.reports(GOLD)[-1]["error"]

    feed.down, feed.today_status = False, 400
    with pytest.raises(DataError, match="rejected the request for today's data"):
        await data.today(GOLD)
    last = data.reports(GOLD)[-1]
    assert last["kind"] == "today" and "400" in last["error"]


async def test_ask_candles_have_their_own_cache(tmp_path: Path) -> None:
    feed = Gold()
    data = gold(tmp_path, feed)
    day = TODAY - timedelta(days=3)
    day = day if day.weekday() != 5 else day - timedelta(days=1)
    ask = GOLD.with_side("ASK")
    await data.sync(GOLD, day, day)
    await data.sync(ask, day, day)
    assert feed.asked(day, "BID") == feed.asked(day, "ASK") == 1
    assert (tmp_path / "XAUUSD-ASK").is_dir() and ask.folder == "XAUUSD-ASK"
    bid_bars, ask_bars = data.load(GOLD, day, day), data.load(ask, day, day)
    assert np.allclose(ask_bars.close - bid_bars.close, 0.3)


# 3.1 / 3.2 — the quality gate ---------------------------------------------------------------

AS_OF = int(datetime(2026, 10, 28, 14, 0, tzinfo=UTC).timestamp())


def walk(start: int, end: int, *, seed: int = 1, sigma: float = 0.00015) -> Bars:
    """Gap-free generated minute bars with large reopen moves after every time gap."""
    grid = trading_minutes(start, end)
    rng = np.random.default_rng(seed)
    r = rng.standard_normal(len(grid)) * sigma
    reopen = np.r_[False, np.diff(grid) > 60]
    r[reopen] = rng.standard_normal(int(reopen.sum())) * sigma * 25
    c = 4000.0 * np.exp(np.cumsum(r))
    o = np.r_[4000.0, c[:-1]]
    return Bars(
        grid, o, np.maximum(o, c) * (1 + sigma / 3), np.minimum(o, c) * (1 - sigma / 3), c,
        np.ones(len(grid)), 1,
    )  # fmt: skip


def pick(bars: Bars, keep: np.ndarray) -> Bars:
    return Bars(
        bars.t[keep], bars.open[keep], bars.high[keep], bars.low[keep], bars.close[keep],
        bars.volume[keep], 1,
    )  # fmt: skip


def gate(bars: Bars, as_of: int = AS_OF) -> dict[str, Any]:
    return assess(
        Series("XAUUSD", "dukascopy", "test", False, bars),
        as_of,
        price_range=(900.0, 8900.0),
        stale_minutes=30,
        window_days=21,
        need_days=120,
    )


def codes(quality: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["code"]: f for f in quality["findings"]}


def test_old_data_is_stale_and_not_reported_as_100_percent_gaps() -> None:
    """The owner's screenshot: newest bar 4 weeks old → STALE and BLOCK GAPS 100 % with zero
    missing minutes. Now: STALE plus NO_RECENT_DATA (the honest reason), no GAPS."""
    bars = walk(AS_OF - 150 * 86400, AS_OF - 27 * 86400)
    found = codes(gate(bars))
    assert found["STALE"]["count"] == 26220  # 19 trading days, as in the screenshot
    assert found["NO_RECENT_DATA"]["level"] == "BLOCK"
    assert "2026-10-01" in found["NO_RECENT_DATA"]["message"]
    assert "GAPS" not in found


def test_gaps_are_measured_where_the_data_is_and_say_where_they_are() -> None:
    bars = walk(AS_OF - 150 * 86400, AS_OF)
    hole = (bars.t >= AS_OF - 6 * 86400) & (bars.t < AS_OF - 4 * 86400)
    cut = pick(bars, ~hole)
    quality = gate(cut)
    gaps = codes(quality)["GAPS"]
    assert gaps["level"] == "WARN" and gaps["count"] == int(hole.sum())
    worst = quality["summary"]["largest_gaps"][0]
    assert worst["minutes"] == int(hole.sum()) and worst["t"] == int(bars.t[hole][0])
    # data that begins inside the window: the minutes before its first bar aren't "missing"
    short = walk(AS_OF - 10 * 86400, AS_OF)
    assert "GAPS" not in codes(gate(short))
    # more than half missing still blocks
    sparse = pick(bars, np.arange(len(bars)) % 3 == 0)
    assert codes(gate(sparse))["GAPS"]["level"] == "BLOCK"


def test_reopen_moves_are_no_spikes_but_a_bad_tick_is() -> None:
    bars = walk(AS_OF - 150 * 86400, AS_OF)
    assert "SPIKES" not in codes(gate(bars))  # used to flag every reopen
    # a bad tick: one close far off that comes straight back
    close = bars.close.copy()
    k = len(close) - 5000
    close[k] *= 1.02
    high, low = np.maximum(bars.high, close), np.minimum(bars.low, close)
    o = bars.open.copy()
    o[k + 1] = close[k]
    high[k + 1] = max(high[k + 1], o[k + 1])
    bad = Bars(bars.t, o, high, low, close, bars.volume, 1)
    spikes = codes(gate(bad))["SPIKES"]
    assert spikes["level"] == "WARN" and spikes["count"] >= 1
    assert gate(bad)["summary"]["spikes"][0]["t"] == int(bars.t[k])
    # a large move that stays (news) is listed, not called a bad tick
    jump = np.cumprod(np.r_[1.0, np.where(np.arange(1, len(close)) == k, 1.02, 1.0)])
    moved = Bars(bars.t, bars.open * jump, bars.high * jump, bars.low * jump, bars.close * jump,
                 bars.volume, 1)  # fmt: skip
    moved.open[k] = bars.close[k - 1] * jump[k - 1]
    moved.low[k] = min(moved.low[k], moved.open[k])
    found = codes(gate(moved))
    assert "SPIKES" not in found and found["LARGE_MOVES"]["level"] == "INFO"


def test_a_wick_far_beyond_its_neighbours_is_a_spike() -> None:
    bars = walk(AS_OF - 150 * 86400, AS_OF)
    high = bars.high.copy()
    k = len(high) - 3000
    high[k] = bars.close[k] * 1.03
    spikes = codes(gate(Bars(bars.t, bars.open, high, bars.low, bars.close, bars.volume, 1)))
    assert spikes["SPIKES"]["count"] == 1
