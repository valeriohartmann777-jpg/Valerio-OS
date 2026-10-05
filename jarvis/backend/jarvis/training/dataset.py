"""The training data: every touch of a key level, the situation at the touch
and what happened next.

Per trading day and market, the key levels are taken at the start of the
session window (the same levels as the morning briefing: previous day,
overnight / Asia range, previous week, volume profile, intact swings, round
numbers). During the window, a *touch* is a bar reaching a level's zone from
one side: coming down to it (support) or up to it (resistance). Whether it is
support or resistance follows from where price comes from, so a level that was
broken earlier can be tested from the other side.

What the model may know is what a trader knows at the touching bar's close:
the level, the bars up to and including that one. The label comes after it:
**held** (price moves ``hold`` ATRs away from the level on the good side
first) or **broken** (``breach`` ATRs through it first) within the horizon.
Touches already broken within the touching bar and touches still undecided
at the horizon are left out (and counted).

Distances are measured in ATR(14) of the bars as known before the touching
bar, and signed so that positive always means "in favour of the level
holding": above support, below resistance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from jarvis.briefing.levels import KeyLevel, LevelRules, key_levels
from jarvis.learning.features import atr, ewm, rolling_mean
from jarvis.learning.market import Bars, local_clock, resample

NY = "America/New_York"

# Kinds of level, grouped (briefing.levels names → feature).
KIND_GROUPS: dict[str, tuple[str, ...]] = {
    "k_day_extreme": ("prev_high", "prev_low"),
    "k_day_close": ("prev_close",),
    "k_profile": ("prev_poc", "prev_vah", "prev_val"),
    "k_overnight": ("overnight_high", "overnight_low"),
    "k_week": ("prev_week_high", "prev_week_low"),
    "k_swing": ("swing_high", "swing_low"),
    "k_round": ("round",),
}

FEATURES: tuple[str, ...] = (
    "nq",
    "support",
    *KIND_GROUPS,
    "k_major_round",
    "confluence",
    "flipped",
    "touch",
    "since_last",
    "minutes",
    "weekday",
    "offset",
    "bar_range",
    "wick",
    "approach_30",
    "approach_60",
    "trend",
    "vwap",
    "session_pos",
    "session_range",
    "from_open",
    "vol_ratio",
    "vol_regime",
    "atr_bp",
    "room",
)

# What the baseline knows: the market, the kind of level, the side and how the
# touching candle closed. The model has to beat that to count.
BASELINE_FEATURES: tuple[str, ...] = (
    "nq",
    "support",
    *KIND_GROUPS,
    "k_major_round",
    "offset",
)

FEATURE_LABELS: dict[str, str] = {
    "nq": "Market (NQ or gold)",
    "support": "Support or resistance",
    "k_day_extreme": "Previous day high/low",
    "k_day_close": "Previous day close",
    "k_profile": "Volume profile (POC / value area)",
    "k_overnight": "Overnight / Asia range",
    "k_week": "Previous week high/low",
    "k_swing": "Swing high/low",
    "k_round": "Round number",
    "k_major_round": "Major round number",
    "confluence": "Confluence (kinds at the level)",
    "flipped": "Role flipped (broken earlier)",
    "touch": "Touch number today",
    "since_last": "Bars since the last touch",
    "minutes": "Time into the session",
    "weekday": "Weekday",
    "offset": "Close of the touching candle vs. the level",
    "bar_range": "Size of the touching candle",
    "wick": "Rejection wick",
    "approach_30": "Move into the level (30 min)",
    "approach_60": "Move into the level (60 min)",
    "trend": "Trend (vs. EMA 50)",
    "vwap": "Distance from VWAP",
    "session_pos": "Position in today's range",
    "session_range": "Today's range so far",
    "from_open": "Move since the session open",
    "vol_ratio": "Volume of the touching candle",
    "vol_regime": "Volatility vs. usual",
    "atr_bp": "Volatility level",
    "room": "Room to the next level beyond",
}

_INDEX = {name: k for k, name in enumerate(FEATURES)}
BASELINE_COLUMNS = [_INDEX[name] for name in BASELINE_FEATURES]
_NO_TOUCH_YET = 99.0
_ROOM_CAP = 20.0


@dataclass(frozen=True)
class TouchRules:
    timeframe: int = 5  # minutes per bar
    horizon_minutes: int = 60
    tolerance: float = 0.1  # in ATRs
    hold: float = 1.0
    breach: float = 0.5
    history_days: int = 16
    round_steps: int = 4

    @property
    def horizon_bars(self) -> int:
        return max(1, -(-self.horizon_minutes // self.timeframe))


@dataclass
class Dataset:
    x: np.ndarray  # (n, len(FEATURES))
    y: np.ndarray  # 1 = held, 0 = broken
    t: np.ndarray  # close of the touching bar, epoch seconds
    cluster: np.ndarray  # one id per market and trading day
    market: np.ndarray  # index into ``markets``
    markets: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)  # through, undecided

    def __len__(self) -> int:
        return len(self.y)

    def subset(self, mask: np.ndarray) -> Dataset:
        return Dataset(
            self.x[mask],
            self.y[mask],
            self.t[mask],
            self.cluster[mask],
            self.market[mask],
            self.markets,
            dict(self.skipped),
        )

    @staticmethod
    def concat(parts: list[Dataset]) -> Dataset:
        markets: list[str] = []
        for part in parts:
            markets += [m for m in part.markets if m not in markets]
        skipped: dict[str, int] = {}
        for part in parts:
            for key, value in part.skipped.items():
                skipped[key] = skipped.get(key, 0) + value
        if not parts:
            return empty_dataset()
        remap = [np.array([markets.index(m) for m in p.markets], dtype=np.int64) for p in parts]
        out = Dataset(
            np.concatenate([p.x for p in parts]),
            np.concatenate([p.y for p in parts]),
            np.concatenate([p.t for p in parts]),
            np.concatenate([p.cluster for p in parts]),
            np.concatenate(
                [r[p.market] if len(p) else p.market for p, r in zip(parts, remap, strict=True)]
            ),
            markets,
            skipped,
        )
        order = np.argsort(out.t, kind="stable")
        return out.subset(order)


def empty_dataset() -> Dataset:
    return Dataset(
        np.empty((0, len(FEATURES))),
        np.empty(0, dtype=np.int64),
        np.empty(0, dtype=np.int64),
        np.empty(0, dtype=np.int64),
        np.empty(0, dtype=np.int64),
    )


class Context:
    """Indicators on the touch timeframe, computed once for all bars."""

    def __init__(self, bars: Bars) -> None:
        self.bars = bars
        h, low, c = bars.high, bars.low, bars.close
        self.atr = atr(h, low, c, 14)
        self.atr_slow = atr(h, low, c, 100)
        self.ema = ewm(c, 2.0 / 51.0, seed=50)
        self.vol_avg = rolling_mean(bars.volume, 20)
        clock = local_clock(bars.t, NY)
        self.day, self.minute, self.weekday = clock.day, clock.minute, clock.weekday
        self.trading_day = (clock.day * 1440 + clock.minute + 360) // 1440  # 18:00 starts a day

    def day_end(self, i: int) -> int:
        """Last bar of the trading day of bar i."""
        return int(np.searchsorted(self.trading_day, self.trading_day[i], side="right")) - 1


@dataclass(frozen=True)
class LevelSet:
    """The key levels of one session, as known at its start."""

    levels: list[KeyLevel]
    price: float  # when they were taken
    major_step: float

    def beyond(self, price: float, side: str) -> float | None:
        """The next level past ``price`` in the breaking direction."""
        if side == "support":
            lower = [lv.price for lv in self.levels if lv.price < price - 1e-9]
            return max(lower) if lower else None
        higher = [lv.price for lv in self.levels if lv.price > price + 1e-9]
        return min(higher) if higher else None


def session_levels(
    minute_bars: Bars, start: int, market: str, level_rules: LevelRules, rules: TouchRules
) -> LevelSet | None:
    """Key levels from the minute bars before ``start`` (epoch seconds)."""
    history = minute_bars.between(start - rules.history_days * 86400, start)
    if len(history) == 0 or start - int(history.t[-1]) > 6 * 3600:
        return None  # no recent data: the levels would be stale
    level_map = key_levels(
        history, market, level_rules, each_side=10_000, round_steps=rules.round_steps
    )
    return LevelSet(level_map.above + level_map.below, level_map.price, level_rules.major_step)


def touch_features(
    ctx: Context,
    a: int,
    i: int,
    level: KeyLevel,
    side: str,
    number: int,
    since_last: float,
    levels: LevelSet,
    market: str,
    session_start: int,
) -> np.ndarray | None:
    """Features of a touch of ``level`` by bar ``i`` (session from bar ``a``),
    as known at bar i's close. None if the ATR isn't known yet."""
    b = ctx.bars
    unit = float(ctx.atr[i - 1]) if i >= 1 else float("nan")
    if not np.isfinite(unit) or unit <= 0:
        return None
    d = 1.0 if side == "support" else -1.0
    p = level.price
    o, h, low, c = float(b.open[i]), float(b.high[i]), float(b.low[i]), float(b.close[i])
    v = float(b.volume[i])
    x = np.full(len(FEATURES), np.nan)

    def put(name: str, value: float) -> None:
        x[_INDEX[name]] = value

    put("nq", 1.0 if market == "NQ" else 0.0)
    put("support", 1.0 if side == "support" else 0.0)
    for group, kinds in KIND_GROUPS.items():
        put(group, 1.0 if any(k in kinds for k in level.kinds) else 0.0)
    major = (
        levels.major_step > 0 and abs(p / levels.major_step - round(p / levels.major_step)) < 1e-9
    )
    put("k_major_round", 1.0 if "round" in level.kinds and major else 0.0)
    put("confluence", float(len(level.kinds)))
    was_above = p > levels.price
    put("flipped", 1.0 if (side == "support") == was_above else 0.0)
    put("touch", float(min(number, 6)))
    put("since_last", float(min(since_last, _NO_TOUCH_YET)))
    put("minutes", float((int(ctx.minute[i]) + b.minutes - session_start) % 1440))
    put("weekday", float(ctx.weekday[i]))
    put("offset", d * (c - p) / unit)
    put("bar_range", (h - low) / unit)
    put("wick", ((min(o, c) - low) if side == "support" else (h - max(o, c))) / unit)
    for name, back in (("approach_30", 30), ("approach_60", 60)):
        j = i - back // b.minutes
        if j >= 0:
            put(name, d * (c - float(b.close[j])) / unit)
    if np.isfinite(ctx.ema[i]):
        put("trend", d * (c - float(ctx.ema[i])) / unit)
    window = slice(a, i + 1)
    s_high, s_low = float(b.high[window].max()), float(b.low[window].min())
    typical = (b.high[window] + b.low[window] + b.close[window]) / 3.0
    volume = b.volume[window]
    if volume.sum() > 0:
        put("vwap", d * (c - float((typical * volume).sum() / volume.sum())) / unit)
    span = s_high - s_low
    position = (c - s_low) / span if span > 0 else 0.5
    put("session_pos", position if side == "support" else 1.0 - position)
    put("session_range", span / unit)
    put("from_open", d * (c - float(b.open[a])) / unit)
    average = float(ctx.vol_avg[i - 1])
    if np.isfinite(average) and average > 0:
        put("vol_ratio", v / average)
    slow = float(ctx.atr_slow[i - 1])
    if np.isfinite(slow) and slow > 0:
        put("vol_regime", unit / slow)
    put("atr_bp", unit / c * 1e4 if c > 0 else np.nan)
    beyond = levels.beyond(p, side)
    put("room", _ROOM_CAP if beyond is None else min(abs(p - beyond) / unit, _ROOM_CAP))
    return x


def outcome(ctx: Context, i: int, price: float, side: str, rules: TouchRules) -> str:
    """held / broken / undecided after bar i, or through (broken within bar i)."""
    b = ctx.bars
    unit = float(ctx.atr[i - 1])
    hold, breach = rules.hold * unit, rules.breach * unit
    last = min(i + rules.horizon_bars, ctx.day_end(i))
    highs, lows = b.high[i + 1 : last + 1], b.low[i + 1 : last + 1]
    if side == "support":
        if b.low[i] <= price - breach:
            return "through"
        reached, failed = _first(highs >= price + hold), _first(lows <= price - breach)
    else:
        if b.high[i] >= price + breach:
            return "through"
        reached, failed = _first(lows <= price - hold), _first(highs >= price + breach)
    if failed is not None and (reached is None or failed <= reached):
        return "broken"
    return "held" if reached is not None else "undecided"


def _first(mask: np.ndarray) -> int | None:
    hits = np.flatnonzero(mask)
    return int(hits[0]) if len(hits) else None


def touches_of(ctx: Context, i: int, price: float, rules: TouchRules) -> list[str]:
    """Sides from which bar i touches ``price`` ('support': coming down to it)."""
    b = ctx.bars
    if i < 1:
        return []
    tol = rules.tolerance * float(ctx.atr[i - 1])
    if not np.isfinite(tol):
        return []
    sides = []
    if b.low[i] <= price + tol < b.low[i - 1]:
        sides.append("support")
    if b.high[i] >= price - tol > b.high[i - 1]:
        sides.append("resistance")
    return sides


def _day_touches(
    ctx: Context, a: int, z: int, levels: LevelSet, rules: TouchRules
) -> list[tuple[int, int, str]]:
    """(bar, level index, side) of every touch in bars a..z, in time order."""
    b = ctx.bars
    idx = np.arange(max(a, 1), z + 1)
    if len(idx) == 0:
        return []
    tol = rules.tolerance * ctx.atr[idx - 1]
    prices = np.array([lv.price for lv in levels.levels])[:, None]
    with np.errstate(invalid="ignore"):
        zone = prices + tol
        support = (b.low[idx] <= zone) & (b.low[idx - 1] > zone)
        zone = prices - tol
        resistance = (b.high[idx] >= zone) & (b.high[idx - 1] < zone)
    found = [(int(idx[j]), int(k), "support") for k, j in zip(*np.nonzero(support), strict=True)]
    found += [
        (int(idx[j]), int(k), "resistance") for k, j in zip(*np.nonzero(resistance), strict=True)
    ]
    found.sort()
    return found


def session_windows(ctx: Context, start: int, end: int) -> list[tuple[int, int]]:
    """(first, last) bar of each day's window [start, end) in New York minutes."""
    minutes = ctx.bars.minutes
    inside = (ctx.minute >= start) & (ctx.minute + minutes <= end)
    idx = np.flatnonzero(inside)
    if len(idx) == 0:
        return []
    days = ctx.day[idx]
    breaks = np.flatnonzero(np.diff(days) != 0) + 1
    firsts, lasts = idx[np.r_[0, breaks]], idx[np.r_[breaks - 1, len(idx) - 1]]
    return list(zip(firsts.tolist(), lasts.tolist(), strict=True))


