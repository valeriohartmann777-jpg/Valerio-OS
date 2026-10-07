"""Support/resistance from causal swing clustering, and objective level touches.

Zones are built only from swings that are already confirmed, merged when their
prices lie within ``merge_tol_atr`` x ATR of an existing zone centre. A zone has no
fixed role: it is resistance while above price and support while below, so polarity
is a property of where price is, which research can test directly.

A *touch* of a level is defined as: price enters the zone around the level and
afterwards moves away by at least ``away_atr`` x ATR before re-entering. Bars that
stay inside the zone are one touch, not many. A touch is known only when the move
away completes, which is when the event is recorded.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Zone:
    zid: int
    center: float
    lo: float
    hi: float
    n_pivots: int
    first_pos: int
    last_pos: int
    prices: list = field(default_factory=list)


def pivot_zones(
    bars: pd.DataFrame,
    pivots: pd.DataFrame,
    atr_series: pd.Series,
    *,
    merge_tol_atr: float = 0.25,
    max_age_bars: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Causal zone tracker.

    Returns ``(state, zones)``. ``state`` (per bar) has the nearest zone above and below
    the close: centre, number of pivots, age in bars and bars since the last pivot.
    ``zones`` lists every zone with its final statistics (descriptive).
    """
    c = bars["close"].to_numpy(float)
    a = atr_series.to_numpy(float)
    n = len(c)
    by_conf: dict[int, list] = {}
    for r in pivots.itertuples(index=False):
        by_conf.setdefault(int(r.confirm_pos), []).append((float(r.price), int(r.pivot_pos)))
    zones: list[Zone] = []
    centers: list[float] = []
    cols = {k: np.full(n, np.nan) for k in (
        "res_center", "res_n", "res_age", "res_since", "sup_center", "sup_n", "sup_age", "sup_since")}
    next_id = 0
    for i in range(n):
        for price, ppos in by_conf.get(i, ()):
            tol = merge_tol_atr * a[i] if np.isfinite(a[i]) else 0.0
            k = bisect.bisect_left(centers, price)
            best = None
            for j in (k - 1, k):
                if 0 <= j < len(zones) and abs(zones[j].center - price) <= tol:
                    if best is None or abs(zones[j].center - price) < abs(zones[best].center - price):
                        best = j
            if best is None:
                z = Zone(next_id, price, price, price, 1, ppos, ppos, [price])
                next_id += 1
                zones.insert(k, z)
                centers.insert(k, price)
            else:
                z = zones[best]
                z.prices.append(price)
                z.n_pivots += 1
                z.center = float(np.mean(z.prices))
                z.lo, z.hi = min(z.lo, price), max(z.hi, price)
                z.last_pos = max(z.last_pos, ppos)
                zones.pop(best)
                centers.pop(best)
                k2 = bisect.bisect_left(centers, z.center)
                zones.insert(k2, z)
                centers.insert(k2, z.center)
        if max_age_bars is not None and zones:
            keep = [z for z in zones if i - z.last_pos <= max_age_bars]
            if len(keep) != len(zones):
                zones = keep
                centers = [z.center for z in zones]
        if not zones or not np.isfinite(c[i]):
            continue
        k = bisect.bisect_right(centers, c[i])
        if k < len(zones):
            z = zones[k]
            cols["res_center"][i], cols["res_n"][i] = z.center, z.n_pivots
            cols["res_age"][i], cols["res_since"][i] = i - z.first_pos, i - z.last_pos
        if k - 1 >= 0:
            z = zones[k - 1]
            cols["sup_center"][i], cols["sup_n"][i] = z.center, z.n_pivots
            cols["sup_age"][i], cols["sup_since"][i] = i - z.first_pos, i - z.last_pos
    state = pd.DataFrame(cols, index=bars.index)
    zlist = pd.DataFrame(
        [{"zid": z.zid, "center": z.center, "lo": z.lo, "hi": z.hi, "n_pivots": z.n_pivots, "first_pos": z.first_pos, "last_pos": z.last_pos} for z in zones]
    )
    return state, zlist


def level_touches(
    bars: pd.DataFrame,
    level: np.ndarray | pd.Series,
    atr_series: pd.Series,
    *,
    side: str,
    zone_atr: float = 0.10,
    away_atr: float = 0.50,
    break_atr: float = 0.25,
) -> pd.DataFrame:
    """Objective touches of a (possibly time-varying, causal) level.

    side='support': price approaches from above. A level is ARMED once a close sits above
    level + zone (a support below price; a level price has not yet traded above is not
    support). A touch starts when low <= level + zone, is CONFIRMED when a later close
    >= level + zone + away, and the level is BROKEN (one event, then inactive until the
    level value changes) when a close < level - break. side='resistance' mirrors this.

    Returns events: touch_start_pos, confirm_pos (known), level, touch_number (1 = first
    touch since the level appeared), reaction_atr, and break events.
    """
    lv = np.asarray(level, float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    a = atr_series.to_numpy(float)
    n = len(c)
    sgn = 1.0 if side == "support" else -1.0
    if side not in ("support", "resistance"):
        raise ValueError(side)
    rows = []
    state = "unarmed"
    start = -1
    touches = 0
    prev_level = np.nan
    for i in range(n):
        L, A = lv[i], a[i]
        if not (np.isfinite(L) and np.isfinite(A)):
            state, touches, prev_level = "unarmed", 0, np.nan
            continue
        if L != prev_level:
            state, touches = "unarmed", 0
            prev_level = L
        zone = zone_atr * A
        if state == "broken":
            continue
        if state == "unarmed":
            if sgn * (c[i] - L) <= zone:
                continue
            state = "away"
        if sgn * (c[i] - L) < -break_atr * A:
            rows.append({"event": "break", "touch_start_pos": start, "confirm_pos": i, "level": L, "touch_number": touches, "reaction_atr": np.nan})
            state, touches = "broken", 0
            continue
        entered = (l[i] <= L + zone) if sgn > 0 else (h[i] >= L - zone)
        if state == "away" and entered:
            state, start = "touching", i
        if state == "touching":
            moved = sgn * (c[i] - L) >= zone + away_atr * A
            if moved and i > start:
                touches += 1
                seg = l[start: i + 1] if sgn > 0 else h[start: i + 1]
                ext = seg.min() if sgn > 0 else seg.max()
                rows.append({"event": "touch", "touch_start_pos": start, "confirm_pos": i, "level": L, "touch_number": touches,
                             "reaction_atr": sgn * (c[i] - ext) / A})
                state = "away"
    return pd.DataFrame(rows, columns=["event", "touch_start_pos", "confirm_pos", "level", "touch_number", "reaction_atr"])
