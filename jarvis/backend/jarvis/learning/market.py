"""Historical market data for learning: Dukascopy minute candles (free, no key).

Source: Dukascopy's data API, ``https://jetta.dukascopy.com/v1`` (the old
``datafeed.dukascopy.com`` .bi5 files stopped answering in July 2026):

    GET /instruments                                  → codes, e.g. "XAU-USD"
    GET /candles/minute/{CODE}/BID/{YYYY}/{M}/{D}     → one UTC day, JSON

A day comes delta-encoded: ``timestamp`` (ms) and a base candle, then per
minute ``times`` (steps of ``shift`` ms) and ``opens``/``highs``/``lows``/
``closes`` (steps of ``multiplier`` in price) plus ``volumes``. Minutes
without trading are left out; flat candles without volume are dropped.

Every day is validated — time order, OHLC consistency, plausible prices — so
a format change shows up as a clear error, never as wrong backtests. Requests
are throttled (a few per second); a run of failures stops the download early
and a few failed days are simply fetched again next time.

Cache: ``data/market/{SYMBOL}/{YYYY-MM}.npz`` with the candles and the days
already fetched (including empty ones), so each day is downloaded once.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import numpy as np

log = logging.getLogger("jarvis.learning")

API = "https://jetta.dukascopy.com/v1"
_HEADERS = {"User-Agent": "JARVIS/0.1 (personal trading research)", "Accept": "application/json"}
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
# A missing day this close to today may just not be published yet.
_RECENT_DAYS = 5
# This many failed days in a row: the source is down — stop instead of trying them all.
_FAILURE_RUN = 8


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


_COLUMNS = ("times", "opens", "highs", "lows", "closes", "volumes")
_BASE = ("open", "high", "low", "close")


def decode_candles(
    payload: Any, day: date, price_range: tuple[float, float], name: str = ""
) -> DayCandles:
    """One day of minute candles from the data API → validated candles with volume."""
    broken = DataError(
        f"Dukascopy's answer for {name} on {day} isn't minute candles (the API may have changed)."
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("times"), list):
        raise broken
    if not payload["times"]:
        return _empty_day()
    try:
        cols = [np.asarray(payload[k], dtype=np.float64) for k in _COLUMNS]
        base = [float(payload[k]) for k in _BASE]
        multiplier, shift = float(payload["multiplier"]), float(payload["shift"])
        start_ms = float(payload["timestamp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise broken from exc
    times, opens, highs, lows, closes, volumes = cols
    if (
        any(len(col) != len(times) for col in cols)
        or not all(np.isfinite(col).all() for col in cols)
        or not np.isfinite(base).all()
        or multiplier <= 0
        or shift <= 0
        or (times < 0).any()
    ):
        raise broken
    t_ms = start_ms + np.cumsum(times) * shift
    o, h, lo, c = (
        round(b / multiplier) + np.cumsum(np.rint(d)).astype(np.int64)
        for b, d in zip(base, (opens, highs, lows, closes), strict=True)
    )
    day_ms = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
    ordered = bool(np.all(np.diff(t_ms) > 0)) and bool(np.all(np.mod(t_ms, 60000) == 0))
    inside = bool(t_ms[0] >= day_ms) and bool(t_ms[-1] < day_ms + 86_400_000)
    consistent = (lo <= np.minimum(o, c)) & (h >= np.maximum(o, c))
    if not ordered or not inside or consistent.mean() < 0.995:
        raise DataError(
            f"Dukascopy's data for {name} on {day} doesn't look like minute candles "
            "(the API may have changed)."
        )
    keep = consistent & (volumes > 0)
    if not keep.any():
        return _empty_day()
    decimals = max(0, -int(Decimal(repr(multiplier)).as_tuple().exponent))

    def price(units: np.ndarray) -> np.ndarray:
        out: np.ndarray = np.round(units[keep] * multiplier, decimals)
        return out

    closes_out = price(c)
    low, high = price_range
    if not low <= float(np.median(closes_out)) <= high:
        raise DataError(
            f"Prices for {name} on {day} are outside the expected range {low:g} to {high:g} "
            "(check price_range in config/learning.yaml)."
        )
    return DayCandles(
        t=(t_ms[keep] // 1000).astype(np.int64),
        o=price(o),
        h=price(h),
        low=price(lo),
        c=closes_out,
        v=volumes[keep],
    )


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find_code(listing: Any, symbol: str) -> str | None:
    """The API code (e.g. "USATECH.IDX-USD") for a symbol like "USATECHIDXUSD"."""
    items: list[Any] = []
    if isinstance(listing, list):
        items = listing
    elif isinstance(listing, dict):
        lists = [v for v in listing.values() if isinstance(v, list)]
        items = lists[0] if lists else [v for v in listing.values() if isinstance(v, dict)]
    wanted = _plain(symbol)
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            continue
        names = [item["code"], str(item.get("name") or "")]
        if any(_plain(n) == wanted for n in names):
            return str(item["code"])
    return None


# Download + cache -----------------------------------------------------------------


@dataclass(frozen=True)
class Instrument:
    name: str  # NQ, XAUUSD
    symbol: str  # Dukascopy's id, also the cache folder: USATECHIDXUSD
    price_range: tuple[float, float]
    code: str = ""  # Dukascopy's API code, e.g. USATECH.IDX-USD (looked up when empty)


Progress = Callable[[int, int], Awaitable[None]]  # (done, total)


class _Failed:
    def __init__(self, reason: str) -> None:
        self.reason = reason


class _Absent:
    """The API has no file for this day (404)."""


_ABSENT = _Absent()


class MarketData:
    def __init__(
        self,
        root: Path,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        concurrency: int = 3,
        requests_per_second: float = 4.0,
        retry_delays: tuple[float, ...] = (2.0, 6.0, 15.0),
    ) -> None:
        self._root = root
        self._transport = transport
        self._concurrency = concurrency
        self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._retry_delays = retry_delays
        self._codes: dict[str, str] = {}
        self._next_slot = 0.0
        self._slot_lock = asyncio.Lock()

    # Sync ------------------------------------------------------------------------

    async def sync(
        self, instrument: Instrument, start: date, end: date, progress: Progress | None = None
    ) -> int:
        """Download the days in [start, end] that aren't cached yet. Returns how
        many days were fetched. Raises ``DataError`` when the source is down or
        has no data for the instrument."""
        months = _months(start, end)
        todo: dict[tuple[int, int], list[date]] = {}
        cached: dict[tuple[int, int], dict[str, np.ndarray]] = {}
        had_data = False
        for month in months:
            stored = self._read_month(instrument.symbol, month)
            cached[month] = stored
            had_data = had_data or len(stored["t"]) > 0
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
        fetched = failed = with_data = 0
        run = 0
        reason = ""
        semaphore = asyncio.Semaphore(self._concurrency)
        async with httpx2.AsyncClient(
            headers=_HEADERS, timeout=30.0, transport=self._transport, follow_redirects=True
        ) as client:
            code = await self._code(client, instrument)

            async def one(day: date) -> tuple[date, DayCandles | _Failed | _Absent | None]:
                nonlocal run, reason
                async with semaphore:
                    result = await self._fetch(client, instrument, code, day)
                if isinstance(result, _Failed):
                    run, reason = run + 1, result.reason
                    if run >= _FAILURE_RUN:
                        raise DataError(
                            f"I can't reach Dukascopy's data right now ({result.reason})."
                        )
                else:
                    run = 0
                return day, result

            absent: dict[tuple[int, int], list[int]] = {}
            for month, days in todo.items():
                try:
                    async with asyncio.TaskGroup() as group:  # a failure run cancels the rest
                        tasks = [group.create_task(one(d)) for d in days]
                except BaseExceptionGroup as errors:
                    data_errors = [e for e in errors.exceptions if isinstance(e, DataError)]
                    if data_errors:
                        raise data_errors[0] from None
                    raise
                stored = cached[month]
                parts = [_day_from_store(stored)]
                new_days = []
                for task in tasks:
                    day, result = task.result()
                    if isinstance(result, _Failed):
                        failed += 1
                        continue  # fetched again next time
                    if isinstance(result, _Absent):
                        absent.setdefault(month, []).append(_day_number(day))
                        continue
                    if result is None:
                        continue  # not published yet
                    parts.append(result)
                    new_days.append(_day_number(day))
                    with_data += len(result.t) > 0
                fetched += len(days)
                if new_days:
                    self._write_month(instrument.symbol, month, parts, stored["days"], new_days)
                if progress:
                    await progress(fetched, total)
                if not had_data and with_data == 0 and fetched >= 60:
                    break  # two months without a single candle: wrong code, don't go on
        if not had_data and with_data == 0 and total >= 10:
            # Never cache "no data" for a code that may be wrong.
            raise DataError(
                f"Dukascopy has no minute data for {instrument.name} (code {code}) from {start} on."
            )
        for month, numbers in absent.items():  # holidays: don't ask again
            stored = self._read_month(instrument.symbol, month)
            self._write_month(
                instrument.symbol, month, [_day_from_store(stored)], stored["days"], numbers
            )
        if failed > max(3, total // 10):
            raise DataError(
                f"I can't reach Dukascopy's data right now ({reason}; "
                f"{failed} of {total} days failed)."
            )
        return fetched

    async def _slot(self) -> None:
        """Wait for this request's turn (throttling)."""
        async with self._slot_lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def _code(self, client: httpx2.AsyncClient, instrument: Instrument) -> str:
        """The instrument's API code, from Dukascopy's list (config as fallback)."""
        if instrument.symbol in self._codes:
            return self._codes[instrument.symbol]
        found = None
        try:
            await self._slot()
            response = await client.get(f"{API}/instruments")
            if response.status_code == 200:
                found = find_code(response.json(), instrument.symbol)
        except (httpx2.HTTPError, ValueError) as exc:
            log.info("couldn't load Dukascopy's instrument list: %s", type(exc).__name__)
        code = found or instrument.code or instrument.symbol
        self._codes[instrument.symbol] = code
        return code

    async def _fetch(
        self, client: httpx2.AsyncClient, instrument: Instrument, code: str, day: date
    ) -> DayCandles | _Failed | _Absent | None:
        url = f"{API}/candles/minute/{code}/BID/{day.year}/{day.month}/{day.day}"
        recent = (datetime.now(UTC).date() - day).days <= _RECENT_DAYS
        last = ""
        for attempt in range(len(self._retry_delays) + 1):
            await self._slot()
            try:
                response = await client.get(url)
            except httpx2.HTTPError as exc:
                last = type(exc).__name__
            else:
                status = response.status_code
                if status == 200:
                    try:
                        payload = response.json()
                    except ValueError:
                        last = "an answer that isn't JSON"
                    else:
                        return decode_candles(payload, day, instrument.price_range, instrument.name)
                elif status == 404:
                    return None if recent else _ABSENT
                elif status in (401, 403):
                    raise DataError(f"Dukascopy refused the data request ({status}).")
                elif status not in _RETRYABLE:
                    raise DataError(f"Dukascopy rejected the data request ({status}).")
                else:
                    last = f"HTTP {status}"
            if attempt < len(self._retry_delays):
                await asyncio.sleep(self._retry_delays[attempt])
        return _Failed(last)

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