def build(
    minute_bars: Bars,
    market: str,
    level_rules: LevelRules,
    window: tuple[int, int],
    rules: TouchRules,
    *,
    market_index: int = 0,
) -> Dataset:
    """All decided touches of ``market``'s key levels in its session window."""
    bars = resample(minute_bars, rules.timeframe)
    if len(bars) == 0:
        return empty_dataset()
    ctx = Context(bars)
    rows: list[np.ndarray] = []
    labels: list[int] = []
    times: list[int] = []
    clusters: list[int] = []
    skipped = {"through": 0, "undecided": 0}
    for a, z in session_windows(ctx, *window):
        levels = session_levels(minute_bars, int(bars.t[a]), market, level_rules, rules)
        if levels is None or not levels.levels:
            continue
        counts: dict[int, int] = {}
        last_touch: dict[int, int] = {}
        for i, k, side in _day_touches(ctx, a, z, levels, rules):
            level = levels.levels[k]
            counts[k] = counts.get(k, 0) + 1
            since = float(i - last_touch[k]) if k in last_touch else _NO_TOUCH_YET
            last_touch[k] = i
            result = outcome(ctx, i, level.price, side, rules)
            if result in ("through", "undecided"):
                skipped[result] += 1
                continue
            x = touch_features(ctx, a, i, level, side, counts[k], since, levels, market, window[0])
            if x is None:
                continue
            rows.append(x)
            labels.append(1 if result == "held" else 0)
            times.append(int(bars.t[i]) + 60 * bars.minutes)
            clusters.append(market_index * 1_000_000 + int(ctx.trading_day[i]))
    if not rows:
        out = empty_dataset()
        out.markets, out.skipped = [market], skipped
        return out
    return Dataset(
        np.vstack(rows),
        np.array(labels, dtype=np.int64),
        np.array(times, dtype=np.int64),
        np.array(clusters, dtype=np.int64),
        np.zeros(len(rows), dtype=np.int64),
        [market],
        skipped,
    )
