"""Live odds: what the trained model says about the levels near the price now.

During the session window, for the nearest key levels above and below the
price: the probability that the level holds when a 5-minute candle touches
it. If the last complete candle touched a level, that touch is scored as it
happened. Otherwise the touch is imagined for the next candle, closing at the
level from where price is now; how a touching candle closes is unknown in
advance, so the prediction is averaged over the shapes touching candles had
in the past.

The model's number is only given where it has proven itself (confirmed on
unseen months and better than the baseline for this market). Otherwise only
the baseline's number is given: how often such levels held in the past.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np

from jarvis.briefing.levels import KeyLevel, key_levels
from jarvis.learning.market import Bars, resample
from jarvis.training.dataset import (
    FEATURES,
    Context,
    LevelSet,
    TouchRules,
    _day_touches,
    session_levels,
    session_windows,
    touch_features,
)
from jarvis.training.model import SHAPE_FEATURES
from jarvis.training.store import Bundle


def _complete(bars: Bars, minute_bars: Bars) -> Bars:
    """Drop the last bar if it hasn't closed yet."""
    if len(bars) and int(bars.t[-1]) + 60 * bars.minutes > int(minute_bars.t[-1]) + 60:
        return bars.between(0, int(bars.t[-1]))
    return bars


def _append(bars: Bars, price: float, volume: float) -> Bars:
    """``bars`` plus an imagined next bar sitting at ``price``."""
    nxt = int(bars.t[-1]) + 60 * bars.minutes
    return Bars(
        np.r_[bars.t, nxt],
        np.r_[bars.open, price],
        np.r_[bars.high, price],
        np.r_[bars.low, price],
        np.r_[bars.close, price],
        np.r_[bars.volume, volume],
        bars.minutes,
    )


def _held(estimator: Any, x: np.ndarray) -> float:
    return float(np.mean(estimator.predict_proba(x)[:, 1]))


def model_verdict(bundle: Bundle | None, market: str) -> tuple[bool, str]:
    """(use the model's numbers?, why)."""
    if bundle is None:
        return False, "No trained model yet."
    report = bundle.trained.report
    status = report.get("status")
    if status != "confirmed":
        return False, f"The trained model isn't confirmed ({status}): {report.get('reason', '')}."
    if market not in report.get("usable_for", []):
        return False, f"The model isn't better than the baseline for {market} on unseen months."
    return True, "Confirmed on unseen months: better than the baseline."


def level_odds(
    bundle: Bundle | None,
    minute_bars: Bars,
    market: str,
    level_rules: Any,
    window: tuple[int, int],
    rules: TouchRules,
    *,
    each_side: int = 2,
) -> dict[str, Any]:
    use_model, why = model_verdict(bundle, market)
    bars = _complete(resample(minute_bars, rules.timeframe), minute_bars)
    if len(bars) < 30:
        raise ValueError("not enough recent data")
    ctx = Context(bars)
    last = len(bars) - 1
    price = float(bars.close[last])
    unit = float(ctx.atr[last])
    as_of = datetime.fromtimestamp(int(bars.t[last]) + 60 * bars.minutes, UTC).isoformat()
    windows = [w for w in session_windows(ctx, *window) if ctx.day[w[0]] == ctx.day[last]]
    in_session = bool(windows) and windows[0][0] <= last <= windows[0][1]
    minute = int(ctx.minute[last]) + bars.minutes
    out: dict[str, Any] = {
        "market": market,
        "price": price,
        "as_of": as_of,
        "atr_points": round(unit, 2),
        "session": "open" if in_session else ("not_started" if minute <= window[0] else "closed"),
        "model_used": use_model,
        "model_note": why,
        "definition": (
            f"held = within {rules.horizon_minutes} minutes after a {rules.timeframe}-minute "
            f"candle touches the level, price moves {rules.hold:g} ATR away from it before "
            f"going {rules.breach:g} ATR through it (ATR now {unit:.2f} points)"
        ),
        "levels": [],
    }
    if not in_session:
        level_map = key_levels(minute_bars, market, level_rules, each_side=each_side)
        out["levels"] = [
            {"price": lv.price, "labels": lv.labels, "side": side}
            for side, group in (("resistance", level_map.above), ("support", level_map.below))
            for lv in group
        ]
        out["note"] = "Odds are given during the session window the model was trained on."
        return out

    a = windows[0][0]
    levels = session_levels(minute_bars, int(bars.t[a]), market, level_rules, rules)
    if levels is None or not np.isfinite(unit) or unit <= 0:
        raise ValueError("no levels for today's session")
    touches: dict[int, list[int]] = {}
    for i, k, _ in _day_touches(ctx, a, last, levels, rules):
        touches.setdefault(k, []).append(i)
    # A level the last candle touched is scored from the side it was touched from.
    touched_now = {k: side for _, k, side in _day_touches(ctx, last, last, levels, rules)}
    prices = [lv.price for lv in levels.levels]
    above = sorted((k for k, p in enumerate(prices) if p > price), key=lambda k: prices[k])
    below = sorted((k for k, p in enumerate(prices) if p <= price), key=lambda k: -prices[k])
    for side, chosen in (("resistance", above[:each_side]), ("support", below[:each_side])):
        for k in chosen:
            level = levels.levels[k]
            entry = _odds(
                bundle,
                ctx,
                a,
                last,
                level,
                touched_now.get(k, side),
                touches.get(k, []),
                k in touched_now,
                levels,
                market,
                window[0],
                use_model,
            )
            entry["distance_points"] = round(abs(level.price - price), 2)
            entry["distance_atr"] = round(abs(level.price - price) / unit, 2)
            out["levels"].append(entry)
    return out


def _odds(
    bundle: Bundle | None,
    ctx: Context,
    a: int,
    last: int,
    level: KeyLevel,
    side: str,
    touched: list[int],  # bars that touched the level today, up to ``last``
    just_touched: bool,
    levels: LevelSet,
    market: str,
    session_start: int,
    use_model: bool,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "price": level.price,
        "labels": level.labels,
        "side": side,
        "touches_today": len(touched),
        "just_touched": just_touched,
        "p_hold_model": None,
        "p_hold_baseline": None,
    }
    if bundle is None:
        return entry
    trained = bundle.trained
    rows = None
    if just_touched:  # the last candle touched it: score that touch as it happened
        since = float(last - touched[-2]) if len(touched) >= 2 else 99.0
        x = touch_features(
            ctx, a, last, level, side, len(touched), since, levels, market, session_start
        )
        rows = None if x is None else x[None, :]
    else:  # imagine the next candle touching it
        volume = float(ctx.vol_avg[last]) if np.isfinite(ctx.vol_avg[last]) else 0.0
        future = Context(_append(ctx.bars, level.price, volume))
        since = float(last + 1 - touched[-1]) if touched else 99.0
        number = len(touched) + 1
        x = touch_features(
            future, a, last + 1, level, side, number, since, levels, market, session_start
        )
        if x is not None and len(trained.shapes):
            rows = np.repeat(x[None, :], len(trained.shapes), axis=0)
            for j, name in enumerate(SHAPE_FEATURES):
                rows[:, FEATURES.index(name)] = trained.shapes[:, j]
    if rows is None:
        return entry
    entry["p_hold_baseline"] = round(_held(trained.baseline, rows), 3)
    if use_model:
        entry["p_hold_model"] = round(_held(trained.model, rows), 3)
    return entry
