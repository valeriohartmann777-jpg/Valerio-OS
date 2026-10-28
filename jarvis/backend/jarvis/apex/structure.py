"""Market structure from closed bars: confirmed swings, their labels, trend and breaks.

A swing high is a bar whose high is above the ``n`` bars before it and not below the ``n``
bars after it; it is **known only after those n bars have closed** (``confirmed``). Each
swing is labelled against the previous swing of its kind: HH / LH for highs, HL / LL for lows.

Trend: up when the latest high is HH and the latest low HL, down for LH + LL, else range.
A *break* is a bar closing beyond the latest confirmed swing that hasn't been broken yet:
in the trend's direction (or from a range) it is a **BOS** (break of structure), against an
up or down trend a **CHoCH** (change of character). Everything uses only bars up to the bar
in question — nothing here looks ahead.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from jarvis.learning.features import atr
from jarvis.learning.market import Bars


@dataclass(frozen=True)
class Swing:
    kind: str  # high | low
    price: float
    t: int  # open time of the swing bar
    confirmed: int  # close time of the bar that confirmed it
    label: str  # HH, LH, HL, LL ("" for the first of its kind)


def swings(bars: Bars, n: int) -> list[Swing]:
    """Confirmed swing highs and lows, in order of confirmation."""
    size = len(bars)
    if size < 2 * n + 1:
        return []
    step = 60 * bars.minutes
    found: list[tuple[int, str, float, int]] = []  # (confirm index, kind, price, swing index)
    for kind, values in (("high", bars.high), ("low", -bars.low)):
        window = sliding_window_view(values, 2 * n + 1)  # window k covers bars k .. k+2n
        centre = window[:, n]
        left = window[:, :n].max(axis=1)
        right = window[:, n + 1 :].max(axis=1)
        idx = np.flatnonzero((centre > left) & (centre >= right))
        for k in idx.tolist():
            j = k + n
            price = float(bars.high[j]) if kind == "high" else float(bars.low[j])
            found.append((j + n, kind, price, j))
    found.sort(key=lambda f: (f[0], f[3]))
    out: list[Swing] = []
    last: dict[str, float] = {}
    for confirm, kind, price, j in found:
        previous = last.get(kind)
        if previous is None:
            label = ""
        elif kind == "high":
            label = "HH" if price > previous else "LH"
        else:
            label = "HL" if price > previous else "LL"
        last[kind] = price
        out.append(Swing(kind, price, int(bars.t[j]), int(bars.t[confirm]) + step, label))
    return out


def _trend(high_label: str, low_label: str) -> str:
    if high_label == "HH" and low_label == "HL":
        return "up"
    if high_label == "LH" and low_label == "LL":
        return "down"
    return "range"


def structure(bars: Bars, n: int, keep: int = 8) -> dict[str, Any]:
    """Swings, trend and structure breaks of ``bars`` (all closed)."""
    found = swings(bars, n)
    step = 60 * bars.minutes
    closes = bars.close
    close_times = bars.t + step
    events: list[dict[str, Any]] = []
    trend = "range"
    labels = {"high": "", "low": ""}
    active: dict[str, Swing | None] = {"high": None, "low": None}
    cursor = 0  # next swing to apply
    for i in range(len(bars)):
        # A break is judged with what was known before this bar closed.
        for kind, sign in (("high", 1.0), ("low", -1.0)):
            swing = active[kind]
            if swing is not None and sign * (closes[i] - swing.price) > 0:
                against = (kind == "high" and trend == "down") or (kind == "low" and trend == "up")
                events.append(
                    {
                        "type": "CHoCH" if against else "BOS",
                        "direction": "up" if kind == "high" else "down",
                        "level": swing.price,
                        "t": int(close_times[i]),
                        "trend_before": trend,
                    }
                )
                active[kind] = None
        while cursor < len(found) and found[cursor].confirmed <= close_times[i]:
            swing = found[cursor]
            cursor += 1
            labels[swing.kind] = swing.label or labels[swing.kind]
            active[swing.kind] = swing
            trend = _trend(labels["high"], labels["low"])
    highs = [s for s in found if s.kind == "high"]
    lows = [s for s in found if s.kind == "low"]
    values = atr(bars.high, bars.low, bars.close, 14) if len(bars) else np.empty(0)
    latest_atr = float(values[-1]) if len(values) and np.isfinite(values[-1]) else None
    return {
        "minutes": bars.minutes,
        "trend": trend,
        "last_high": asdict(highs[-1]) if highs else None,
        "last_low": asdict(lows[-1]) if lows else None,
        "swings": [asdict(s) for s in found[-keep:]],
        "events": events[-5:],
        "atr": latest_atr,
        "bars": len(bars),
    }
