"""Support and resistance zones: key levels with a width, their reasons and test history.

Levels come from the same objective rules as the morning briefing (``briefing/levels.py``):
previous day high / low / close and value area, overnight or Asia range, previous week,
intact 15-minute swings, round numbers. Each becomes a zone of ± ``width_atr`` x ATR(14) of
15-minute bars (at least half the briefing's merge distance and two ticks); overlapping
zones merge and keep all reasons. ``score`` only ranks zones — it is not a probability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from jarvis.briefing.levels import LevelRules, key_levels
from jarvis.learning.market import Bars, resample
from jarvis.training.dataset import KIND_GROUPS

GROUP_OF = {kind: group for group, kinds in KIND_GROUPS.items() for kind in kinds}
GROUP_LABELS = {
    "k_day_extreme": "previous day high/low",
    "k_day_close": "previous day close",
    "k_profile": "volume profile (POC / value area)",
    "k_overnight": "overnight / Asia range",
    "k_week": "previous week high/low",
    "k_swing": "swing high/low",
    "k_round": "round number",
}


@dataclass
class Zone:
    low: float
    high: float
    kinds: list[str]
    labels: list[str]
    major_round: bool = False
    tests: int = 0  # 15-minute bars that entered the zone from outside (zone history window)
    side: str = ""  # support | resistance (relative to the price at analysis)
    inside: bool = False  # the price is in the zone right now
    distance_atr: float = 0.0
    id: str = ""
    groups: list[str] = field(default_factory=list)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    @property
    def score(self) -> float:
        week = 1.0 if "k_week" in self.groups else 0.0
        return round(
            len(self.groups) + (1.0 if self.major_round else 0.0) + 0.5 * min(self.tests, 3) + week,
            2,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "low": self.low,
            "high": self.high,
            "mid": self.mid,
            "side": self.side,
            "inside": self.inside,
            "distance_atr": round(self.distance_atr, 2),
            "kinds": self.kinds,
            "groups": self.groups,
            "reasons": sorted({GROUP_LABELS[g] for g in self.groups}),
            "labels": self.labels,
            "major_round": self.major_round,
            "tests": self.tests,
            "score": self.score,
        }


def snap(price: float, tick: float, mode: str = "nearest") -> float:
    steps = price / tick
    value = (
        np.floor(steps + 1e-9)
        if mode == "down"
        else np.ceil(steps - 1e-9)
        if mode == "up"
        else np.round(steps)
    )
    return round(float(value) * tick, 10)


def build_zones(
    minute_bars: Bars,
    market: str,
    rules: LevelRules,
    *,
    atr15: float,
    width_atr: float,
    tick: float,
    each_side: int,
) -> list[Zone]:
    """Zones as of the last bar of ``minute_bars``, nearest first on each side."""
    level_map = key_levels(minute_bars, market, rules, each_side=each_side * 3, round_steps=2)
    price = level_map.price
    half = max(width_atr * atr15, rules.merge_within / 2, 2 * tick)
    raw: list[Zone] = []
    for level in level_map.above + level_map.below:
        ratio = level.price / rules.major_step
        major = "round" in level.kinds and abs(ratio - round(ratio)) < 1e-9
        raw.append(
            Zone(
                low=snap(level.price - half, tick, "down"),
                high=snap(level.price + half, tick, "up"),
                kinds=list(level.kinds),
                labels=list(level.labels),
                major_round=major,
            )
        )
    raw.sort(key=lambda z: z.low)
    merged: list[Zone] = []
    for zone in raw:
        if merged and zone.low <= merged[-1].high:
            last = merged[-1]
            last.high = max(last.high, zone.high)
            last.kinds = sorted(set(last.kinds) | set(zone.kinds))
            last.labels = last.labels + [x for x in zone.labels if x not in last.labels]
            last.major_round = last.major_round or zone.major_round
        else:
            merged.append(zone)
    fifteen = resample(minute_bars, 15)
    for zone in merged:
        zone.groups = sorted({GROUP_OF[k] for k in zone.kinds if k in GROUP_OF})
        zone.tests = _tests(fifteen, zone.low, zone.high)
        zone.inside = zone.low <= price <= zone.high
        zone.side = "resistance" if zone.mid > price else "support"
        edge = zone.low if zone.side == "resistance" else zone.high
        zone.distance_atr = 0.0 if zone.inside else abs(edge - price) / atr15 if atr15 > 0 else 0.0
    supports = sorted((z for z in merged if z.side == "support"), key=lambda z: -z.high)[:each_side]
    resistances = sorted((z for z in merged if z.side == "resistance"), key=lambda z: z.low)[
        :each_side
    ]
    for k, zone in enumerate(supports, 1):
        zone.id = f"S{k}"
    for k, zone in enumerate(resistances, 1):
        zone.id = f"R{k}"
    return resistances[::-1] + supports  # top to bottom


def _tests(bars: Bars, low: float, high: float) -> int:
    """Bars entering [low, high] after a bar that stayed outside it."""
    if len(bars) < 2:
        return 0
    touching = (bars.low <= high) & (bars.high >= low)
    return int(np.sum(touching[1:] & ~touching[:-1]))
