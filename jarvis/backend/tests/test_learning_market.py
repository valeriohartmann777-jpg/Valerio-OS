"""Dukascopy's data API, the download cache, resampling and local time."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import numpy as np
import pytest

from jarvis.learning.market import (
    Bars,
    DataError,
    Instrument,
    MarketData,
    decode_candles,
    find_code,
    local_clock,
    resample,
)
from tests.learning_data import candles_json

NQ = Instrument("NQ", "USATECHIDXUSD", (4000.0, 39000.0), "USATECH.IDX-USD")
GOLD = Instrument("XAUUSD", "XAUUSD", (900.0, 8900.0), "XAU-USD")
DAY = date(2024, 3, 5)
DAY_START = int(datetime(2024, 3, 5, tzinfo=UTC).timestamp())


def test_decode_follows_the_reference_implementation() -> None:
    """The first candles of dukascopy-node's own fixture (BTC-USD, 2023-02-19)."""
    payload = {
        "timestamp": 1676764800000,
        "multiplier": 0.1,
        "open": 24599.3,
        "high": 24599.3,
        "low": 24593.0,
        "close": 24593.2,
        "shift": 60000,
        "times": [0, 1, 1, 1],
        "opens": [0, -57, 31, 57],
        "highs": [0, -26, 68, 16],
        "lows": [0, 2, 25, 32],
        "closes": [0, 33, 60, -32],
        "volumes": [0.001062, 8.46e-4, 0.001557, 9.0e-4],
    }
    candles = decode_candles(payload, date(2023, 2, 19), (10000, 99999), "BTC")
    assert candles.t.tolist() == [1676764800 + 60 * i for i in range(4)]
    assert candles.o.tolist() == [24599.3, 24593.6, 24596.7, 24602.4]
    assert candles.h.tolist() == [24599.3, 24596.7, 24603.5, 24605.1]
    assert candles.low.tolist() == [24593.0, 24593.2, 24595.7, 24598.9]
    assert candles.c.tolist() == [24593.2, 24596.5, 24602.5, 24599.3]


def test_decode_skips_gaps_and_flat_minutes() -> None:
    rows = [
        (570, 20000.0, 20001.5, 19999.5, 20001.0, 2.5),  # 09:30 UTC
        (571, 20001.0, 20001.0, 20001.0, 20001.0, 0.0),  # no trading: dropped
        (575, 20001.25, 20003.0, 20000.75, 20002.5, 1.0),  # after a gap
    ]
    candles = decode_candles(candles_json(DAY, rows), DAY, NQ.price_range, "NQ")
    assert candles.t.tolist() == [DAY_START + 570 * 60, DAY_START + 575 * 60]
    assert candles.o.tolist() == [20000.0, 20001.25]
    assert candles.h.tolist() == [20001.5, 20003.0]
    assert candles.low.tolist() == [19999.5, 20000.75]
    assert candles.c.tolist() == [20001.0, 20002.5]
    assert candles.v.tolist() == [2.5, 1.0]


def test_an_empty_day_is_no_trading() -> None:
    assert len(decode_candles(candles_json(DAY, []), DAY, NQ.price_range).t) == 0


@pytest.mark.parametrize(
    "payload",
    [
        "<html>maintenance</html>",
        {"times": [0, 1]},
        {**candles_json(DAY, [(0, 2000.0, 2001.0, 1999.0, 2000.5, 1.0)]), "opens": [0, 1]},
        {**candles_json(DAY, [(0, 2000.0, 2001.0, 1999.0, 2000.5, 1.0)]), "multiplier": 0},
    ],
)
def test_malformed_answers_are_refused(payload: Any) -> None:
    with pytest.raises(DataError, match="isn't minute candles"):
        decode_candles(payload, DAY, GOLD.price_range, "XAUUSD")


def test_candles_that_dont_add_up_are_refused() -> None:
    rows = [(i, 2000.0, 1999.0, 2001.0, 2000.0, 1.0) for i in range(50)]  # high < low
    with pytest.raises(DataError, match="doesn't look like minute candles"):
        decode_candles(candles_json(DAY, rows), DAY, GOLD.price_range, "XAUUSD")
    late = candles_json(DAY, [(0, 2000.0, 2001.0, 1999.0, 2000.5, 1.0)])
    late["timestamp"] = int(late["timestamp"]) + 86_400_000  # type: ignore[call-overload]
    with pytest.raises(DataError, match="doesn't look like minute candles"):
        decode_candles(late, DAY, GOLD.price_range, "XAUUSD")


