"""Scenarios for the nearest zones, with probabilities that were counted, not guessed.

The base rates come from ``training/dataset.py`` — every touch of a key level in the
market's session window, labelled **held** (price moved ``hold`` ATR away on the good side
first) or **broken** (``breach`` ATR through first) within the horizon. Only touches whose
outcome was already decided **before the moment of analysis** count. For a zone, the rate
uses the touches of the same side and of any of the zone's kinds of level; fewer than
``min_samples`` decided touches → "insufficient data" instead of a number.

"held" and "broken" are complementary among decided touches; touches broken within the
touching bar and touches still undecided at the horizon are left out (and counted).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from jarvis.apex.stats import rate
from jarvis.briefing.levels import LevelRules
from jarvis.learning.market import Bars
from jarvis.training.dataset import FEATURES, Dataset, TouchRules, build, empty_dataset

_COL = {name: k for k, name in enumerate(FEATURES)}


@dataclass
class BaseRates:
    dataset: Dataset
    rules: TouchRules
    window: tuple[int, int]
    as_of: int

    def decided_before(self) -> Dataset:
        horizon = 60 * self.rules.horizon_minutes
        return self.dataset.subset(self.dataset.t + horizon <= self.as_of)

    def definition(self) -> str:
        r = self.rules
        start, end = (f"{m // 60:02d}:{m % 60:02d}" for m in self.window)
        return (
            f"Touches of key levels by {r.timeframe}-minute bars between {start} and {end} New "
            f"York time. Held = price moved {r.hold:g} ATR(14) away on the level's side before "
            f"going {r.breach:g} ATR through it, within {r.horizon_minutes} minutes."
        )


def base_rates(
    segments: list[Bars],
    market: str,
    level_rules: LevelRules,
    window: tuple[int, int],
    rules: TouchRules,
    as_of: int,
) -> BaseRates:
    """All decided touches in the history before ``as_of`` (one build per contract segment,
    so levels never mix two contracts)."""
    parts = [build(seg, market, level_rules, window, rules) for seg in segments if len(seg)]
    dataset = Dataset.concat(parts) if parts else empty_dataset()
    return BaseRates(dataset, rules, window, as_of)


def zone_rate(base: BaseRates, groups: list[str], side: str, min_samples: int) -> dict[str, Any]:
    data = base.decided_before()
    if len(data) == 0 or not groups:
        return rate(0, 0, days=0, min_samples=min_samples)
    support = data.x[:, _COL["support"]] == (1.0 if side == "support" else 0.0)
    kinds = np.zeros(len(data), dtype=bool)
    for group in groups:
        if group in _COL:
            kinds |= data.x[:, _COL[group]] == 1.0
    mask = support & kinds
    held = int(np.sum(data.y[mask] == 1))
    return rate(
        held, int(np.sum(mask)), days=len(np.unique(data.cluster[mask])), min_samples=min_samples
    )


def side_rate(base: BaseRates, side: str, min_samples: int) -> dict[str, Any]:
    data = base.decided_before()
    if len(data) == 0:
        return rate(0, 0, days=0, min_samples=min_samples)
    mask = data.x[:, _COL["support"]] == (1.0 if side == "support" else 0.0)
    held = int(np.sum(data.y[mask] == 1))
    return rate(
        held, int(np.sum(mask)), days=len(np.unique(data.cluster[mask])), min_samples=min_samples
    )


def scenarios(
    zones: list[dict[str, Any]], base: BaseRates, price: float, min_samples: int
) -> list[dict[str, Any]]:
    """Hold / break scenarios for the nearest support and resistance."""
    out: list[dict[str, Any]] = []
    by_id = {z["id"]: z for z in zones}
    for first, second, side in (("S1", "S2", "support"), ("R1", "R2", "resistance")):
        zone = by_id.get(first)
        if zone is None:
            continue
        held = zone_rate(base, zone["groups"], side, min_samples)
        reference = side_rate(base, side, min_samples)
        beyond = by_id.get(second)
        opposite = by_id.get("R1" if side == "support" else "S1")
        span = f"{zone['low']:g}-{zone['high']:g}"
        toward = (
            f"toward {opposite['id']} ({opposite['low']:g}-{opposite['high']:g})"
            if opposite
            else "away from the zone"
        )
        broken = dict(held)
        if held["p"] is not None:
            broken["p"] = round(1 - held["p"], 4)
            broken["ci"] = [round(1 - held["ci"][1], 4), round(1 - held["ci"][0], 4)]
            broken["successes"] = held["n"] - held["successes"]
        out.append(
            {
                "id": f"{first}-hold",
                "zone": first,
                "kind": "hold",
                "side": side,
                "statement": f"{side.capitalize()} {first} ({span}) holds on a touch → {toward}",
                "rate": held,
                "reference": reference,
            }
        )
        out.append(
            {
                "id": f"{first}-break",
                "zone": first,
                "kind": "break",
                "side": side,
                "statement": (
                    f"{side.capitalize()} {first} ({span}) breaks on a touch → "
                    + (
                        f"toward {second} ({beyond['low']:g}-{beyond['high']:g})"
                        if beyond
                        else "no further zone mapped"
                    )
                ),
                "rate": broken,
                "reference": None,
            }
        )
    return out
