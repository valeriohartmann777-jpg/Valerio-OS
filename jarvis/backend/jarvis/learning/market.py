"""Historical market data for learning: Dukascopy minute candles (free, no key).

Source: ``https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YYYY}/{MM-1}/{DD}/BID_candles_min_1.bi5``
— one LZMA file per UTC day of 24-byte big-endian records: seconds since the
day began (uint32), open, close, low, high (uint32, in points) and volume
(float32). Weekend and holiday minutes come as flat candles without volume and
are dropped.

The point divisor is not hard-coded: the one that puts the day's prices into
the instrument's plausible range wins (ranges span less than a factor of ten,
so only one can). Every file is validated — time order, OHLC consistency — so
a format change shows up as a clear error, never as wrong backtests.

Cache: ``data/market/{SYMBOL}/{YYYY-MM}.npz`` with the candles and the days
already fetched (including empty ones), so each day is downloaded once.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import lzma
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import numpy as np

log = logging.getLogger("jarvis.learning")

FEED = "https://datafeed.dukascopy.com/datafeed"
_RECORD = np.dtype(
    [("t", ">u4"), ("o", ">u4"), ("c", ">u4"), ("l", ">u4"), ("h", ">u4"), ("v", ">f4")]
)
_DIVISORS = (1, 10, 100, 1000, 10000, 100000)
_HEADERS = {"User-Agent": "Mozilla/5.0 (JARVIS research; historical candles)"}
# A missing file this close to today may just not be published yet.
_RECENT_DAYS = 5


class DataError(Exception):
    """Market data could not be fetched or does not look right."""


@dataclass(frozen=True)
class Bars:
    """OHLCV bars; ``t`` is each bar's opening time in UTC epoch seconds."""

    t: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    minutes: int = 1

    def __len__(self) -> int:
        return len(self.t)

    def between(self, start: int, end: int) -> Bars:
        """Bars opening in [start, end) (epoch seconds)."""
        lo, hi = np.searchsorted(self.t, [start, end])
        return Bars(
            self.t[lo:hi],
            self.open[lo:hi],
            self.high[lo:hi],
            self.low[lo:hi],
            self.close[lo:hi],
            self.volume[lo:hi],
            self.minutes,
        )


def empty_bars(minutes: int = 1) -> Bars:
    f = np.empty(0, dtype=np.float64)
    return Bars(np.empty(0, dtype=np.int64), f, f, f, f, f, minutes)


def resample(bars: Bars, minutes: int) -> Bars:
    """Aggregate to ``minutes``-bars aligned to the UTC clock (also local clocks
    with whole-hour offsets, which NY and London have)."""
    if minutes == bars.minutes or len(bars) == 0:
        return bars if minutes == bars.minutes else empty_bars(minutes)
    if minutes % bars.minutes:
        raise ValueError(f"can't build {minutes}-minute bars from {bars.minutes}-minute bars")
    size = minutes * 60
    key = bars.t // size
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(bars)] - 1
    return Bars(
        key[starts] * size,
        bars.open[starts],
        np.maximum.reduceat(bars.high, starts),
        np.minimum.reduceat(bars.low, starts),
        bars.close[ends],
        np.add.reduceat(bars.volume, starts),
        minutes,
    )


@dataclass(frozen=True)
class Clock:
    """Local calendar of each bar in one time zone."""

    day: np.ndarray  # local days since 1970-01-01
    minute: np.ndarray  # local minute of day at the bar's open (0-1439)
    weekday: np.ndarray  # 0 = Monday


