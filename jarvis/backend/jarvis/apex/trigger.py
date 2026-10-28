"""Strategy ``zone_rejection_5m``: SETUP → TRIGGER → ORDER_PLAN, on closed 5-minute bars.

Version ``1.0.0`` (every change of a rule or number below is a new version and a new trial).

- **SETUP (long):** the nearest support zone S1 is within ``reach_atr`` ATR(15m) below the price,
  or the price is inside it. Short mirrors it at R1.
- **TRIGGER (long):** the last 5-minute bar that had closed by ``as_of`` — and closed at most one
  bar ago — traded into S1 (low ≤ S1 top), closed back above it (close > S1 top) with a bullish
  body (close > open), and did not break through the zone (low ≥ S1 bottom - stop buffer).
  No such bar → ``WAITING_FOR_TRIGGER``: nothing to order.
- **ORDER_PLAN (long):** buy **stop** one tick above the trigger bar's high (the move must
  continue); stop loss below the lower of the trigger bar's low and S1's bottom by
  ``stop_buffer_atr`` ATR; take profits one tick before the next two resistance zones. Valid for
  ``trigger_valid_minutes``; held at most ``max_hold_minutes``. Rejected (with the reason) when
  the stop is below the market's minimum or wider than ``max_stop_atr`` ATR, no target zone
  exists, or reward/risk to target 1 after costs is below ``min_rr``.

Only bars closed by ``as_of`` are used; the same function runs live and in the track record.
"""

from __future__ import annotations

from typing import Any

from jarvis.apex.candidates import candidate_id
from jarvis.apex.zones import snap
from jarvis.learning.market import Bars
from jarvis.settings import ApexMarketSettings, ApexSettings

STRATEGY = "zone_rejection_5m"
VERSION = "1.0.0"
BAR_MINUTES = 5


def evaluate(
    market: str,
    zones: list[dict[str, Any]],
    bars5: Bars,
    *,
    price: float,
    atr15: float,
    as_of: int,
    trend_60: str,
    spec: ApexMarketSettings,
    cfg: ApexSettings,
    costs: float | None = None,
) -> dict[str, Any]:
    """{status, direction, setups, trigger, candidate, rejected, reasons} at ``as_of``.

    ``bars5`` must hold only 5-minute bars that had closed by ``as_of``; ``costs`` are the
    round-trip costs deducted from the reward (default: the market's ``cost_points``)."""
    supports = sorted((z for z in zones if z["side"] == "support"), key=lambda z: -z["high"])
    resistances = sorted((z for z in zones if z["side"] == "resistance"), key=lambda z: z["low"])
    reasons: list[str] = []
    setups: list[dict[str, Any]] = []
    if atr15 <= 0:
        return _result("NO_TRADE", reasons=["No volatility measure yet (too few 15-minute bars)."])
    for zone, long in (
        (supports[0] if supports else None, True),
        (resistances[0] if resistances else None, False),
    ):
        if zone is None:
            continue
        distance = (
            0.0 if zone["inside"] else ((price - zone["high"]) if long else (zone["low"] - price))
        )
        if distance > cfg.reach_atr * atr15:
            away = distance / atr15
            reasons.append(
                f"{zone['id']} is {away:.1f} ATR away — no setup (limit {cfg.reach_atr:g})."
            )
            continue
        setups.append(
            {
                "zone": zone["id"],
                "direction": "long" if long else "short",
                "distance_atr": round(distance / atr15, 2),
            }
        )
    if not setups:
        return _result("NO_TRADE", reasons=reasons or ["No zone near the price."])

    last = len(bars5) - 1
    fresh = last >= 0 and int(bars5.t[last]) + 60 * BAR_MINUTES * 2 > as_of
    buffer = cfg.stop_buffer_atr * atr15
    rejected: list[dict[str, Any]] = []
    for setup in setups:
        long = setup["direction"] == "long"
        zone = next(z for z in zones if z["id"] == setup["zone"])
        if not fresh:
            reasons.append(f"Setup at {zone['id']}: no closed 5-minute bar in the last 10 minutes.")
            continue
        o, h, lo, c = (float(x[last]) for x in (bars5.open, bars5.high, bars5.low, bars5.close))
        if long:
            touched, closed_back, body = lo <= zone["high"], c > zone["high"], c > o
            held = lo >= zone["low"] - buffer
        else:
            touched, closed_back, body = h >= zone["low"], c < zone["low"], c < o
            held = h <= zone["high"] + buffer
        if not (touched and closed_back and body and held):
            missing = [
                name
                for name, ok in (
                    ("touch the zone", touched),
                    ("close back out of it", closed_back),
                    ("close in the trade's direction", body),
                    ("stay within the stop buffer", held),
                )
                if not ok
            ]
            reasons.append(
                f"Setup at {zone['id']}: the last 5-minute bar did not " + ", ".join(missing) + "."
            )
            continue
        trigger = {
            "bar_utc": int(bars5.t[last]),
            "closed_utc": int(bars5.t[last]) + 60 * BAR_MINUTES,
            "open": o, "high": h, "low": lo, "close": c,
            "rule": f"{'bullish' if long else 'bearish'} 5-minute rejection of {zone['id']}",
        }  # fmt: skip
        plan = _plan(market, zone, long, supports, resistances, trigger, atr15=atr15, as_of=as_of,
                     trend_60=trend_60, spec=spec, cfg=cfg,
                     costs=spec.cost_points if costs is None else costs)  # fmt: skip
        if "reason" in plan:
            rejected.append(plan)
            reasons.append(plan["reason"])
            continue
        return _result("CANDIDATE", direction=setup["direction"], setups=setups, trigger=trigger,
                       candidate=plan, rejected=rejected, reasons=reasons)  # fmt: skip
    return _result("WAITING_FOR_TRIGGER", setups=setups, rejected=rejected, reasons=reasons)


