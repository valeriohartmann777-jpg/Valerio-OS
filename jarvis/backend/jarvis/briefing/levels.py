"""Today's key levels for one market, from minute bars.

All times are New York time. A trading day runs from 18:00 to 17:00 (futures
convention); a trading week starts Sunday 18:00. Levels:

- previous day high / low / close and its volume profile (POC, value area) —
  for NQ the regular session (09:30-16:00), for gold the whole trading day
- today's range so far: NQ overnight (before 09:30), gold Asia (19:00-03:00)
- previous week high / low
- intact swing highs / lows on 15-minute bars (not traded through since)
- round numbers

Levels closer together than ``merge_within`` become one level with several
reasons — confluence is what traders look for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from jarvis.learning.features import rolling_max, volume_profile
from jarvis.learning.market import Bars, local_clock, resample

NY = "America/New_York"
RTH = (9 * 60 + 30, 16 * 60)
ASIA = (19 * 60, 3 * 60)


@dataclass(frozen=True)
class LevelRules:
    round_step: float
    major_step: float
    merge_within: float
    regular_session: bool  # NQ: the previous day is the regular session
    swing_bars: int = 5
    swing_days: int = 10


@dataclass
class KeyLevel:
    price: float
    labels: list[str]
    kinds: list[str]
    note: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"price": self.price, "labels": self.labels, "kinds": self.kinds, "note": self.note}


@dataclass
class LevelMap:
    instrument: str
    price: float
    as_of: int  # epoch seconds of the last bar's close
    above: list[KeyLevel] = field(default_factory=list)  # nearest first
    below: list[KeyLevel] = field(default_factory=list)  # nearest first
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument,
            "price": self.price,
            "as_of": self.as_of,
            "above": [level.as_dict() for level in self.above],
            "below": [level.as_dict() for level in self.below],
            "ranges": {k: list(v) for k, v in self.ranges.items()},
        }


# Which study expressions describe which kind of level (for the research notes).
KIND_EXPRESSIONS: dict[str, tuple[str, ...]] = {
    "prev_high": ("prev_high",),
    "prev_low": ("prev_low",),
    "prev_close": ("prev_close",),
    "prev_poc": ("prev_poc",),
    "prev_vah": ("prev_vah",),
    "prev_val": ("prev_val",),
    "prev_week_high": ("prev_week_high",),
    "prev_week_low": ("prev_week_low",),
    "overnight_high": ("window_high(",),
    "overnight_low": ("window_low(",),
    "swing_high": ("pivot_high(",),
    "swing_low": ("pivot_low(",),
    "round": ("round_above(", "round_below("),
}


def key_levels(bars: Bars, instrument: str, rules: LevelRules, each_side: int = 4) -> LevelMap:
    if len(bars) == 0:
        raise ValueError("no bars")
    clock = local_clock(bars.t, NY)
    minute = clock.minute
    trading_day = (clock.day * 1440 + minute + 360) // 1440  # 18:00 starts the next day
    current = int(trading_day[-1])
    price = float(bars.close[-1])
    found: list[tuple[float, str, str]] = []
    ranges: dict[str, tuple[float, float]] = {}

    def add(level: float, label: str, kind: str) -> None:
        if np.isfinite(level):
            found.append((float(level), label, kind))

    # Previous day: the regular session (NQ) or the whole trading day (gold).
    if rules.regular_session:
        in_rth = (minute >= RTH[0]) & (minute < RTH[1])
        days = sorted(set(clock.day[in_rth].tolist()))
        today = int(clock.day[-1])
        complete = [d for d in days if d < today or (d == today and minute[-1] >= RTH[1])]
        if complete:
            mask = in_rth & (clock.day == complete[-1])
            _day_levels(bars, mask, "RTH Vortag", add)
        overnight = (trading_day == current) & ((minute < RTH[0]) | (minute >= 18 * 60))
        if overnight.any():
            low, high = float(bars.low[overnight].min()), float(bars.high[overnight].max())
            ranges["Overnight"] = (low, high)
            add(high, "Overnight-Hoch", "overnight_high")
            add(low, "Overnight-Tief", "overnight_low")
    else:
        earlier = trading_day[trading_day < current]
        if len(earlier):
            _day_levels(bars, trading_day == earlier.max(), "Vortag", add)
        asia = (trading_day == current) & ((minute >= ASIA[0]) | (minute < ASIA[1]))
        if asia.any():
            low, high = float(bars.low[asia].min()), float(bars.high[asia].max())
            ranges["Asia"] = (low, high)
            add(high, "Asia-Hoch", "overnight_high")
            add(low, "Asia-Tief", "overnight_low")

    today_mask = trading_day == current
    ranges["Heute"] = (float(bars.low[today_mask].min()), float(bars.high[today_mask].max()))

    week = (trading_day + 3) // 7  # day 0 was a Thursday; weeks start Monday (= Sunday 18:00)
    earlier_weeks = week[week < week[-1]]
    if len(earlier_weeks):
        mask = week == earlier_weeks.max()
        low, high = float(bars.low[mask].min()), float(bars.high[mask].max())
        ranges["Vorwoche"] = (low, high)
        add(high, "Vorwochenhoch", "prev_week_high")
        add(low, "Vorwochentief", "prev_week_low")

    for level, label, kind in _swings(bars, trading_day, current, rules):
        add(level, label, kind)

    below_round = np.floor(price / rules.round_step) * rules.round_step
    for level in (below_round, below_round + rules.round_step):
        major = abs(level / rules.major_step - round(level / rules.major_step)) < 1e-9
        add(level, f"Runde Zahl ({_plain(rules.major_step)}er)" if major else "Runde Zahl", "round")

    merged = _merge(found, rules.merge_within)
    above = sorted((m for m in merged if m.price > price), key=lambda m: m.price)
    below = sorted((m for m in merged if m.price <= price), key=lambda m: -m.price)
    return LevelMap(
        instrument=instrument,
        price=price,
        as_of=int(bars.t[-1]) + 60 * bars.minutes,
        above=above[:each_side],
        below=below[:each_side],
        ranges=ranges,
    )


def _plain(value: float) -> str:
    return f"{value:g}"


def _day_levels(
    bars: Bars, mask: np.ndarray, suffix: str, add: Callable[[float, str, str], None]
) -> None:
    high, low = float(bars.high[mask].max()), float(bars.low[mask].min())
    close = float(bars.close[mask][-1])
    add(high, f"Hoch {suffix}", "prev_high")
    add(low, f"Tief {suffix}", "prev_low")
    add(close, f"Schluss {suffix}", "prev_close")
    typical = (bars.high[mask] + bars.low[mask] + bars.close[mask]) / 3.0
    poc, vah, val = volume_profile(typical, bars.volume[mask], close)
    add(poc, f"POC {suffix}", "prev_poc")
    add(vah, f"VAH {suffix}", "prev_vah")
    add(val, f"VAL {suffix}", "prev_val")


def _swings(
    bars: Bars, trading_day: np.ndarray, current: int, rules: LevelRules
) -> list[tuple[float, str, str]]:
    """Confirmed 15-minute swing highs/lows of the last days that price hasn't
    traded through since."""
    keep = trading_day >= current - rules.swing_days
    if not keep.any():
        return []
    first = int(np.flatnonzero(keep)[0])
    fifteen = resample(bars.between(int(bars.t[first]), int(bars.t[-1]) + 1), 15)
    n = rules.swing_bars
    out: list[tuple[float, str, str]] = []
    for highs, sign, label, kind in (
        (fifteen.high, 1.0, "Swing-Hoch (15m)", "swing_high"),
        (-fifteen.low, -1.0, "Swing-Tief (15m)", "swing_low"),
    ):
        size = len(highs)
        if size < 2 * n + 1:
            continue
        centre = rolling_max(highs, 2 * n + 1)
        left = rolling_max(highs, n)
        j = np.arange(n, size - n)
        pivots = j[(highs[j] >= centre[j + n]) & (highs[j] > left[j - 1])]
        later_max = np.maximum.accumulate(highs[::-1])[::-1]  # max of highs[k:]
        for p in pivots.tolist():
            intact = p + 1 >= size or later_max[p + 1] <= highs[p]  # a retest is fine
            if intact:
                out.append((sign * float(highs[p]), label, kind))
    return out


def _merge(found: list[tuple[float, str, str]], within: float) -> list[KeyLevel]:
    merged: list[KeyLevel] = []
    for level, label, kind in sorted(found):
        last = merged[-1] if merged else None
        if last is not None and level - last.price <= within:
            if label not in last.labels:
                last.labels.append(label)
            if kind not in last.kinds:
                last.kinds.append(kind)
            continue
        merged.append(KeyLevel(level, [label], [kind]))
    return merged
