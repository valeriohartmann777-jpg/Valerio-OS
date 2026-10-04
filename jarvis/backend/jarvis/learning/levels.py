"""Level studies: how support and resistance actually behave, measured.

A study takes a level (any expression, e.g. ``pivot_low(10)`` or
``prev_week_high``) and finds every *touch*: price reaching the level's zone
coming from the right side (from above for support, from below for
resistance). The level is the one known before the touching bar (no
look-ahead). From the touching bar's close, the study records what happened
within the horizon: **held** (price reached ``hold`` points beyond the level
on the good side first), **broken** (``breach`` points through it first) or
undecided. A bar that does both counts as broken.

Chance is measured, not assumed: a control group takes random moments in the
same period and session where price touches an arbitrary price *the same
way* — from the same side, starting the same distance away — and records
the same outcomes. ``expected_rate`` is how often those meaningless prices
"held"; ``edge_z`` compares the two. About 2 or more means the level holds
more often than chance; around 0 means it is just a price. Touches already
broken within the touching bar are counted separately (``broken_on_touch``).

Studies only ever see the in-sample period: what the research model learns
here can't leak into the out-of-sample check of the strategies it builds.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any, Literal

import numpy as np
from pydantic import Field, field_validator

from jarvis.learning.features import FeatureFrame, _shift
from jarvis.learning.market import Bars, resample
from jarvis.learning.strategy import (
    TIMEFRAME_MINUTES,
    Instrument,
    RuleError,
    Session,
    Timeframe,
    _Strict,
    parse_condition,
    parse_expression,
)

MAX_CANDIDATES = 40000  # control moments examined per study


class LevelStudy(_Strict):
    name: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=400)
    instrument: Instrument
    timeframe: Timeframe
    session: Session
    level: str = Field(description="the level's price (an expression)")
    side: Literal["support", "resistance"]
    tolerance: str = Field("0.1 * atr(14)", description="touch zone around the level, in points")
    hold: str = Field("1.0 * atr(14)", description="move away that counts as holding")
    breach: str = Field("0.5 * atr(14)", description="move through that counts as broken")
    horizon_minutes: int = Field(60, ge=5, le=1440)
    when: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("level", "tolerance", "hold", "breach")
    @classmethod
    def _expression(cls, value: str) -> str:
        try:
            parse_expression(value)
        except RuleError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("when")
    @classmethod
    def _rules(cls, rules: list[str]) -> list[str]:
        for rule in rules:
            try:
                parse_condition(rule)
            except RuleError as exc:
                raise ValueError(f"{rule!r}: {exc}") from exc
        return rules


def run_study(study: LevelStudy, minute_bars: Bars, start: date, end: date) -> dict[str, Any]:
    """Run ``study`` on the minute bars between ``start`` and ``end`` (exclusive)."""
    lo = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
    hi = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp())
    bars = resample(minute_bars.between(lo, hi), TIMEFRAME_MINUTES[study.timeframe])
    frame = FeatureFrame(bars, study.session)

    def known(expression: str) -> np.ndarray:
        """As known at the previous bar's close, i.e. before the touching bar."""
        return _shift(frame.value(parse_expression(expression)), 1)

    level, tolerance = known(study.level), known(study.tolerance)
    hold, breach = known(study.hold), known(study.breach)
    allowed = frame.in_session.copy()
    for rule in study.when:
        allowed &= _shift(frame.condition(parse_condition(rule)).astype(np.float64), 1) == 1
    with np.errstate(invalid="ignore"):
        allowed &= np.isfinite(hold) & np.isfinite(breach) & (hold > 0) & (breach > 0)
    horizon = max(1, math.ceil(study.horizon_minutes / bars.minutes))
    session_last = np.full(frame.n, -1, dtype=np.int64)
    for s, e in zip(frame.group_starts.tolist(), frame.group_ends.tolist(), strict=True):
        session_last[s : e + 1] = e

    touches = _touches(
        frame, study.side, level, tolerance, hold, breach, allowed, horizon, session_last
    )
    offsets = [t.offset for t in touches if t.outcome != "through"]
    control = _control(
        frame, study.side, offsets, tolerance, hold, breach, allowed, horizon, session_last
    )
    result = _summary(touches, control, frame.session_id)
    years = (bars.t // 86400).astype("datetime64[D]").astype("datetime64[Y]").astype(int) + 1970
    result["by_year"] = {}
    for year in sorted({int(years[t.bar]) for t in touches}):
        mine = [t for t in touches if years[t.bar] == year]
        theirs = [c for c in control if years[c[0]] == year]
        result["by_year"][str(year)] = {
            "touches": len(mine),
            "held_rate": _round(_held(mine)[0]),
            "expected_rate": _round(_share(theirs)),
        }
    return result


class Touch:
    __slots__ = ("adverse", "bar", "favourable", "number", "offset", "outcome")

    def __init__(
        self, bar: int, outcome: str, offset: float, favourable: float, adverse: float, number: int
    ) -> None:
        self.bar, self.outcome, self.offset = bar, outcome, offset
        self.favourable, self.adverse, self.number = favourable, adverse, number


def _first(mask: np.ndarray) -> int | None:
    hits = np.flatnonzero(mask)
    return int(hits[0]) if len(hits) else None


def _outcome(frame: FeatureFrame, side: str, i: int, last: int, good: float, bad: float) -> str:
    """From bar i's close: did price reach ``good`` before ``bad`` (bars i+1..last)?"""
    b = frame.bars
    highs, lows = b.high[i + 1 : last + 1], b.low[i + 1 : last + 1]
    if side == "support":  # good above, bad below
        reached, failed = _first(highs >= good), _first(lows <= bad)
    else:
        reached, failed = _first(lows <= good), _first(highs >= bad)
    if failed is not None and (reached is None or failed <= reached):
        return "broken"
    return "held" if reached is not None else "undecided"


def _touch_at(
    frame: FeatureFrame,
    side: str,
    i: int,
    price: float,
    tolerance: float,
    hold: float,
    breach: float,
    last: int,
) -> tuple[str, float, float, float] | None:
    """(outcome, offset of the close from the level, favourable, adverse) if
    bar i touches ``price`` coming from the right side, else None."""
    b = frame.bars
    close = float(b.close[i])
    if side == "support":
        zone = price + tolerance
        if not (b.low[i] <= zone < b.low[i - 1]):
            return None
        good, bad = price + hold, price - breach
        through = b.low[i] <= bad
        offset = close - price
        favourable = float(b.high[i + 1 : last + 1].max() - price) if last > i else 0.0
        adverse = float(price - b.low[i : last + 1].min())
    else:
        zone = price - tolerance
        if not (b.high[i] >= zone > b.high[i - 1]):
            return None
        good, bad = price - hold, price + breach
        through = b.high[i] >= bad
        offset = price - close
        favourable = float(price - b.low[i + 1 : last + 1].min()) if last > i else 0.0
        adverse = float(b.high[i : last + 1].max() - price)
    outcome = "through" if through else _outcome(frame, side, i, last, good, bad)
    return outcome, offset, favourable, adverse


def _touches(
    frame: FeatureFrame,
    side: str,
    level: np.ndarray,
    tolerance: np.ndarray,
    hold: np.ndarray,
    breach: np.ndarray,
    allowed: np.ndarray,
    horizon: int,
    session_last: np.ndarray,
) -> list[Touch]:
    b = frame.bars
    with np.errstate(invalid="ignore"):
        usable = allowed & np.isfinite(level) & np.isfinite(tolerance)
        if side == "support":
            zone = level + tolerance
            maybe = (b.low <= zone) & np.r_[False, b.low[:-1] > zone[1:]]
        else:
            zone = level - tolerance
            maybe = (b.high >= zone) & np.r_[False, b.high[:-1] < zone[1:]]
    seen: Counter[tuple[int, float]] = Counter()
    out: list[Touch] = []
    for i in np.flatnonzero(usable & maybe).tolist():
        last = min(int(session_last[i]), i + horizon)
        price = float(level[i])
        touch = _touch_at(
            frame, side, i, price, float(tolerance[i]), float(hold[i]), float(breach[i]), last
        )
        if touch is None:
            continue
        outcome, offset, favourable, adverse = touch
        key = (int(frame.session_id[i]), round(price, 6))
        seen[key] += 1
        out.append(Touch(i, outcome, offset, favourable, adverse, seen[key]))
    return out


def _control(
    frame: FeatureFrame,
    side: str,
    offsets: list[float],
    tolerance: np.ndarray,
    hold: np.ndarray,
    breach: np.ndarray,
    allowed: np.ndarray,
    horizon: int,
    session_last: np.ndarray,
) -> list[tuple[int, str]]:
    """(bar, outcome) of arbitrary prices touched the way the real level was:
    at random moments, a price placed at one of the real touches' distances
    from the close, kept only if that bar touches it from the same side."""
    if not offsets:
        return []
    with np.errstate(invalid="ignore"):
        usable = allowed & np.isfinite(tolerance)
    eligible = np.flatnonzero(usable[1:-1]) + 1
    step = max(1, len(eligible) // MAX_CANDIDATES)
    close = frame.bars.close
    out: list[tuple[int, str]] = []
    for k, j in enumerate(eligible[::step].tolist()):
        offset = offsets[(k * 7919) % len(offsets)]  # spread over the real distances
        price = close[j] - offset if side == "support" else close[j] + offset
        last = min(int(session_last[j]), j + horizon)
        touch = _touch_at(
            frame,
            side,
            j,
            float(price),
            float(tolerance[j]),
            float(hold[j]),
            float(breach[j]),
            last,
        )
        if touch is not None and touch[0] != "through":
            out.append((j, touch[0]))
    return out


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _held(touches: list[Touch]) -> tuple[float | None, int]:
    """Share of decided touches (after the touching bar) that held."""
    decided = [t for t in touches if t.outcome in ("held", "broken")]
    if not decided:
        return None, 0
    return sum(t.outcome == "held" for t in decided) / len(decided), len(decided)


def _share(control: list[tuple[int, str]]) -> float | None:
    decided = [c for c in control if c[1] in ("held", "broken")]
    return sum(c[1] == "held" for c in decided) / len(decided) if decided else None


def _clustered(pairs: list[tuple[int, bool]]) -> tuple[float, float, int] | None:
    """Held share and its variance with errors clustered by session: touches on
    the same day aren't independent, so they count as one cluster."""
    groups: dict[int, list[int]] = {}
    for session, held in pairs:
        counts = groups.setdefault(session, [0, 0])
        counts[0] += int(held)
        counts[1] += 1
    total = sum(n for _, n in groups.values())
    if total == 0 or len(groups) < 2:
        return None
    share = sum(h for h, _ in groups.values()) / total
    spread = sum((h - share * n) ** 2 for h, n in groups.values()) / total**2
    spread *= len(groups) / (len(groups) - 1)
    return share, spread, total


def _summary(
    touches: list[Touch], control: list[tuple[int, str]], session_of: np.ndarray
) -> dict[str, Any]:
    held, decided = _held(touches)
    expected = _share(control)
    c_decided = sum(c[1] in ("held", "broken") for c in control)
    real = _clustered(
        [
            (int(session_of[t.bar]), t.outcome == "held")
            for t in touches
            if t.outcome in ("held", "broken")
        ]
    )
    chance = _clustered(
        [
            (int(session_of[j]), outcome == "held")
            for j, outcome in control
            if outcome in ("held", "broken")
        ]
    )
    edge_z = None
    if real and chance and decided >= 5 and c_decided >= 5:
        spread = math.sqrt(real[1] + chance[1])
        edge_z = (real[0] - chance[0]) / spread if spread > 0 else 0.0
    outcomes = Counter(t.outcome for t in touches)
    by_touch: dict[str, Any] = {}
    for label, members in (
        ("first", [t for t in touches if t.number == 1]),
        ("second", [t for t in touches if t.number == 2]),
        ("third_or_later", [t for t in touches if t.number >= 3]),
    ):
        rate, n = _held(members)
        by_touch[label] = {"decided": n, "held_rate": _round(rate)}
    return {
        "touches": len(touches),
        "held": outcomes.get("held", 0),
        "broken": outcomes.get("broken", 0),
        "broken_on_touch": outcomes.get("through", 0),
        "undecided": outcomes.get("undecided", 0),
        "held_rate": _round(held),
        "expected_rate": _round(expected),
        "controls": c_decided,
        "edge_z": _round(edge_z, 2),
        "by_touch": by_touch,
        "avg_favourable_points": _round(
            float(np.mean([t.favourable for t in touches])) if touches else None, 2
        ),
        "avg_adverse_points": _round(
            float(np.mean([t.adverse for t in touches])) if touches else None, 2
        ),
    }
