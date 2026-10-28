"""Track record of the candidate rules: walk forward through history, as if live.

At every decision point (each ``eval_step_minutes`` inside the market's session window) the
series is cut to what was known then (``Series.until``) and **the same function as the live
analysis** (``engine.candidates_at``) makes candidates. Each is resolved on the bars after it
with the conservative rules of ``resolve.py``, net of costs. A candidate repeating an earlier
one of the same day (same direction and entry) is counted once.

Reported: counts of every outcome, win rate with a 95% Wilson interval, expectancy in R with a
day-clustered interval, profit factor, drawdown — for the whole period and separately for
the last ``oos_share`` of it. Fewer than ``min_samples`` filled trades → "insufficient".
A positive number is a measurement on this data, never a promise.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from jarvis.apex.data import Series, trading_minutes
from jarvis.apex.engine import ENGINE_VERSION, MarketContext, candidates_at
from jarvis.apex.resolve import resolve
from jarvis.apex.stats import clustered_mean, wilson
from jarvis.settings import ApexSettings

NY = ZoneInfo("America/New_York")
Progress = Callable[[float], None]


def decision_times(start: date, end: date, window: tuple[int, int], step_minutes: int) -> list[int]:
    """Epoch seconds of the decision points on weekdays in [start, end)."""
    out: list[int] = []
    day = start
    while day < end:
        if day.weekday() < 5:
            for minute in range(window[0], window[1], step_minutes):
                local = datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=NY)
                out.append(int(local.timestamp()))
        day += timedelta(days=1)
    return out


def track_record(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    start: date,
    end: date,
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    times = decision_times(start, end, ctx.window, cfg.eval_step_minutes)
    last_bar = int(series.bars.t[-1]) + 60 if len(series.bars) else 0
    trades: list[dict[str, Any]] = []
    skipped = {"stale": 0, "no_data": 0}
    seen: set[tuple[int, str, float]] = set()
    for k, when in enumerate(times):
        if progress and k % 10 == 0:
            progress(k / max(len(times), 1))
        known = series.until(when)
        if len(known.bars) == 0:
            skipped["no_data"] += 1
            continue
        behind = len(trading_minutes(int(known.bars.t[-1]) + 60, when))
        if behind > cfg.stale_minutes:
            skipped["stale"] += 1
            continue
        made, _, _ = candidates_at(known, ctx, cfg, when)
        day = datetime.fromtimestamp(when, NY).date().toordinal()
        for c in made:
            key = (day, c["direction"], float(c["entry"]["price"]))
            if key in seen:
                continue
            seen.add(key)
            horizon = int(c["valid_until"]) + 60 * int(c["max_hold_minutes"])
            if horizon > last_bar:
                continue  # its outcome isn't known yet in this data
            outcome = resolve(c, series.bars, cost_points=ctx.spec.cost_points, now=horizon)
            trades.append(
                {
                    "id": c["id"],
                    "at": when,
                    "day": day,
                    "direction": c["direction"],
                    "with_trend": c["with_trend"],
                    "entry": c["entry"]["price"],
                    "stop": c["stop"],
                    "target": c["targets"][0]["price"],
                    "rr": c["targets"][0]["rr"],
                    **{k2: outcome[k2] for k2 in ("state", "r", "filled_at", "exit_at")},
                }
            )
    if progress:
        progress(1.0)
    split = times[int(len(times) * (1 - cfg.oos_share))] if times else 0
    all_stats = summarize(trades, cfg.min_samples)
    in_sample = summarize([t for t in trades if t["at"] < split], cfg.min_samples)
    out_sample = summarize([t for t in trades if t["at"] >= split], cfg.min_samples)
    return {
        "engine": ENGINE_VERSION,
        "template": "zone_reaction",
        "market": ctx.name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "oos_from": datetime.fromtimestamp(split, UTC).isoformat() if split else None,
        "decisions": len(times),
        "skipped": skipped,
        "data": {
            "source": series.source,
            "label": series.label,
            "synthetic": series.synthetic,
            "proxy": series.proxy,
            "sha256": series.digest(),
        },
        "all": all_stats,
        "in_sample": in_sample,
        "out_of_sample": out_sample,
        "by_direction": {
            d: summarize([t for t in trades if t["direction"] == d], cfg.min_samples)
            for d in ("long", "short")
        },
        "by_trend": {
            name: summarize([t for t in trades if t["with_trend"] is flag], cfg.min_samples)
            for name, flag in (("with_trend", True), ("counter_trend", False))
        },
        "verdict": verdict(in_sample, out_sample, cfg.min_samples),
        "trades": trades[-500:],
        "settings": {
            "step_minutes": cfg.eval_step_minutes,
            "reach_atr": cfg.reach_atr,
            "stop_buffer_atr": cfg.stop_buffer_atr,
            "min_rr": cfg.min_rr,
            "max_stop_atr": cfg.max_stop_atr,
            "valid_minutes": cfg.valid_minutes,
            "max_hold_minutes": cfg.max_hold_minutes,
            "cost_points": ctx.spec.cost_points,
        },
    }


def summarize(trades: list[dict[str, Any]], min_samples: int) -> dict[str, Any]:
    states = {
        s: sum(1 for t in trades if t["state"] == s)
        for s in ("win", "loss", "timeout", "expired", "missed")
    }
    filled = [t for t in trades if t["state"] in ("win", "loss", "timeout")]
    r = np.array([float(t["r"]) for t in filled], dtype=np.float64)
    days = np.array([int(t["day"]) for t in filled], dtype=np.int64)
    wins = int(np.sum(r > 0))
    ci = wilson(wins, len(filled))
    enough = len(filled) >= min_samples
    gains, losses = float(r[r > 0].sum()), float(-r[r < 0].sum())
    curve = np.cumsum(r) if len(r) else np.zeros(0)
    drawdown = (
        float(np.max(np.maximum.accumulate(np.r_[0.0, curve]) - np.r_[0.0, curve]))
        if len(r)
        else 0.0
    )
    return {
        "candidates": len(trades),
        "filled": len(filled),
        "states": states,
        "fill_rate": round(len(filled) / len(trades), 4) if trades else None,
        "win_rate": round(wins / len(filled), 4) if filled and enough else None,
        "win_ci": [round(ci[0], 4), round(ci[1], 4)] if ci and enough else None,
        "expectancy_r": clustered_mean(r, days) if enough else {"mean": None, "n": len(filled)},
        "profit_factor": round(gains / losses, 3) if losses > 0 and enough else None,
        "total_r": round(float(r.sum()), 3) if len(r) else 0.0,
        "max_drawdown_r": round(drawdown, 3),
        "status": "ok" if enough else "insufficient",
    }


def verdict(
    in_sample: dict[str, Any], out_sample: dict[str, Any], min_samples: int
) -> dict[str, str]:
    if out_sample["status"] != "ok" or in_sample["status"] != "ok":
        return {
            "code": "INSUFFICIENT",
            "text": f"Fewer than {min_samples} filled trades in a period — no conclusion.",
        }
    oos = out_sample["expectancy_r"]
    ins = in_sample["expectancy_r"]
    if oos["ci"] and oos["ci"][0] > 0 and ins["mean"] is not None and ins["mean"] > 0:
        return {
            "code": "POSITIVE_OOS",
            "text": "Positive expectancy in sample and out of sample (95% interval above 0 out of "
            "sample). A measurement on this data — not a guarantee; check it on new data.",
        }
    if oos["mean"] is not None and oos["mean"] <= 0:
        return {"code": "NO_EDGE", "text": "No positive expectancy out of sample after costs."}
    return {
        "code": "INCONCLUSIVE",
        "text": "Out-of-sample expectancy is not clearly above zero (interval includes 0).",
    }
