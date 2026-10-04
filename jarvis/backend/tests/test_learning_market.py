"""Dukascopy candle files, the download cache, resampling and local time."""

from __future__ import annotations

import lzma
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx2
import numpy as np
import pytest

from jarvis.learning.market import (
    Bars,
    DataError,
    Instrument,
    MarketData,
    decode_day,
    local_clock,
    resample,
)
from tests.learning_data import bi5

NQ = Instrument("NQ", "USATECHIDXUSD", (4000.0, 39000.0))
GOLD = Instrument("XAUUSD", "XAUUSD", (900.0, 8900.0))


def test_decode_scales_prices_and_drops_flat_minutes() -> None:
    day = date(2024, 3, 5)
    raw = bi5(
        [
            (0, 20000_000, 20000_000, 20000_000, 20000_000, 0.0),  # no trading: dropped
            (60, 20000_250, 20001_000, 19999_500, 20001_500, 2.5),
            (120, 20001_000, 20000_750, 20000_250, 20001_250, 1.0),
        ]
    )
    candles = decode_day(raw, day, NQ.price_range, NQ.symbol)
    start = int(datetime(2024, 3, 5, tzinfo=UTC).timestamp())
    assert candles.t.tolist() == [start + 60, start + 120]
    assert candles.o.tolist() == [20000.25, 20001.0]
    assert candles.c.tolist() == [20001.0, 20000.75]
    assert candles.low.tolist() == [19999.5, 20000.25]
    assert candles.h.tolist() == [20001.5, 20001.25]
    assert candles.v.tolist() == [2.5, 1.0]


def test_decode_picks_the_divisor_by_plausible_price() -> None:
    raw = bi5([(0, 2650_123, 2650_500, 2649_900, 2650_800, 1.0)])
    candles = decode_day(raw, date(2024, 3, 5), GOLD.price_range, GOLD.symbol)
    assert candles.c.tolist() == [2650.5]


def test_empty_file_means_no_trading() -> None:
    assert len(decode_day(b"", date(2024, 3, 9), NQ.price_range).t) == 0


def test_a_different_layout_is_refused_not_misread() -> None:
    # Written as open, high, low, close: high lands in the close column etc.
    rows = [(60 * i, 20000_000, 20004_000 + i, 19996_000, 19999_000, 1.0) for i in range(50)]
    with pytest.raises(DataError, match="format may have changed"):
        decode_day(bi5(rows), date(2024, 3, 5), NQ.price_range, NQ.symbol)


def test_prices_outside_the_range_are_refused() -> None:
    # 8950 or 895 at any power of ten: neither is a plausible gold price.
    raw = bi5([(0, 8950_000, 8950_000, 8950_000, 8950_000, 1.0)])
    with pytest.raises(DataError, match="expected range"):
        decode_day(raw, date(2024, 3, 5), GOLD.price_range, GOLD.symbol)


def test_damaged_file_is_refused() -> None:
    with pytest.raises(DataError, match="damaged"):
        decode_day(b"not lzma at all", date(2024, 3, 5), NQ.price_range)
    with pytest.raises(DataError, match="layout"):
        decode_day(lzma.compress(b"x" * 25, format=lzma.FORMAT_ALONE), date(2024, 3, 5), (1, 9))


def _feed(
    requests: list[str], *, missing: set[date] | None = None, fail: int = 0, status: int = 200
) -> httpx2.MockTransport:
    failures = {"left": fail}

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        parts = request.url.path.split("/")
        year, month, day = int(parts[-4]), int(parts[-3]) + 1, int(parts[-2])
        current = date(year, month, day)
        if failures["left"]:
            failures["left"] -= 1
            return httpx2.Response(503)
        if status != 200:
            return httpx2.Response(status)
        if missing and current in missing:
            return httpx2.Response(404)
        if current.weekday() == 6:  # Sunday evening: a few minutes only
            rows = [(82800, 20000_000, 20000_500, 19999_500, 20001_000, 1.0)]
        else:
            rows = [(60 * i, 20000_000, 20000_500, 19999_500, 20001_000, 1.0) for i in range(3)]
        return httpx2.Response(200, content=bi5(rows))

    return httpx2.MockTransport(handler)


async def test_sync_downloads_each_day_once_and_skips_saturdays(tmp_path: Path) -> None:
    requests: list[str] = []
    market = MarketData(tmp_path, transport=_feed(requests), retry_delays=())
    progress: list[tuple[int, int]] = []

    async def report(done: int, total: int) -> None:
        progress.append((done, total))

    # Thu 2024-02-29 … Mon 2024-03-04 (a month boundary and a weekend)
    fetched = await market.sync(NQ, date(2024, 2, 29), date(2024, 3, 4), report)
    assert fetched == 4  # Thu, Fri, Sun, Mon
    assert "/datafeed/USATECHIDXUSD/2024/01/29/BID_candles_min_1.bi5" in requests  # month - 1
    assert not any("/02/02/" in r for r in requests)  # Saturday 2024-03-02
    assert progress[-1] == (4, 4)

    again = await market.sync(NQ, date(2024, 2, 29), date(2024, 3, 4))
    assert again == 0 and len(requests) == 4

    bars = market.load(NQ, date(2024, 2, 29), date(2024, 3, 4))
    assert len(bars) == 3 + 3 + 1 + 3
    assert np.all(np.diff(bars.t) > 0)
    assert market.coverage(NQ) == (date(2024, 2, 29), date(2024, 3, 4), 10)


async def test_old_missing_days_are_recorded_recent_ones_retried(tmp_path: Path) -> None:
    requests: list[str] = []
    today = datetime.now(UTC).date()
    recent, old = today - timedelta(days=1), date(2024, 3, 4)
    market = MarketData(tmp_path, transport=_feed(requests, missing={recent, old}), retry_delays=())
    await market.sync(NQ, old, old)
    await market.sync(NQ, old, old)
    assert len(requests) == 1  # an old 404 is a day without data

    if recent.weekday() != 5:
        await market.sync(NQ, recent, recent)
        await market.sync(NQ, recent, recent)
        assert len(requests) == 3  # not published yet: asked again


async def test_server_errors_are_retried_then_reported(tmp_path: Path) -> None:
    requests: list[str] = []
    market = MarketData(tmp_path, transport=_feed(requests, fail=2), retry_delays=(0, 0))
    assert await market.sync(NQ, date(2024, 3, 4), date(2024, 3, 4)) == 1
    assert len(requests) == 3

    market = MarketData(tmp_path / "b", transport=_feed([], fail=9), retry_delays=(0, 0))
    with pytest.raises(DataError, match="can't reach"):
        await market.sync(NQ, date(2024, 3, 4), date(2024, 3, 4))

    market = MarketData(tmp_path / "c", transport=_feed([], status=403), retry_delays=())
    with pytest.raises(DataError, match="refused"):
        await market.sync(NQ, date(2024, 3, 4), date(2024, 3, 4))


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
