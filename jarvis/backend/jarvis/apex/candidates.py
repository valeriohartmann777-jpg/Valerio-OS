"""Trade candidates: entry, stop and targets from zones — deterministic, documented rules.

Template ``zone_reaction`` (one rule set, the same for live analysis and the track record):

- **Long at support:** price above a support zone that is within ``reach_atr`` ATR(15m).
  Entry: buy limit at the zone's top. Stop: below the zone's bottom by ``stop_buffer_atr``
  ATR. Target 1: one tick below the nearest resistance zone above the entry; target 2 at the
  next one. **Short at resistance** mirrors it.
- Rejected (with the reason kept) when the stop is smaller than the market's minimum or wider
  than ``max_stop_atr`` ATR, when there is no target zone, or when reward/risk to target 1
  after round-trip costs is below ``min_rr``. Price inside a zone → no candidate (no chasing).
- Valid ``valid_minutes`` for the fill, then held at most ``max_hold_minutes``.
- Reward/risk counts the costs the execution model deducts (``execution.py``): on bid-only
  data the spread is modelled on the order's side, the rest of the costs comes off the reward.

These are research candidates. They carry no probability of their own: the zone's counted
base rate and the template's measured track record are attached separately.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jarvis.apex.zones import snap
from jarvis.settings import ApexMarketSettings, ApexSettings

TEMPLATE = "zone_reaction"
# 1.1.0: costs from the execution model (bid/ask side on bid-only data); 1.0.0 = flat costs.
VERSION = "1.1.0"


def candidate_id(fields: dict[str, Any]) -> str:
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return "apx-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def generate(
    market: str,
    zones: list[dict[str, Any]],
    *,
    price: float,
    atr15: float,
    as_of: int,
    trend_60: str,
    spec: ApexMarketSettings,
    cfg: ApexSettings,
    costs: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(candidates, rejected) for the nearest support and resistance zones; ``costs`` are the
    round-trip costs deducted from the reward (default: the market's ``cost_points``)."""
    out: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    supports = [z for z in zones if z["side"] == "support"]
    resistances = [z for z in zones if z["side"] == "resistance"]
    supports.sort(key=lambda z: -z["high"])
    resistances.sort(key=lambda z: z["low"])
    for zone, direction in (
        (supports[0] if supports else None, "long"),
        (resistances[0] if resistances else None, "short"),
    ):
        if zone is None:
            continue
        made = _one(
            market, zone, direction, supports, resistances,
            price=price, atr15=atr15, as_of=as_of, trend_60=trend_60, spec=spec, cfg=cfg,
            costs=spec.cost_points if costs is None else costs,
        )  # fmt: skip
        (out if "entry" in made else rejected).append(made)
    return out, rejected


def _one(
    market: str,
    zone: dict[str, Any],
    direction: str,
    supports: list[dict[str, Any]],
    resistances: list[dict[str, Any]],
    *,
    price: float,
    atr15: float,
    as_of: int,
    trend_60: str,
    spec: ApexMarketSettings,
    cfg: ApexSettings,
    costs: float,
) -> dict[str, Any]:
    long = direction == "long"
    base = {"template": TEMPLATE, "direction": direction, "zone": zone["id"]}
    if zone["inside"]:
        return {
            **base,
            "reason": f"Price is inside {zone['id']} — no entry while it is being tested.",
        }
    if atr15 <= 0:
        return {**base, "reason": "No volatility measure yet (too few 15-minute bars)."}
    distance = (price - zone["high"]) if long else (zone["low"] - price)
    if distance > cfg.reach_atr * atr15:
        return {
            **base,
            "reason": f"{zone['id']} is {distance / atr15:.1f} ATR away (limit {cfg.reach_atr:g}).",
        }
    tick = spec.tick
    buffer = cfg.stop_buffer_atr * atr15
    if long:
        entry = snap(zone["high"], tick, "down")
        stop = snap(zone["low"] - buffer, tick, "down")
        ahead = [z for z in resistances if z["low"] > entry + tick]
        targets = [snap(z["low"] - tick, tick, "down") for z in ahead[:2]]
    else:
        entry = snap(zone["low"], tick, "up")
        stop = snap(zone["high"] + buffer, tick, "up")
        ahead = [z for z in supports if z["high"] < entry - tick]
        targets = [snap(z["high"] + tick, tick, "up") for z in ahead[:2]]
    risk = abs(entry - stop)
    if risk < spec.min_stop_points:
        return {
            **base,
            "reason": f"Stop {risk:g} is below the minimum of {spec.min_stop_points:g} points.",
        }
    if risk > cfg.max_stop_atr * atr15:
        return {
            **base,
            "reason": f"Stop {risk / atr15:.1f} ATR is wider than {cfg.max_stop_atr:g} ATR.",
        }
    if not targets:
        return {**base, "reason": "No opposing zone mapped for a target."}
    cost = costs
    reward = [abs(t - entry) for t in targets]
    rr = [round((r - cost) / risk, 2) for r in reward]
    if rr[0] < cfg.min_rr:
        return {
            **base,
            "reason": f"Reward/risk to target 1 after costs is {rr[0]:g} (minimum {cfg.min_rr:g}).",
        }
    with_trend = (trend_60 == "up") == long if trend_60 in ("up", "down") else None
    fields = {
        "market": market,
        "as_of": as_of,
        "template": TEMPLATE,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "targets": targets,
    }
    return {
        "id": candidate_id(fields),
        **base,
        "strategy_version": VERSION,
        "market": market,
        "created_at": as_of,
        "entry": {"type": "limit", "price": entry},
        "stop": stop,
        "targets": [
            {"price": t, "rr": r, "zone": z["id"]}
            for t, r, z in zip(targets, rr, ahead, strict=False)
        ],
        "risk_points": round(risk, 10),
        "risk_usd": round(risk * spec.point_value, 2),
        "cost_usd": round(cost * spec.point_value, 2),
        "unit": spec.unit,
        "valid_until": as_of + 60 * cfg.valid_minutes,
        "max_hold_minutes": cfg.max_hold_minutes,
        "trend_60": trend_60,
        "with_trend": with_trend,
        "rules": [
            f"Fill: {'buy' if long else 'sell'} limit {entry:g}, only until the validity ends.",
            "Before the fill: target 1 traded first → candidate missed (no chase).",
            "After the fill: stop and target in the same minute → counted as stop (conservative).",
            f"Still open after {cfg.max_hold_minutes} minutes → closed at that minute's close.",
        ],
    }
