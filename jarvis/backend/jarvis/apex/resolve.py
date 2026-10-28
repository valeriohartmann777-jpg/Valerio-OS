"""What happened to a candidate afterwards, judged on the one-minute bars after it was made.

States: ``pending`` (not filled yet, still valid) · ``open`` (filled, neither stop nor target
yet) · ``win`` (target 1) · ``loss`` (stop) · ``timeout`` (closed at the market after the
maximum hold) · ``expired`` (never filled, or a stop entry cancelled because price closed
beyond the stop first) · ``missed`` (target 1 traded before the fill).

Prices are broker order prices (``execution.py``): buys execute on the ask, sells on the bid.
With ``spread`` > 0 the bars are bid prices and the ask is bid + ``spread``; with 0 the bars
are the traded price and the spread is inside ``cost_points``.

Conservative, because one-minute bars don't show the order of high and low:
- a limit fills at its price, never better, when a bar trades through it;
- a stop entry fills at its price, or at the bar's open when the bar opened beyond it (gap);
- in the bar that fills, only the stop can be hit (a target in that bar is not counted);
- after the fill, a bar that reaches both stop and target counts as a stop, and a bar that
  opens beyond the stop exits at that open;
- R multiples are net of ``cost_points`` per round trip.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from jarvis.learning.market import Bars

FINAL = ("win", "loss", "timeout", "expired", "missed")


def resolve(
    candidate: dict[str, Any],
    bars: Bars,
    *,
    cost_points: float,
    now: int,
    spread: float = 0.0,
) -> dict[str, Any]:
    """Outcome of ``candidate`` given the minute bars after it (``now`` = how far data goes)."""
    created = int(candidate["created_at"])
    entry = float(candidate["entry"]["price"])
    stop_entry = candidate["entry"].get("type") == "stop"
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
    t = after.t[start:]
    # Buys execute on the ask (bid + spread): a long's entry, a short's exits.
    enter = spread if long else 0.0
    leave = 0.0 if long else spread
    e_open, e_high, e_low = (x[start:] + enter for x in (after.open, after.high, after.low))
    x_open, x_high, x_low, x_close = (
        x[start:] + leave for x in (after.open, after.high, after.low, after.close)
    )
    step = 60 * bars.minutes

    filled_at: int | None = None
    fill_price = entry
    fill_index = -1
    for i in range(len(t)):
        if t[i] >= valid_until:
            return _out("expired", checked=int(t[i]))
        if stop_entry:
            touched = e_high[i] >= entry if long else e_low[i] <= entry
            gap = (e_open[i] > entry) if long else (e_open[i] < entry)
            price = float(e_open[i]) if touched and gap else entry
        else:
            touched = e_low[i] <= entry if long else e_high[i] >= entry
            price = entry
        if touched:
            filled_at, fill_index, fill_price = int(t[i]), i, price
            break
        reached = x_high[i] >= target if long else x_low[i] <= target
        if reached:
            return _out("missed", checked=int(t[i]) + step)
        beyond = x_close[i] <= stop if long else x_close[i] >= stop
        if stop_entry and beyond:
            return _out("expired", checked=int(t[i]) + step, cancelled=True)
    if filled_at is None:
        checked = int(t[-1]) + step if len(t) else created
        if checked >= valid_until or now >= valid_until:
            return _out("expired", checked=max(checked, valid_until))
        return _out("pending", checked=checked)

    def r(price: float) -> float:
        move = (price - fill_price) if long else (fill_price - price)
        return round((move - cost_points) / risk, 4)

    def done(state: str, i: int, price: float) -> dict[str, Any]:
        return _out(state, filled_at, int(t[i]) + step, price, r(price), best, worst, fill_price)

    best = worst = 0.0
    for i in range(fill_index, len(t)):
        hit_stop = x_low[i] <= stop if long else x_high[i] >= stop
        hit_target = (x_high[i] >= target if long else x_low[i] <= target) and i > fill_index
        favourable = (x_high[i] - fill_price) if long else (fill_price - x_low[i])
        adverse = (fill_price - x_low[i]) if long else (x_high[i] - fill_price)
        best = max(best, favourable / risk)
        worst = max(worst, adverse / risk)
        if hit_stop:
            gapped = i > fill_index and (x_open[i] < stop if long else x_open[i] > stop)
            return done("loss", i, float(x_open[i]) if gapped else stop)
        if hit_target:
            return done("win", i, target)
        if int(t[i]) + step >= filled_at + hold:
            return done("timeout", i, float(x_close[i]))
    checked = int(t[-1]) + step if len(t) else filled_at
    return {
        **_out("open", filled_at, None, None, None, best, worst, fill_price),
        "checked": checked,
    }


def _out(
    state: str,
    filled_at: int | None = None,
    exit_at: int | None = None,
    exit_price: float | None = None,
    r_multiple: float | None = None,
    mfe: float = 0.0,
    mae: float = 0.0,
    fill_price: float | None = None,
    *,
    checked: int | None = None,
    cancelled: bool = False,
) -> dict[str, Any]:
    out = {
        "state": state,
        "final": state in FINAL,
        "filled_at": filled_at,
        "fill_price": None if fill_price is None else round(fill_price, 10),
        "exit_at": exit_at,
        "exit_price": None if exit_price is None else round(exit_price, 10),
        "r": r_multiple,
        "mfe_r": round(mfe, 3),
        "mae_r": round(mae, 3),
        "checked": checked if checked is not None else exit_at,
    }
    if cancelled:
        out["cancelled"] = "price closed beyond the stop before the stop entry filled"
    return out
