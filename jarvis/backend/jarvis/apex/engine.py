"""One APEX analysis: quality gate → structure → zones → scenarios → candidates.

``analyze`` is a pure function of the series and the moment ``as_of``. It first cuts the
series to the bars that had closed by ``as_of`` (``Series.until``), so the same moment gives
the same result whether or not later bars exist — tested in ``test_apex.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import numpy as np

from jarvis.apex.candidates import generate
from jarvis.apex.data import Series, assess, current_contract
from jarvis.apex.scenarios import BaseRates, base_rates, scenarios, zone_rate
from jarvis.apex.structure import structure
from jarvis.apex.zones import build_zones
from jarvis.briefing.levels import LevelRules
from jarvis.learning.features import atr
from jarvis.learning.market import Bars, resample
from jarvis.settings import ApexMarketSettings, ApexSettings
from jarvis.training.dataset import TouchRules

ENGINE_VERSION = "apex-1.0.0"


@dataclass(frozen=True)
class MarketContext:
    """Everything fixed about a market that an analysis needs."""

    name: str
    spec: ApexMarketSettings
    level_rules: LevelRules
    levels_market: str  # NQ or XAUUSD (whose level rules apply)
    window: tuple[int, int]  # session window, New York minutes
    touch_rules: TouchRules
    price_range: tuple[float, float]


def closed(bars: Bars, minutes: int, as_of: int) -> Bars:
    """``minutes``-bars that had fully closed by ``as_of`` (an unfinished last bar is dropped)."""
    out = resample(bars, minutes)
    if len(out) and int(out.t[-1]) + 60 * minutes > as_of:
        return out.between(int(out.t[0]), int(out.t[-1]))
    return out


def atr15_of(bars: Bars, as_of: int) -> float:
    fifteen = closed(bars, 15, as_of)
    if len(fifteen) < 15:
        return 0.0
    value = atr(fifteen.high, fifteen.low, fifteen.close, 14)[-1]
    return float(value) if np.isfinite(value) else 0.0


def segments(series: Series) -> list[Bars]:
    """The series split at contract changes."""
    bars = series.bars
    if not series.breaks:
        return [bars]
    edges = [int(bars.t[0]), *series.breaks, int(bars.t[-1]) + 1]
    return [bars.between(a, b) for a, b in pairwise(edges)]


def zones_and_structure(
    series: Series, ctx: MarketContext, cfg: ApexSettings, as_of: int
) -> dict[str, Any]:
    """Structure and zones from the current contract's bars before ``as_of``."""
    current = current_contract(series)
    bars = current.bars.between(as_of - cfg.level_days * 86400, as_of)
    if len(bars) == 0:
        return {"price": None, "atr15": 0.0, "structure": {}, "zones": []}
    a15 = atr15_of(bars, as_of)
    found = {}
    for minutes in cfg.structure_minutes:
        found[str(minutes)] = structure(closed(bars, minutes, as_of), cfg.swing_bars)
    zones = build_zones(
        bars,
        ctx.levels_market,
        ctx.level_rules,
        atr15=a15,
        width_atr=cfg.zone_width_atr,
        tick=ctx.spec.tick,
        each_side=cfg.zones_each_side,
    )
    return {
        "price": float(bars.close[-1]),
        "atr15": a15,
        "structure": found,
        "zones": [z.as_dict() for z in zones],
        "bars": bars,
    }


def candidates_at(
    series: Series, ctx: MarketContext, cfg: ApexSettings, as_of: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Candidates at ``as_of`` (used by the live analysis and the track record alike)."""
    view = zones_and_structure(series, ctx, cfg, as_of)
    if view["price"] is None:
        return [], [], view
    trend_60 = view["structure"].get("60", {}).get("trend", "range")
    made, rejected = generate(
        ctx.name,
        view["zones"],
        price=view["price"],
        atr15=view["atr15"],
        as_of=as_of,
        trend_60=trend_60,
        spec=ctx.spec,
        cfg=cfg,
    )
    return made, rejected, view


def analyze(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    as_of: int,
    *,
    base: BaseRates | None = None,
    track_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    known = series.until(as_of)
    quality = assess(
        known,
        as_of,
        price_range=ctx.price_range,
        stale_minutes=cfg.stale_minutes,
        window_days=cfg.level_days,
        need_days=cfg.history_days,
    )
    head: dict[str, Any] = {
        "engine": ENGINE_VERSION,
        "market": ctx.name,
        "as_of": as_of,
        "data": {
            "source": known.source,
            "label": known.label,
            "synthetic": known.synthetic,
            "proxy": known.proxy,
            "sha256": known.digest(),
            "provenance": known.provenance,
        },
        "quality": quality,
        "spec": {
            "label": ctx.spec.label,
            "tick": ctx.spec.tick,
            "point_value": ctx.spec.point_value,
            "unit": ctx.spec.unit,
            "cost_points": ctx.spec.cost_points,
        },
    }
    if quality["status"] == "BLOCK":
        return {
            **head,
            "blocked": True,
            "structure": {},
            "zones": [],
            "scenarios": [],
            "candidates": [],
            "rejected": [],
            "chart": [],
        }
    made, rejected, view = candidates_at(known, ctx, cfg, as_of)
    if base is None:
        base = base_rates(
            segments(known.until(as_of)),
            ctx.levels_market,
            ctx.level_rules,
            ctx.window,
            ctx.touch_rules,
            as_of,
        )
    found = scenarios(view["zones"], base, view["price"] or 0.0, cfg.min_samples)
    rates = {
        z["id"]: zone_rate(base, z["groups"], z["side"], cfg.min_samples) for z in view["zones"]
    }
    if quality["stale"]:
        rejected = [
            {
                "template": "zone_reaction",
                "reason": "Data is stale — no candidates until it is updated.",
            }
        ]
        made = []
    for c in made:
        c["zone_rate"] = rates.get(c["zone"])
        c["track_record"] = track_record
        c["data_sha256"] = head["data"]["sha256"]
        c["synthetic"] = known.synthetic
        c["engine"] = ENGINE_VERSION
    chart = _chart(view.get("bars"))
    return {
        **head,
        "blocked": False,
        "price": view["price"],
        "atr15": round(view["atr15"], 4),
        "structure": view["structure"],
        "zones": [{**z, "rate": rates.get(z["id"])} for z in view["zones"]],
        "scenarios": found,
        "base_rates": {
            "definition": base.definition(),
            "touches": len(base.decided_before()),
            "skipped": base.dataset.skipped,
        },
        "candidates": made,
        "rejected": rejected,
        "chart": chart,
    }


def _chart(bars: Bars | None, count: int = 160) -> list[list[float]]:
    """Last ``count`` 15-minute bars as [time, open, high, low, close] for the chart."""
    if bars is None or len(bars) == 0:
        return []
    fifteen = resample(bars, 15)
    tail = slice(max(0, len(fifteen) - count), len(fifteen))
    return [
        [int(t), float(o), float(h), float(lo), float(c)]
        for t, o, h, lo, c in zip(
            fifteen.t[tail],
            fifteen.open[tail],
            fifteen.high[tail],
            fifteen.low[tail],
            fifteen.close[tail],
            strict=True,
        )
    ]
