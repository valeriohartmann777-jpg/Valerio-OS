"""Bias, market paths and context — "where may price go next, by when, and why?" (spec G, H, K).

**Bias** (``BIAS_VERSION``): confirmed structure on closed D1 (New York trading days,
18:00-17:00), H4 and H1 bars. All timeframes with a trend agree → BULLISH / BEARISH; they
disagree → CONFLICTED; no trend anywhere → NEUTRAL. A bias is a reading, not a signal.

**Path event** (``PATH_VERSION``): from the price at ``as_of``, which barrier is touched first
within ``path_horizon_minutes`` (one-minute bars after the moment):

- ``UPPER_FIRST`` — the upper barrier (lower edge of the nearest ranked resistance);
- ``LOWER_FIRST`` — the lower barrier (upper edge of the nearest ranked support);
- ``NEITHER`` — neither before the horizon ends;
- ``AMBIGUOUS`` — both inside the same minute (the order is unknown: never resolved in
  anyone's favour).

These four are exclusive and exhaustive, so their shares sum to 1. Without a ranked zone on a
side the barrier is 2 ATR away (declared fallback). The shares are **counted**: the same event,
in ATR units, from every hourly checkpoint of the history whose outcome was already known
before ``as_of``, with Dirichlet(½) shrinkage and a Wilson interval each. They are labelled
``NOT VALIDATED`` until a calibration run supports them — a counted share is not a calibrated
probability, and never a trade's chance of reaching its target.

**Zone interaction** (``COUNTED``): how the touch episodes of the zones in the snapshot ended
— reject and rotate, sweep and reclaim, break and accept, weak reaction, unresolved — pooled,
with counts. Break-and-retest is a conditional path after a break, not part of the partition.

**Context**: scheduled macro events known at ``as_of`` (the schedule's own known-at time; an
actual value only once released), a blackout window around high-impact events, and the
related markets — ``UNAVAILABLE`` until a feed is connected; a missing source never
corroborates anything.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from jarvis.apex.capability import Evidence
from jarvis.apex.stats import wilson
from jarvis.apex.structure import structure
from jarvis.learning.features import atr as atr_series
from jarvis.learning.market import Bars, local_clock, resample

BIAS_VERSION = "bias-1.0.0"
PATH_VERSION = "path-1.0.0"
NY = "America/New_York"
OUTCOMES = ("UPPER_FIRST", "LOWER_FIRST", "NEITHER", "AMBIGUOUS")
INTERACTIONS = (
    "REJECT_AND_ROTATE",
    "SWEEP_RECLAIM",
    "BREAK_AND_ACCEPT",
    "WEAK_REACTION",
    "UNRESOLVED",
)
FALLBACK_ATR = 2.0


# Bias --------------------------------------------------------------------------------------


def daily_bars(bars: Bars, as_of: int) -> Bars:
    """Closed New York trading days (18:00-17:00) as bars; the running day is left out."""
    if len(bars) == 0:
        return bars
    clock = local_clock(bars.t, NY)
    day = (clock.day * 1440 + clock.minute + 360) // 1440
    starts = np.flatnonzero(np.r_[True, day[1:] != day[:-1]])
    ends = np.r_[starts[1:], len(bars)] - 1
    done = starts[:-1], ends[:-1]  # the last group may still be trading
    if len(done[0]) == 0:
        return Bars(
            bars.t[:0],
            bars.open[:0],
            bars.high[:0],
            bars.low[:0],
            bars.close[:0],
            bars.volume[:0],
            1440,
        )
    s, e = done
    return Bars(
        bars.t[s],
        bars.open[s],
        np.maximum.reduceat(bars.high, s)[: len(s)],
        np.minimum.reduceat(bars.low, s)[: len(s)],
        bars.close[e],
        np.add.reduceat(bars.volume, s)[: len(s)],
        1440,
    )


def _closed(bars: Bars, minutes: int, as_of: int) -> Bars:
    out = resample(bars, minutes)
    if len(out) and int(out.t[-1]) + 60 * minutes > as_of:
        out = out.between(int(out.t[0]), int(out.t[-1]))
    return out


def combine(trends: list[str]) -> str:
    """BULLISH / BEARISH when every timeframe with a trend agrees, CONFLICTED when they
    disagree, NEUTRAL when none has one."""
    directional = [t for t in trends if t in ("up", "down")]
    if not directional:
        return "NEUTRAL"
    if all(t == "up" for t in directional):
        return "BULLISH"
    if all(t == "down" for t in directional):
        return "BEARISH"
    return "CONFLICTED"


def bias(bars: Bars, as_of: int) -> dict[str, Any]:
    frames = {
        "D1": (daily_bars(bars, as_of), 2),
        "H4": (_closed(bars, 240, as_of), 2),
        "H1": (_closed(bars, 60, as_of), 3),
    }
    readings: dict[str, Any] = {}
    reasons: list[str] = []
    for name, (b, n) in frames.items():
        s = (
            structure(b, n)
            if len(b) >= 2 * n + 1
            else {"trend": "unknown", "events": [], "last_high": None, "last_low": None}
        )
        readings[name] = {
            "trend": s["trend"],
            "bars": len(b),
            "last_event": (s["events"] or [None])[-1],
            "last_high": s.get("last_high"),
            "last_low": s.get("last_low"),
        }
        hi, lo = s.get("last_high") or {}, s.get("last_low") or {}
        reasons.append(
            f"{name}: {s['trend']}"
            + (
                f" (last high {hi.get('label') or '—'} {hi.get('price', 0):g}, last low "
                f"{lo.get('label') or '—'} {lo.get('price', 0):g})"
                if hi and lo
                else " (too few closed bars)"
            )
        )
    state = combine([r["trend"] for r in readings.values()])
    h1 = readings["H1"]
    invalidation = None
    if state == "BULLISH" and h1["last_low"]:
        invalidation = f"an H1 close below the last higher low {h1['last_low']['price']:g}"
    elif state == "BEARISH" and h1["last_high"]:
        invalidation = f"an H1 close above the last lower high {h1['last_high']['price']:g}"
    return {
        "version": BIAS_VERSION,
        "state": state,
        "timeframes": readings,
        "reasons": reasons,
        "invalidation": invalidation,
        "updated_at": as_of,
        "is_signal": False,
    }


# Path events -------------------------------------------------------------------------------


def first_touch(high: np.ndarray, low: np.ndarray, upper: float, lower: float) -> str:
    up = high >= upper
    down = low <= lower
    if not up.any() and not down.any():
        return "NEITHER"
    iu = int(np.argmax(up)) if up.any() else len(high)
    il = int(np.argmax(down)) if down.any() else len(low)
    if iu == il:
        return "AMBIGUOUS"
    return "UPPER_FIRST" if iu < il else "LOWER_FIRST"


def barriers(zone_map: dict[str, Any], price: float, a15: float) -> dict[str, Any]:
    res = [z for z in zone_map.get("resistance", []) if z["bounds"]["low"] > price]
    sup = [z for z in zone_map.get("support", []) if z["bounds"]["high"] < price]
    upper_zone = min(res, key=lambda z: z["bounds"]["low"]) if res else None
    lower_zone = max(sup, key=lambda z: z["bounds"]["high"]) if sup else None
    upper = upper_zone["bounds"]["low"] if upper_zone else price + FALLBACK_ATR * a15
    lower = lower_zone["bounds"]["high"] if lower_zone else price - FALLBACK_ATR * a15
    return {
        "upper": round(float(upper), 10),
        "lower": round(float(lower), 10),
        "upper_zone": upper_zone["zone_id"] if upper_zone else None,
        "lower_zone": lower_zone["zone_id"] if lower_zone else None,
        "upper_source": f"resistance #{upper_zone['rank']} lower edge"
        if upper_zone
        else f"price + {FALLBACK_ATR:g} ATR (no ranked resistance)",
        "lower_source": f"support #{lower_zone['rank']} upper edge"
        if lower_zone
        else f"price - {FALLBACK_ATR:g} ATR (no ranked support)",
    }


def path_scenarios(
    bars: Bars,
    as_of: int,
    zone_map: dict[str, Any],
    *,
    horizon_minutes: int,
    window: tuple[int, int],
    min_samples: int,
) -> dict[str, Any]:
    """The barrier event now, and its counted shares from the history before ``as_of``."""
    price = zone_map.get("price")
    a15 = float(zone_map.get("atr15") or 0.0)
    if price is None or a15 <= 0 or len(bars) == 0:
        return {
            "version": PATH_VERSION,
            "available": False,
            "reason": "No price or volatility yet.",
        }
    b = barriers(zone_map, float(price), a15)
    du, dl = (b["upper"] - price) / a15, (price - b["lower"]) / a15
    fifteen = _closed(bars, 15, as_of)
    a = atr_series(fifteen.high, fifteen.low, fifteen.close, 14)
    close_15 = fifteen.t + 900
    clock = local_clock(bars.t, NY)
    hourly = np.flatnonzero(clock.minute % 60 == 0)
    counts = dict.fromkeys(OUTCOMES, 0)
    h = horizon_minutes * 60
    sampled = 0
    for i in hourly.tolist():
        t0 = int(bars.t[i])  # decision at the open of this minute = close of the one before
        if t0 + h > as_of or i == 0:
            continue
        minute = int(clock.minute[i])
        inside = (
            window[0] <= minute < window[1]
            if window[0] < window[1]
            else (minute >= window[0] or minute < window[1])
        )
        if not inside:
            continue
        k = int(np.searchsorted(close_15, t0, side="right")) - 1
        if k < 14 or not np.isfinite(a[k]) or a[k] <= 0:
            continue
        p0 = float(bars.close[i - 1])
        j = int(np.searchsorted(bars.t, t0 + h, side="left"))
        outcome = first_touch(bars.high[i:j], bars.low[i:j], p0 + du * a[k], p0 - dl * a[k])
        counts[outcome] += 1
        sampled += 1
    n = sampled
    shares = {o: (counts[o] + 0.5) / (n + 2.0) for o in OUTCOMES}
    total = sum(shares.values())
    shares = {o: round(v / total, 4) for o, v in shares.items()}
    intervals = {}
    for o in OUTCOMES:
        ci = wilson(counts[o], n) or (0.0, 1.0)
        intervals[o] = [round(ci[0], 4), round(ci[1], 4)]
    effective = round(n * min(1.0, 60 * 60 / h), 1)
    enough = n >= min_samples
    best = max(OUTCOMES[:3], key=lambda o: shares[o])
    return {
        "version": PATH_VERSION,
        "available": True,
        "event_definition": (
            f"first touch within {horizon_minutes} minutes after the moment: upper {b['upper']:g} "
            f"({b['upper_source']}) vs lower {b['lower']:g} ({b['lower_source']}); same-minute "
            "touches of both are AMBIGUOUS"
        ),
        "as_of": as_of,
        "price": price,
        "horizon_minutes": horizon_minutes,
        "barriers": b,
        "distance_atr": {"upper": round(du, 3), "lower": round(dl, 3)},
        "counts": counts,
        "samples": n,
        "effective_samples": effective,
        "shares": shares if enough else None,
        "intervals": intervals if enough else None,
        "partition": "exclusive and exhaustive (sums to 1)",
        "status": "NOT_VALIDATED" if enough else "INSUFFICIENT_DATA",
        "calibrated": False,
        "most_frequent": best if enough else None,
        "note": "Counted shares of the same event in ATR units at earlier hourly checkpoints — "
        "not a calibrated probability, and not a trade's chance of reaching its target.",
    }


def interaction_scenarios(zone_map: dict[str, Any], min_samples: int) -> dict[str, Any]:
    zones = (
        zone_map.get("support", []) + zone_map.get("resistance", []) + zone_map.get("others", [])
    )
    counts = dict.fromkeys(INTERACTIONS, 0)
    for z in zones:
        for e in z.get("episodes", []):
            kind = e.get("kind")
            if kind == "rejection":
                counts["REJECT_AND_ROTATE"] += 1
            elif kind == "sweep_reclaim":
                counts["SWEEP_RECLAIM"] += 1
            elif kind == "break":
                counts["BREAK_AND_ACCEPT"] += 1
            elif kind == "weak":
                counts["WEAK_REACTION"] += 1
            elif kind in ("pending", "open"):
                counts["UNRESOLVED"] += 1
    fakeouts = sum(
        len([t for t in z.get("transitions", []) if "fakeout" in str(t.get("reason"))])
        for z in zones
    )
    n = sum(counts.values())
    enough = n >= min_samples
    return {
        "definition": "how a touch episode of a mapped zone ended (one per episode, exclusive)",
        "counts": counts,
        "samples": n,
        "shares": {k: round(v / n, 4) for k, v in counts.items()} if n and enough else None,
        "fakeouts_after_breaks": fakeouts,
        "status": "NOT_VALIDATED" if enough else "INSUFFICIENT_DATA",
        "conditional": [
            "BREAKOUT_RETEST: after a break is accepted, a return to the broken zone that holds "
            "from the other side (a flip) — watched by the breakout_retest setup.",
            "FAKEOUT: a break that closes back on the original side twice — counted as "
            "counter-evidence on that zone.",
        ],
        "pooled_over_zones": len(zones),
    }


def path_cards(zone_map: dict[str, Any], interactions: dict[str, Any]) -> list[dict[str, Any]]:
    """Plain-language alternative paths at the buy and sell zones (illustrative hypotheses)."""
    cards = []
    shares = interactions.get("shares") or {}
    for key, side, opposite in (
        ("sell_zone", "resistance", "support"),
        ("buy_zone", "support", "resistance"),
    ):
        z = zone_map.get(key)
        if not z:
            continue
        other = zone_map.get(opposite) or []
        target = other[0]["bounds"] if other else None
        cards.append(
            {
                "zone_id": z["zone_id"],
                "side": side,
                "bounds": z["bounds"],
                "state": z["state"],
                "paths": [
                    {
                        "id": "REJECT_AND_ROTATE",
                        "text": f"rejects at the {side} and rotates toward the opposite zone"
                        + (f" ({target['low']:g}-{target['high']:g})" if target else ""),
                        "share": shares.get("REJECT_AND_ROTATE"),
                    },
                    {
                        "id": "SWEEP_RECLAIM",
                        "text": f"sweeps beyond the {side}, then reclaims it",
                        "share": shares.get("SWEEP_RECLAIM"),
                    },
                    {
                        "id": "BREAK_AND_ACCEPT",
                        "text": f"breaks the {side} with two closes beyond and continues "
                        "toward the next zone",
                        "share": shares.get("BREAK_AND_ACCEPT"),
                    },
                    {
                        "id": "WEAK_REACTION",
                        "text": "touches without a decisive reaction",
                        "share": shares.get("WEAK_REACTION"),
                    },
                ],
            }
        )
    return cards


# Context -----------------------------------------------------------------------------------


RELATED = {
    "XAUUSD": ("DXY (US dollar index)", "US 10y real yield", "COMEX GC futures flow"),
    "NQ": ("ES futures", "US 10y yield", "VIX"),
    "MNQ": ("ES futures", "US 10y yield", "VIX"),
}


def market_context(
    market: str,
    as_of: int,
    events: list[dict[str, Any]],
    *,
    before_minutes: int,
    after_minutes: int,
    calendar_loaded: bool,
) -> dict[str, Any]:
    """Point-in-time macro context: only what was known at ``as_of``."""
    known = [
        e
        for e in events
        if int(e["schedule_known_utc"]) <= as_of
        and e["currency"] in ("USD", "ALL")
        and as_of - 86400 <= int(e["scheduled_utc"]) <= as_of + 2 * 86400
    ]
    visible = []
    blocking = []
    for e in known:
        released = e.get("released_utc")
        item = {
            "event": e["event"],
            "category": e["category"],
            "importance": e["importance"],
            "scheduled_utc": int(e["scheduled_utc"]),
            "source": e["source"],
            "actual": e.get("actual") if released is not None and int(released) <= as_of else None,
            "minutes_away": round((int(e["scheduled_utc"]) - as_of) / 60, 1),
        }
        visible.append(item)
        if (
            e["importance"] == "high"
            and -after_minutes * 60 <= int(e["scheduled_utc"]) - as_of <= before_minutes * 60
        ):
            blocking.append(item)
    if not calendar_loaded:
        macro = {
            "status": Evidence.UNAVAILABLE.value,
            "reason": "No macro calendar imported — event risk is unknown, so no signal can "
            "pass the macro check.",
        }
    elif blocking:
        macro = {
            "status": Evidence.CONTRADICTED.value,
            "reason": f"High-impact event within the blackout: {blocking[0]['event']}",
        }
    else:
        macro = {
            "status": Evidence.CONFIRMED.value,
            "reason": "No high-impact event inside the blackout window.",
        }
    return {
        "as_of": as_of,
        "macro": macro,
        "events": sorted(visible, key=lambda x: x["scheduled_utc"])[:12],
        "blackout": {
            "before_minutes": before_minutes,
            "after_minutes": after_minutes,
            "active": bool(blocking),
        },
        "related_markets": [
            {
                "market": m,
                "status": Evidence.UNAVAILABLE.value,
                "reason": "no feed connected — never used as corroboration",
            }
            for m in RELATED.get(market, ())
        ],
        "unscheduled_risk": "UNRESOLVED EVENT RISK cannot be detected without a news feed.",
    }