def test_implausible_prices_are_refused() -> None:
    rows = [(0, 20000.0, 20001.0, 19999.0, 20000.5, 1.0)]
    with pytest.raises(DataError, match="expected range"):
        decode_candles(candles_json(DAY, rows), DAY, GOLD.price_range, "XAUUSD")


def test_codes_come_from_the_instrument_list() -> None:
    listing = [{"code": "EUR-USD", "name": "EUR/USD"}, {"code": "USATECH.IDX-USD", "name": "x"}]
    assert find_code(listing, "USATECHIDXUSD") == "USATECH.IDX-USD"
    assert find_code({"instruments": [{"code": "XAU-USD", "name": "XAU/USD"}]}, "XAUUSD") == (
        "XAU-USD"
    )
    assert find_code({"unexpected": True}, "XAUUSD") is None


class Feed:
    """A stand-in for Dukascopy's data API."""

    def __init__(
        self,
        *,
        missing: set[date] | None = None,
        failing: set[date] | None = None,
        fail_first: int = 0,
        status: int = 200,
        listing: Any = None,
        codes: tuple[str, ...] = ("USATECH.IDX-USD", "XAU-USD"),
    ) -> None:
        self.missing = missing or set()
        self.failing = failing or set()
        self.fail_first = fail_first
        self.status = status
        self.listing = listing if listing is not None else [{"code": c} for c in codes]
        self.codes = codes
        self.requests: list[str] = []

    def candle_requests(self) -> list[str]:
        return [r for r in self.requests if "/candles/" in r]

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        self.requests.append(path)
        if path == "/v1/instruments":
            if self.listing == "down":
                return httpx2.Response(503)
            return httpx2.Response(200, json=self.listing)
        if self.fail_first:
            self.fail_first -= 1
            return httpx2.Response(503)
        if self.status != 200:
            return httpx2.Response(self.status)
        parts = path.split("/")  # /v1/candles/minute/{code}/BID/{y}/{m}/{d}
        code, current = parts[4], date(int(parts[6]), int(parts[7]), int(parts[8]))
        if current in self.failing:
            return httpx2.Response(503)
        if code not in self.codes or current in self.missing:
            return httpx2.Response(404)
        price = 2000.0 if code.startswith("XAU") else 20000.0
        if current.weekday() == 6:  # Sunday evening: a few minutes only
            rows = [(1380, price, price + 1, price - 1, price + 0.5, 1.0)]
        else:
            rows = [(i, price, price + 1, price - 1, price + 0.5, 1.0) for i in range(3)]
        return httpx2.Response(200, json=candles_json(current, rows))


def market(tmp_path: Path, feed: Feed, **kwargs: Any) -> MarketData:
    options: dict[str, Any] = {"retry_delays": (), "requests_per_second": 0, **kwargs}
    return MarketData(tmp_path, transport=httpx2.MockTransport(feed), **options)


async def test_sync_downloads_each_day_once_and_skips_saturdays(tmp_path: Path) -> None:
    feed = Feed()
    data = market(tmp_path, feed)
    progress: list[tuple[int, int]] = []

    async def report(done: int, total: int) -> None:
        progress.append((done, total))

    # Thu 2024-02-29 … Mon 2024-03-04 (a month boundary and a weekend)
    fetched = await data.sync(NQ, date(2024, 2, 29), date(2024, 3, 4), report)
    assert fetched == 4  # Thu, Fri, Sun, Mon
    assert feed.requests[0] == "/v1/instruments"
    assert "/v1/candles/minute/USATECH.IDX-USD/BID/2024/2/29" in feed.requests  # months 1-based
    assert not any(r.endswith("/2024/3/2") for r in feed.requests)  # Saturday
    assert progress[-1] == (4, 4)

    again = await data.sync(NQ, date(2024, 2, 29), date(2024, 3, 4))
    assert again == 0 and len(feed.candle_requests()) == 4

    bars = data.load(NQ, date(2024, 2, 29), date(2024, 3, 4))
    assert len(bars) == 3 + 3 + 1 + 3 and np.all(np.diff(bars.t) > 0)
    assert bars.close[0] == 20000.5
    assert data.coverage(NQ) == (date(2024, 2, 29), date(2024, 3, 4), 10)


