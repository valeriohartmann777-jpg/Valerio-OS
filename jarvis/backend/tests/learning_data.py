"""Synthetic market data for the learning tests."""

from __future__ import annotations

import lzma
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from jarvis.learning.market import Bars

NY = ZoneInfo("America/New_York")


def bi5(rows: list[tuple[int, int, int, int, int, float]]) -> bytes:
    """Dukascopy candle file: (seconds, open, close, low, high, volume)."""
    dtype = np.dtype(
        [("t", ">u4"), ("o", ">u4"), ("c", ">u4"), ("l", ">u4"), ("h", ">u4"), ("v", ">f4")]
    )
    return lzma.compress(np.array(rows, dtype=dtype).tobytes(), format=lzma.FORMAT_ALONE)


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