def local_clock(t: np.ndarray, tz: str) -> Clock:
    zone = ZoneInfo(tz)

    def offset(ts: int) -> int:
        delta = datetime.fromtimestamp(ts, tz=zone).utcoffset()
        return int(delta.total_seconds()) if delta else 0

    days = t // 86400
    unique, inverse = np.unique(days, return_inverse=True)
    offsets = np.empty(len(t), dtype=np.int64)
    per_day = np.empty(len(unique), dtype=np.int64)
    switching: list[int] = []
    for k, d in enumerate(unique.tolist()):
        first, last = offset(d * 86400), offset(d * 86400 + 86399)
        per_day[k] = first
        if first != last:  # daylight saving changes during this UTC day
            switching.append(k)
    offsets[:] = per_day[inverse]
    for k in switching:
        for i in np.flatnonzero(inverse == k).tolist():
            offsets[i] = offset(int(t[i]))
    local = t + offsets
    day = local // 86400
    return Clock(day=day, minute=(local % 86400) // 60, weekday=(day + 3) % 7)


# Decoding -----------------------------------------------------------------------


@dataclass(frozen=True)
class DayCandles:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    low: np.ndarray
    c: np.ndarray
    v: np.ndarray


def _empty_day() -> DayCandles:
    f = np.empty(0, dtype=np.float64)
    return DayCandles(np.empty(0, dtype=np.int64), f, f, f, f, f)


def decode_day(
    raw: bytes, day: date, price_range: tuple[float, float], symbol: str = ""
) -> DayCandles:
    """One ``BID_candles_min_1.bi5`` file → validated candles with traded volume."""
    if not raw:
        return _empty_day()
    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError as exc:
        raise DataError(f"Dukascopy sent a damaged file for {symbol} {day}.") from exc
    if len(data) % _RECORD.itemsize:
        raise DataError(f"Dukascopy's file for {symbol} {day} isn't in the expected candle layout.")
    rec = np.frombuffer(data, dtype=_RECORD)
    rec = rec[rec["v"] > 0]
    if len(rec) == 0:
        return _empty_day()
    seconds = rec["t"].astype(np.int64)
    o, c = rec["o"].astype(np.int64), rec["c"].astype(np.int64)
    lo, hi = rec["l"].astype(np.int64), rec["h"].astype(np.int64)
    ordered = bool(np.all(np.diff(seconds) > 0)) and bool(np.all(seconds % 60 == 0))
    consistent = (lo <= np.minimum(o, c)) & (hi >= np.maximum(o, c))
    if not ordered or seconds[-1] >= 86400 or consistent.mean() < 0.995:
        raise DataError(
            f"Dukascopy's data for {symbol} on {day} doesn't look like minute candles "
            "(the file format may have changed)."
        )
    divisor = _divisor(float(np.median(c)), price_range, symbol, day)
    keep = consistent
    start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    return DayCandles(
        t=start + seconds[keep],
        o=o[keep] / divisor,
        h=hi[keep] / divisor,
        low=lo[keep] / divisor,
        c=c[keep] / divisor,
        v=rec["v"][keep].astype(np.float64),
    )


def _divisor(median: float, price_range: tuple[float, float], symbol: str, day: date) -> int:
    low, high = price_range
    fits = [d for d in _DIVISORS if low <= median / d <= high]
    if len(fits) != 1:
        raise DataError(
            f"Prices for {symbol} on {day} are outside the expected range "
            f"{low:g} to {high:g} (check price_range in config/learning.yaml)."
        )
    return fits[0]


# Download + cache -----------------------------------------------------------------


@dataclass(frozen=True)
class Instrument:
    name: str  # NQ, XAUUSD
    symbol: str  # Dukascopy symbol
    price_range: tuple[float, float]


Progress = Callable[[int, int], Awaitable[None]]  # (done, total)


class MarketData:
    def __init__(
        self,
        root: Path,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        concurrency: int = 6,
        retry_delays: tuple[float, ...] = (1.0, 3.0, 9.0),
    ) -> None:
        self._root = root
        self._transport = transport
        self._concurrency = concurrency
        self._retry_delays = retry_delays

    # Sync ------------------------------------------------------------------------

    async def sync(
        self, instrument: Instrument, start: date, end: date, progress: Progress | None = None
    ) -> int:
        """Download the days in [start, end] that aren't cached yet. Returns how
        many days were fetched. Raises ``DataError``."""
        months = _months(start, end)
        todo: dict[tuple[int, int], list[date]] = {}
        cached: dict[tuple[int, int], dict[str, np.ndarray]] = {}
        for month in months:
            stored = self._read_month(instrument.symbol, month)
            cached[month] = stored
            done = set(stored["days"].tolist())
            days = [
                d
                for d in _days_of(month, start, end)
                if d.weekday() != 5 and _day_number(d) not in done  # no trading on Saturdays
            ]
            if days:
                todo[month] = days
        total = sum(len(days) for days in todo.values())
        if total == 0:
            return 0
        fetched = 0
        semaphore = asyncio.Semaphore(self._concurrency)
        async with httpx2.AsyncClient(
            headers=_HEADERS, timeout=30.0, transport=self._transport, follow_redirects=True
        ) as client:

            async def one(day: date) -> tuple[date, DayCandles | None]:
                async with semaphore:
                    return day, await self._fetch(client, instrument, day)

            for month, days in todo.items():
                results = await asyncio.gather(*(one(d) for d in days))
                stored = cached[month]
                parts = [_day_from_store(stored)]
                new_days = []
                for day, candles in results:
                    if candles is None:
                        continue  # not published yet; try again next time
                    parts.append(candles)
                    new_days.append(_day_number(day))
                fetched += len(days)
                if new_days:
                    self._write_month(instrument.symbol, month, parts, stored["days"], new_days)
                if progress:
                    await progress(fetched, total)
        return fetched

    async def _fetch(
        self, client: httpx2.AsyncClient, instrument: Instrument, day: date
    ) -> DayCandles | None:
        url = (
            f"{FEED}/{instrument.symbol}/{day.year:04d}/{day.month - 1:02d}/{day.day:02d}/"
            "BID_candles_min_1.bi5"
        )
        recent = (datetime.now(UTC).date() - day).days <= _RECENT_DAYS
        last: str = ""
        for attempt in range(len(self._retry_delays) + 1):
            try:
                response = await client.get(url)
            except httpx2.HTTPError as exc:
                last = type(exc).__name__
            else:
                if response.status_code == 200:
                    return decode_day(
                        response.content, day, instrument.price_range, instrument.symbol
                    )
                if response.status_code == 404:
                    return None if recent else _empty_day()
                if response.status_code not in (429, 500, 502, 503, 504):
                    raise DataError(f"Dukascopy refused the data request ({response.status_code}).")
                last = f"HTTP {response.status_code}"
            if attempt < len(self._retry_delays):
                await asyncio.sleep(self._retry_delays[attempt])
        raise DataError(f"I can't reach Dukascopy's data feed right now ({last}).")

    # Load ------------------------------------------------------------------------

    def load(self, instrument: Instrument, start: date, end: date) -> Bars:
        """Cached 1-minute bars for [start, end] (UTC days)."""
        parts = [
            _day_from_store(self._read_month(instrument.symbol, m)) for m in _months(start, end)
        ]
        t = np.concatenate([p.t for p in parts])
        order = np.argsort(t, kind="stable")
        bars = Bars(
            t[order],
            np.concatenate([p.o for p in parts])[order],
            np.concatenate([p.h for p in parts])[order],
            np.concatenate([p.low for p in parts])[order],
            np.concatenate([p.c for p in parts])[order],
            np.concatenate([p.v for p in parts])[order],
        )
        lo = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
        hi = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()) + 86400
        return bars.between(lo, hi)

    def coverage(self, instrument: Instrument) -> tuple[date, date, int] | None:
        """First day, last day and number of cached minute bars."""
        folder = self._root / instrument.symbol
        files = sorted(folder.glob("*.npz")) if folder.exists() else []
        first = last = None
        count = 0
        for path in files:
            with np.load(path) as data:
                t = data["t"]
                if len(t) == 0:
                    continue
                count += len(t)
                lo, hi = int(t[0]), int(t[-1])
            first = lo if first is None else min(first, lo)
            last = hi if last is None else max(last, hi)
        if first is None or last is None:
            return None
        return (
            datetime.fromtimestamp(first, UTC).date(),
            datetime.fromtimestamp(last, UTC).date(),
            count,
        )

    # Cache files -----------------------------------------------------------------

    def _path(self, symbol: str, month: tuple[int, int]) -> Path:
        return self._root / symbol / f"{month[0]:04d}-{month[1]:02d}.npz"

    def _read_month(self, symbol: str, month: tuple[int, int]) -> dict[str, np.ndarray]:
        path = self._path(symbol, month)
        if path.exists():
            try:
                with np.load(path) as data:
                    return {k: data[k] for k in ("t", "o", "h", "l", "c", "v", "days")}
            except (OSError, ValueError, KeyError):
                log.warning("discarding a damaged market data file: %s", path.name)
        f = np.empty(0, dtype=np.float64)
        return {
            "t": np.empty(0, dtype=np.int64),
            "o": f,
            "h": f,
            "l": f,
            "c": f,
            "v": f,
            "days": np.empty(0, dtype=np.int32),
        }

    def _write_month(
        self,
        symbol: str,
        month: tuple[int, int],
        parts: list[DayCandles],
        done: np.ndarray,
        new_days: list[int],
    ) -> None:
        t = np.concatenate([p.t for p in parts])
        order = np.argsort(t, kind="stable")
        path = self._path(symbol, month)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.tmp.npz")
        np.savez_compressed(
            tmp,
            t=t[order],
            o=np.concatenate([p.o for p in parts])[order],
            h=np.concatenate([p.h for p in parts])[order],
            l=np.concatenate([p.low for p in parts])[order],
            c=np.concatenate([p.c for p in parts])[order],
            v=np.concatenate([p.v for p in parts])[order],
            days=np.union1d(done, np.array(new_days, dtype=np.int32)).astype(np.int32),
        )
        tmp.replace(path)


def _day_from_store(stored: dict[str, np.ndarray]) -> DayCandles:
    return DayCandles(
        stored["t"].astype(np.int64),
        stored["o"].astype(np.float64),
        stored["h"].astype(np.float64),
        stored["l"].astype(np.float64),
        stored["c"].astype(np.float64),
        stored["v"].astype(np.float64),
    )


def _day_number(day: date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


def _months(start: date, end: date) -> list[tuple[int, int]]:
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _days_of(month: tuple[int, int], start: date, end: date) -> list[date]:
    first = max(date(month[0], month[1], 1), start)
    last = min(date(month[0], month[1], calendar.monthrange(*month)[1]), end)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]
