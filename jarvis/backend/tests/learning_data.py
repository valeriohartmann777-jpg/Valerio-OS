"""Synthetic market data for the learning tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import numpy as np

from jarvis.learning.market import Bars

NY = ZoneInfo("America/New_York")


def candles_json(
    day: date, rows: list[tuple[int, float, float, float, float, float]], multiplier: float = 0.25
) -> dict[str, object]:
    """A day as Dukascopy's data API sends it: rows of (minute of day, open,
    high, low, close, volume), delta-encoded against the first candle."""
    start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    if not rows:
        return {
            "timestamp": start,
            "multiplier": multiplier,
            "shift": 60000,
            "times": [],
            "opens": [],
            "highs": [],
            "lows": [],
            "closes": [],
            "volumes": [],
            "open": None,
            "high": None,
            "low": None,
            "close": None,
        }
    minutes = [r[0] for r in rows]
    units = [[round(r[k] / multiplier) for r in rows] for k in (1, 2, 3, 4)]

    def deltas(values: list[int]) -> list[int]:
        return [0] + [b - a for a, b in pairwise(values)]

    return {
        "timestamp": start,
        "multiplier": multiplier,
        "shift": 60000,
        "open": rows[0][1],
        "high": rows[0][2],
        "low": rows[0][3],
        "close": rows[0][4],
        "times": [minutes[0]] + [b - a for a, b in pairwise(minutes)],
        "opens": deltas(units[0]),
        "highs": deltas(units[1]),
        "lows": deltas(units[2]),
        "closes": deltas(units[3]),
        "volumes": [r[5] for r in rows],
    }


def session_bars(
    days: list[date],
    price: float = 20000.0,
    *,
    path: object = None,
    start: str = "09:30",
    end: str = "16:00",
    seed: int = 7,
    noise: float = 1.0,
) -> Bars:
    """1-minute bars for New York sessions on ``days``.

    ``path(day_index, minute_index) -> drift`` adds a deterministic move per bar.
    """
    rng = np.random.default_rng(seed)
    t, o, h, low, c, v = [], [], [], [], [], []
    level = price
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    for d_index, day in enumerate(days):
        opening = datetime(day.year, day.month, day.day, sh, sm, tzinfo=NY)
        count = (eh * 60 + em) - (sh * 60 + sm)
        for m in range(count):
            ts = int((opening + timedelta(minutes=m)).astimezone(UTC).timestamp())
            drift = path(d_index, m) if callable(path) else 0.0
            step = float(rng.normal(0, noise)) + drift
            first = level
            last = level + step
            wick = abs(float(rng.normal(0, noise * 0.3)))
            t.append(ts)
            o.append(first)
            c.append(last)
            h.append(max(first, last) + wick)
            low.append(min(first, last) - wick)
            v.append(float(rng.integers(50, 150)))
            level = last
    return Bars(
        np.array(t, dtype=np.int64),
        np.array(o),
        np.array(h),
        np.array(low),
        np.array(c),
        np.array(v),
    )


def weekdays(start: date, end: date) -> list[date]:
    out = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def futures_minutes(start: date, end: date) -> np.ndarray:
    """UTC epoch seconds of every minute a futures market trades between the
    New York days ``start`` and ``end``: Sunday 18:00 to Friday 17:00 with a
    break from 17:00 to 18:00."""
    stamps: list[np.ndarray] = []
    day = start
    while day <= end:
        weekday = day.weekday()  # 0 = Monday
        parts = []
        if weekday < 5:  # 00:00-17:00
            parts.append((0, 17 * 60))
        if weekday in (6, 0, 1, 2, 3):  # Sunday-Thursday: 18:00-24:00
            parts.append((18 * 60, 24 * 60))
        for first, last in parts:
            hour = 12 if first == 0 else 20
            moment = datetime(day.year, day.month, day.day, hour, tzinfo=NY)
            offset = moment.utcoffset()
            midnight = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
            shift = -int(offset.total_seconds()) if offset else 0
            stamps.append(midnight + shift + 60 * np.arange(first, last, dtype=np.int64))
        day += timedelta(days=1)
    return np.concatenate(stamps) if stamps else np.empty(0, dtype=np.int64)


def futures_bars(
    start: date, end: date, price: float = 20000.0, *, noise: float = 1.0, seed: int = 7
) -> Bars:
    """A random walk of 1-minute bars over futures trading hours."""
    rng = np.random.default_rng(seed)
    t = futures_minutes(start, end)
    close = price + np.cumsum(rng.normal(0.0, noise, len(t)))
    opens = np.r_[price, close[:-1]]
    wick_up = np.abs(rng.normal(0.0, noise * 0.3, len(t)))
    wick_down = np.abs(rng.normal(0.0, noise * 0.3, len(t)))
    return Bars(
        t,
        opens,
        np.maximum(opens, close) + wick_up,
        np.minimum(opens, close) - wick_down,
        close,
        rng.integers(50, 150, len(t)).astype(np.float64),
    )
