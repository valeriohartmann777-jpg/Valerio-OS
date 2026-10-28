"""What happened to a candidate afterwards, judged on the one-minute bars after it was made.

States: ``pending`` (not filled yet, still valid) · ``open`` (filled, neither stop nor target
yet) · ``win`` (target 1) · ``loss`` (stop) · ``timeout`` (closed at the market after the
maximum hold) · ``expired`` (never filled) · ``missed`` (target 1 traded before the fill).

Conservative, because one-minute bars don't show the order of high and low:
- a limit fills at its price, never better, when a bar trades through it;
- in the bar that fills, only the stop can be hit (a target in that bar is not counted);
- after the fill, a bar that reaches both stop and target counts as a stop;
- R multiples are net of the market's round-trip costs.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from jarvis.learning.market import Bars

FINAL = ("win", "loss", "timeout", "expired", "missed")


def resolve(
    candidate: dict[str, Any], bars: Bars, *, cost_points: float, now: int
) -> dict[str, Any]:
    """Outcome of ``candidate`` given the minute bars after it (``now`` = how far data goes)."""
    created = int(candidate["created_at"])
    entry = float(candidate["entry"]["price"])
    stop = float(candidate["stop"])
    target = float(candidate["targets"][0]["price"])
    long = candidate["direction"] == "long"
    risk = abs(entry - stop)
    valid_until = int(candidate["valid_until"])
    hold = 60 * int(candidate["max_hold_minutes"])
    after = bars.between(created, now + 1)
    # Only bars that started after the candidate was made (a bar open at that moment closed
    # before it and was part of the analysis).
    start = int(np.searchsorted(after.t, created, side="left"))
    t, high, low, close = (
        after.t[start:],
        after.high[start:],
        after.low[start:],
        after.close[start:],
    )
    step = 60 * bars.minutes

    def r(price: float) -> float:
        move = (price - entry) if long else (entry - price)
        return round((move - cost_points) / risk, 4)

    filled_at: int | None = None
    fill_index = -1
    for i in range(len(t)):
        if t[i] >= valid_until:
            return _out("expired", checked=int(t[i]))
        touched = low[i] <= entry if long else high[i] >= entry
        if touched:
            filled_at, fill_index = int(t[i]), i
            break
        reached = high[i] >= target if long else low[i] <= target
        if reached:
            return _out("missed", checked=int(t[i]) + step)
    if filled_at is None:
        checked = int(t[-1]) + step if len(t) else created
        if checked >= valid_until or now >= valid_until:
            return _out("expired", checked=max(checked, valid_until))
        return _out("pending", checked=checked)

    best = worst = 0.0
    for i in range(fill_index, len(t)):
        hit_stop = low[i] <= stop if long else high[i] >= stop
        hit_target = (high[i] >= target if long else low[i] <= target) and i > fill_index
        favourable = (high[i] - entry) if long else (entry - low[i])
        adverse = (entry - low[i]) if long else (high[i] - entry)
        best = max(best, favourable / risk)
        worst = max(worst, adverse / risk)
        if hit_stop:
            return _out("loss", filled_at, int(t[i]) + step, stop, r(stop), best, worst)
        if hit_target:
            return _out("win", filled_at, int(t[i]) + step, target, r(target), best, worst)
        if int(t[i]) + step >= filled_at + hold:
            price = float(close[i])
            return _out("timeout", filled_at, int(t[i]) + step, price, r(price), best, worst)
    checked = int(t[-1]) + step if len(t) else filled_at
    return {**_out("open", filled_at, None, None, None, best, worst), "checked": checked}


def _out(
    state: str,
    filled_at: int | None = None,
    exit_at: int | None = None,
    exit_price: float | None = None,
    r_multiple: float | None = None,
    mfe: float = 0.0,
    mae: float = 0.0,
    *,
    checked: int | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "final": state in FINAL,
        "filled_at": filled_at,
        "exit_at": exit_at,
        "exit_price": exit_price,
        "r": r_multiple,
        "mfe_r": round(mfe, 3),
        "mae_r": round(mae, 3),
        "checked": checked if checked is not None else exit_at,
    }