async def test_the_code_is_looked_up_and_the_config_is_the_fallback(tmp_path: Path) -> None:
    renamed = Feed(codes=("USATECH.IDX-USD-NEW",), listing=[{"code": "USATECH.IDX-USD-NEW"}])
    # "USATECHIDXUSDNEW" doesn't match the symbol, so the configured code is used — and
    # yields nothing: that is reported, not cached as "no data".
    with pytest.raises(DataError, match=r"no minute data for NQ \(code USATECH.IDX-USD\)"):
        await market(tmp_path, renamed).sync(NQ, date(2024, 3, 4), date(2024, 3, 22))

    listed = Feed(codes=("NDX-USD",), listing=[{"code": "NDX-USD", "name": "USATECH.IDX/USD"}])
    assert await market(tmp_path / "b", listed).sync(NQ, date(2024, 3, 4), date(2024, 3, 4)) == 1
    assert "/v1/candles/minute/NDX-USD/BID/2024/3/4" in listed.requests

    no_list = Feed(listing="down")
    assert await market(tmp_path / "c", no_list).sync(NQ, date(2024, 3, 4), date(2024, 3, 4)) == 1


async def test_missing_days(tmp_path: Path) -> None:
    holiday = date(2024, 3, 29)
    feed = Feed(missing={holiday})
    data = market(tmp_path, feed)
    await data.sync(NQ, date(2024, 3, 25), date(2024, 3, 29))
    await data.sync(NQ, date(2024, 3, 25), date(2024, 3, 29))
    assert len(feed.candle_requests()) == 5  # an old 404 is a day without data

    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    if yesterday.weekday() != 5:
        recent = Feed(missing={yesterday})
        data = market(tmp_path / "recent", recent)
        await data.sync(NQ, yesterday - timedelta(days=13), yesterday)
        asked = recent.candle_requests().count
        path = f"/v1/candles/minute/USATECH.IDX-USD/BID/{yesterday.year}/{yesterday.month}/"
        await data.sync(NQ, yesterday - timedelta(days=13), yesterday)
        assert asked(f"{path}{yesterday.day}") == 2  # not published yet: asked again


async def test_server_errors(tmp_path: Path) -> None:
    feed = Feed(fail_first=2)
    data = market(tmp_path, feed, retry_delays=(0, 0))
    assert await data.sync(NQ, date(2024, 3, 4), date(2024, 3, 4)) == 1
    assert len(feed.candle_requests()) == 3

    down = Feed(fail_first=10_000)
    with pytest.raises(DataError, match=r"can't reach Dukascopy's data right now \(HTTP 503\)"):
        await market(tmp_path / "b", down).sync(NQ, date(2024, 1, 1), date(2024, 6, 30))
    assert len(down.candle_requests()) <= 27  # stopped in January instead of trying six months

    with pytest.raises(DataError, match="refused"):
        await market(tmp_path / "c", Feed(status=403)).sync(NQ, date(2024, 3, 4), date(2024, 3, 4))


async def test_a_few_failed_days_are_fetched_next_time(tmp_path: Path) -> None:
    flaky = date(2024, 3, 13)
    feed = Feed(failing={flaky})
    data = market(tmp_path, feed)
    await data.sync(NQ, date(2024, 3, 1), date(2024, 3, 31))
    feed.failing.clear()
    assert await data.sync(NQ, date(2024, 3, 1), date(2024, 3, 31)) == 1
    assert feed.candle_requests()[-1].endswith("/2024/3/13")


def test_resample_builds_aligned_ohlcv() -> None:
    t = np.arange(10, dtype=np.int64) * 60 + 300 * 1000  # 10 minutes from a 5-minute boundary
    o = np.arange(10, dtype=np.float64)
    bars = Bars(t, o, o + 1, o - 1, o + 0.5, np.ones(10))
    five = resample(bars, 5)
    assert five.minutes == 5
    assert five.t.tolist() == [t[0], t[5]]
    assert five.open.tolist() == [0.0, 5.0]
    assert five.high.tolist() == [5.0, 10.0]
    assert five.low.tolist() == [-1.0, 4.0]
    assert five.close.tolist() == [4.5, 9.5]
    assert five.volume.tolist() == [5.0, 5.0]


def test_local_clock_follows_daylight_saving() -> None:
    def ts(text: str) -> int:
        return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())

    t = np.array(
        [
            ts("2024-03-08 14:30"),  # 09:30 EST (UTC-5)
            ts("2024-03-10 06:59"),  # 01:59 EST, the night of the switch
            ts("2024-03-10 07:00"),  # 03:00 EDT
            ts("2024-03-11 13:30"),  # 09:30 EDT (UTC-4)
        ],
        dtype=np.int64,
    )
    clock = local_clock(t, "America/New_York")
    assert clock.minute.tolist() == [570, 119, 180, 570]
    assert clock.weekday.tolist() == [4, 6, 6, 0]
    assert clock.day[3] - clock.day[0] == 3
