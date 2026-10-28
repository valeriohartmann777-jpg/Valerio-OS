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

from jarvis.apex import candidates, setups, trigger
from jarvis.apex.capability import capability_for, factor_matrix
from jarvis.apex.context import bias, interaction_scenarios, path_cards, path_scenarios
from jarvis.apex.data import Series, assess, current_contract
from jarvis.apex.execution import Execution, execution_for
from jarvis.apex.orderflow import orderflow_view
from jarvis.apex.scenarios import BaseRates, base_rates, scenarios, zone_rate
from jarvis.apex.structure import structure
from jarvis.apex.zonemap import ZoneCache, zone_view
from jarvis.apex.zones import build_zones
from jarvis.briefing.levels import LevelRules
from jarvis.learning.features import atr
from jarvis.learning.market import Bars, resample
from jarvis.settings import ApexMarketSettings, ApexSettings
from jarvis.training.dataset import TouchRules

# 1.1.0: quality gate repaired (gaps over the covered span, NO_RECENT_DATA, spikes on
# consecutive minutes only) — see docs/apex/APEX_DATA_DIAGNOSIS.md.
# 1.2.0: execution model (bid/ask side, proxy block), strategy zone_rejection_5m (5-minute
# trigger, stop entry) next to zone_reaction, per-strategy status — docs/APEX_SIGNAL_SPEC.md.
# 1.3.0: capability matrix and evidence statuses, the causal zone map (top four per side,
# lifecycle, episodes), multi-timeframe bias, barrier path events and zone interactions —
# docs/apex/APEX_ZONE_SPEC.md, APEX_PRODUCT_SPEC.md.
# 1.4.0: zone map built at the last 15-minute close (cacheable), the three zone setups
# sweep_reclaim / rejection_rotation / breakout_retest with their stages, the eligibility gate
# and the alert budget — docs/apex/APEX_SIGNAL_RULES.md.
ENGINE_VERSION = "apex-1.4.0"
ZONE_DAYS = 14  # minute data behind the zone map
STRATEGIES = {
    candidates.TEMPLATE: candidates.VERSION,
    trigger.STRATEGY: trigger.VERSION,
    **setups.SETUPS,
}


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