def _plan(
    market: str,
    zone: dict[str, Any],
    long: bool,
    supports: list[dict[str, Any]],
    resistances: list[dict[str, Any]],
    trigger: dict[str, Any],
    *,
    atr15: float,
    as_of: int,
    trend_60: str,
    spec: ApexMarketSettings,
    cfg: ApexSettings,
    costs: float,
) -> dict[str, Any]:
    tick = spec.tick
    buffer = cfg.stop_buffer_atr * atr15
    direction = "long" if long else "short"
    base = {"template": STRATEGY, "direction": direction, "zone": zone["id"]}
    if long:
        entry = snap(trigger["high"] + tick, tick, "up")
        stop = snap(min(trigger["low"], zone["low"]) - buffer, tick, "down")
        ahead = [z for z in resistances if z["low"] > entry + tick]
        targets = [snap(z["low"] - tick, tick, "down") for z in ahead[:2]]
    else:
        entry = snap(trigger["low"] - tick, tick, "down")
        stop = snap(max(trigger["high"], zone["high"]) + buffer, tick, "up")
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
    rr = [round((abs(t - entry) - cost) / risk, 2) for t in targets]
    if rr[0] < cfg.min_rr:
        return {
            **base,
            "reason": f"Reward/risk to target 1 after costs is {rr[0]:g} (minimum {cfg.min_rr:g}).",
        }
    with_trend = (trend_60 == "up") == long if trend_60 in ("up", "down") else None
    fields = {"market": market, "as_of": as_of, "template": STRATEGY, "version": VERSION,
              "direction": direction, "entry": entry, "stop": stop, "targets": targets}  # fmt: skip
    return {
        "id": candidate_id(fields),
        **base,
        "strategy_version": VERSION,
        "market": market,
        "created_at": as_of,
        "entry": {"type": "stop", "price": entry},
        "stop": stop,
        "targets": [
            {"price": t, "rr": r, "zone": z["id"]}
            for t, r, z in zip(targets, rr, ahead, strict=False)
        ],
        "risk_points": round(risk, 10),
        "risk_usd": round(risk * spec.point_value, 2),
        "cost_usd": round(cost * spec.point_value, 2),
        "unit": spec.unit,
        "valid_until": as_of + 60 * cfg.trigger_valid_minutes,
        "max_hold_minutes": cfg.max_hold_minutes,
        "trend_60": trend_60,
        "with_trend": with_trend,
        "trigger": trigger,
        "rules": [
            f"Fill: {'buy' if long else 'sell'} stop {entry:g} — only if the move continues, "
            "only until the validity ends.",
            "Before the fill: target 1 traded first → missed (no chase).",
            "After the fill: stop and target in the same minute → counted as stop (conservative).",
            f"Still open after {cfg.max_hold_minutes} minutes → closed at that minute's close.",
            f"Invalid when price closes beyond the stop {stop:g} before the fill.",
        ],
    }


def _result(
    status: str,
    *,
    direction: str = "none",
    setups: list[dict[str, Any]] | None = None,
    trigger: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
    rejected: list[dict[str, Any]] | None = None,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "strategy": STRATEGY,
        "version": VERSION,
        "status": status,
        "direction": direction,
        "setups": setups or [],
        "trigger": trigger,
        "candidate": candidate,
        "rejected": rejected or [],
        "reasons": reasons or [],
    }