def plans_at(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    as_of: int,
    execution: Execution,
    strategies: tuple[str, ...] = tuple(STRATEGIES),
    *,
    zone_cache: ZoneCache | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Each strategy's decision at ``as_of`` — the live analysis and the track record alike.

    Per strategy: {strategy, version, status, direction, setups, trigger, candidates, rejected,
    reasons}; status NO_TRADE | WAITING_FOR_TRIGGER | CANDIDATE | BLOCKED. The zone setups add
    their ``stage`` and ``setup_state``; ``zone_cache`` reuses zone maps of a 15-minute bar."""
    view = zones_and_structure(series, ctx, cfg, as_of)
    if view["price"] is None:
        return {}, view
    wanted = [name for name in setups.SETUPS if name in strategies]
    if wanted:
        view["zone_map"], view["placed"] = zone_view_at(series, ctx, cfg, as_of, view, zone_cache)
    trend_60 = view["structure"].get("60", {}).get("trend", "range")
    out: dict[str, dict[str, Any]] = {}
    if candidates.TEMPLATE in strategies:
        made, rejected = candidates.generate(
            ctx.name,
            view["zones"],
            price=view["price"],
            atr15=view["atr15"],
            as_of=as_of,
            trend_60=trend_60,
            spec=ctx.spec,
            cfg=cfg,
            costs=execution.costs,
        )
        out[candidates.TEMPLATE] = {
            "strategy": candidates.TEMPLATE,
            "version": candidates.VERSION,
            "status": "CANDIDATE" if made else "NO_TRADE",
            "direction": made[0]["direction"] if len(made) == 1 else "none",
            "setups": [],
            "trigger": None,
            "candidates": made,
            "rejected": rejected,
            "reasons": [r["reason"] for r in rejected],
        }
    if trigger.STRATEGY in strategies:
        # Only the last closed 5-minute bars matter for the trigger.
        recent = view["bars"].between(as_of - 1800, as_of)
        found = trigger.evaluate(
            ctx.name,
            view["zones"],
            closed(recent, trigger.BAR_MINUTES, as_of),
            price=view["price"],
            atr15=view["atr15"],
            as_of=as_of,
            trend_60=trend_60,
            spec=ctx.spec,
            cfg=cfg,
            costs=execution.costs,
        )
        plan = found.pop("candidate")
        out[trigger.STRATEGY] = {**found, "candidates": [plan] if plan else []}
    if wanted:
        bars = view["bars"]
        start5 = as_of - as_of % 300 - 4 * 3600
        bars5 = closed(bars.between(start5, as_of), setups.BAR_MINUTES, as_of)
        minute = bars.between(as_of - setups.BREAK_MAX_HOURS * 3600 - 900, as_of)
        for name in wanted:
            out[name] = setups.evaluate(
                name, ctx.name, view["zone_map"], view["placed"], bars5, minute, as_of=as_of,
                trend_60=trend_60, spec=ctx.spec, cfg=cfg, costs=execution.costs,
                spread=execution.spread,
            )  # fmt: skip
    for decision in out.values():
        if not execution.tradable:
            block(decision, "DATA_UNAVAILABLE: " + execution.reason)
        for c in decision["candidates"]:
            c["execution"] = execution.as_dict()
            c["cost_usd"] = round(execution.total_cost * ctx.spec.point_value, 2)
    return out, view


def zone_view_at(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    as_of: int,
    view: dict[str, Any],
    cache: ZoneCache | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The zone map at ``as_of`` from the current contract's bars of ``view``."""
    capability = capability_for(series, ctx.spec)
    vwap = next(f["status"] for f in factor_matrix(capability, stale=False)
                if f["factor"] == "vwap")  # fmt: skip
    return zone_view(
        view["bars"],
        ctx.name,
        as_of,
        tick=ctx.spec.tick,
        round_step=ctx.level_rules.major_step,
        reach_atr=cfg.reach_atr,
        source_ids=[str(series.provenance.get("source_id") or series.source)],
        vwap_status=vwap,
        days=ZONE_DAYS,
        cache=cache,
    )


def block(decision: dict[str, Any], reason: str) -> None:
    """No order plan from this decision, for ``reason``."""
    decision.update(
        status="BLOCKED",
        direction="none",
        candidates=[],
        rejected=[{"template": decision["strategy"], "reason": reason}],
        reasons=[reason],
    )
    if decision["strategy"] in setups.SETUPS:
        decision.update(setup_state="BLOCKED")
        decision.setdefault("stage", "SCANNING")


def analyze(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    as_of: int,
    *,
    base: BaseRates | None = None,
    track_records: dict[str, Any] | None = None,
    execution: Execution | None = None,
) -> dict[str, Any]:
    known = series.until(as_of)
    execution = execution or execution_for(known, ctx.spec)
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
            "last_bar": int(known.bars.t[-1]) if len(known.bars) else None,
        },
        "quality": quality,
        "spec": {
            "label": ctx.spec.label,
            "tick": ctx.spec.tick,
            "point_value": ctx.spec.point_value,
            "unit": ctx.spec.unit,
            "cost_points": ctx.spec.cost_points,
        },
        "execution": execution.as_dict(),
    }
    capability = capability_for(known, ctx.spec)
    head["capability"] = capability.as_dict()
    head["factors"] = factor_matrix(capability, stale=bool(quality["stale"]))
    if quality["status"] == "BLOCK":
        reason = "Data quality gate: " + "; ".join(
            f"{f['code']} — {f['message']}" for f in quality["findings"] if f["level"] == "BLOCK"
        )
        blocked = {name: _blocked(name, reason) for name in STRATEGIES}
        return {
            **head,
            "blocked": True,
            "structure": {},
            "zones": [],
            "scenarios": [],
            "strategies": blocked,
            "candidates": [],
            "rejected": [],
            "chart": [],
        }
    decisions, view = plans_at(known, ctx, cfg, as_of, execution)
    if not decisions:
        empty = "No bars of the current contract."
        decisions = {name: _blocked(name, empty) for name in STRATEGIES}
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
        for decision in decisions.values():
            block(decision, "Data is stale — no candidates until it is updated.")
    made = [c for d in decisions.values() for c in d["candidates"]]
    for c in made:
        c["zone_rate"] = rates.get(c["zone"])
        c["track_record"] = (track_records or {}).get(c["template"])
        c["data_sha256"] = head["data"]["sha256"]
        c["synthetic"] = known.synthetic
        c["engine"] = ENGINE_VERSION
    chart = _chart(view.get("bars"))
    mapped = view.get("zone_map") or zone_view_at(known, ctx, cfg, as_of, view)[0]
    history = current_contract(known).bars.between(as_of - cfg.history_days * 86400, as_of)
    paths = path_scenarios(
        history, as_of, mapped, horizon_minutes=cfg.path_horizon_minutes,
        window=ctx.window, min_samples=cfg.min_samples,
    )  # fmt: skip
    interactions = interaction_scenarios(mapped, cfg.min_samples)
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
        "strategies": decisions,
        "candidates": made,
        "rejected": [r for d in decisions.values() for r in d["rejected"]],
        "zone_map": mapped,
        "bias": bias(history.between(as_of - 60 * 86400, as_of), as_of),
        "paths": paths,
        "interactions": interactions,
        "path_cards": path_cards(mapped, interactions),
        "auction": orderflow_view(view["bars"], as_of, head["capability"]),
        "chart": chart,
    }


def _blocked(name: str, reason: str) -> dict[str, Any]:
    decision: dict[str, Any] = {
        "strategy": name, "version": STRATEGIES[name], "setups": [], "trigger": None,
    }  # fmt: skip
    block(decision, reason)
    return decision


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
