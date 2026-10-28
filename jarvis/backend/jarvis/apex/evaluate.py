"""Track record of the candidate rules: walk forward through history, as if live.

At every decision point inside the market's session window — each ``eval_step_minutes`` for
``zone_reaction``, each 5-minute close for ``zone_rejection_5m`` and the zone setups — the
series is cut to what was known then (``Series.until``) and **the same function as the live
analysis**
(``engine.plans_at``) makes the strategy's candidates. Each is resolved on the bars after it
with the conservative rules of ``resolve.py`` and the execution model of ``execution.py``
(bid/ask side, costs). A candidate repeating an earlier one of the same day (same direction
and entry) is counted once.

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

from jarvis.apex import setups, trigger
from jarvis.apex.calibration import calibration
from jarvis.apex.data import Series, trading_minutes
from jarvis.apex.engine import ENGINE_VERSION, STRATEGIES, MarketContext, plans_at, segments
from jarvis.apex.execution import Execution, execution_for
from jarvis.apex.probability import features
from jarvis.apex.resolve import resolve
from jarvis.apex.scenarios import base_rates
from jarvis.apex.stats import clustered_mean, wilson
from jarvis.apex.zonemap import ZoneCache
from jarvis.settings import ApexSettings

NY = ZoneInfo("America/New_York")
Progress = Callable[[float], None]


class Cancelled(Exception):
    """A long run was cancelled from the window."""


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


def step_minutes(strategy: str, cfg: ApexSettings) -> int:
    if strategy == trigger.STRATEGY or strategy in setups.SETUPS:
        return trigger.BAR_MINUTES
    return cfg.eval_step_minutes


def collect(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    times: list[int],
    strategy: str,
    execution: Execution,
    *,
    progress: Progress | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Every distinct candidate ``strategy`` made at the decision ``times`` (as if live)."""
    made_all: list[dict[str, Any]] = []
    skipped = {"stale": 0, "no_data": 0}
    seen: set[tuple[int, str, float]] = set()
    zone_cache: ZoneCache = {}
    for k, when in enumerate(times):
        if k % 25 == 0:
            if cancelled and cancelled():
                raise Cancelled
            if progress:
                progress(k / max(len(times), 1))
        known = series.until(when)
        if len(known.bars) == 0:
            skipped["no_data"] += 1
            continue
        behind = len(trading_minutes(int(known.bars.t[-1]) + 60, when))
        if behind > cfg.stale_minutes:
            skipped["stale"] += 1
            continue
        decisions, _ = plans_at(
            known, ctx, cfg, when, execution, (strategy,), zone_cache=zone_cache
        )
        day = datetime.fromtimestamp(when, NY).date().toordinal()
        for c in (decisions.get(strategy) or {}).get("candidates", []):
            key = (day, c["direction"], float(c["entry"]["price"]))
            if key in seen:
                continue
            seen.add(key)
            made_all.append({**c, "at": when, "day": day})
    return made_all, skipped


def judge(made: list[dict[str, Any]], series: Series, execution: Execution) -> list[dict[str, Any]]:
    """Each candidate's outcome on the bars after it; those still undecided at the end of the
    data are left out (their outcome isn't known yet)."""
    last_bar = int(series.bars.t[-1]) + 60 if len(series.bars) else 0
    trades: list[dict[str, Any]] = []
    for c in made:
        horizon = int(c["valid_until"]) + 60 * int(c["max_hold_minutes"])
        if horizon > last_bar:
            continue
        outcome = resolve(
            c, series.bars, cost_points=execution.costs, now=horizon, spread=execution.spread
        )
        trades.append(
            {
                "id": c["id"],
                "at": c["at"],
                "day": c["day"],
                "direction": c["direction"],
                "with_trend": c["with_trend"],
                "entry": c["entry"]["price"],
                "stop": c["stop"],
                "target": c["targets"][0]["price"],
                "rr": c["targets"][0]["rr"],
                "features": features(c),  # known at the decision (the probability head's input)
                **{k: outcome[k] for k in ("state", "r", "filled_at", "exit_at")},
            }
        )
    return trades


def track_record(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    start: date,
    end: date,
    *,
    strategy: str = "zone_reaction",
    execution: Execution | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    execution = execution or execution_for(series, ctx.spec)
    times = decision_times(start, end, ctx.window, step_minutes(strategy, cfg))
    last_bar = int(series.bars.t[-1]) + 60 if len(series.bars) else 0
    made, skipped = collect(series, ctx, cfg, times, strategy, execution, progress=progress)
    trades = judge(made, series, execution)
    if progress:
        progress(1.0)
    # Calibration of the stated scenario rates over the same period (touches decided by then).
    first_ts = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
    last_ts = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp())
    base = base_rates(
        segments(series), ctx.levels_market, ctx.level_rules, ctx.window, ctx.touch_rules, last_bar
    )
    calibrated = calibration(
        base.decided_before(),
        horizon_minutes=ctx.touch_rules.horizon_minutes,
        start=first_ts,
        end=last_ts,
        min_samples=cfg.min_samples,
    )
    split = times[int(len(times) * (1 - cfg.oos_share))] if times else 0
    all_stats = summarize(trades, cfg.min_samples)
    in_sample = summarize([t for t in trades if t["at"] < split], cfg.min_samples)
    out_sample = summarize([t for t in trades if t["at"] >= split], cfg.min_samples)
    return {
        "engine": ENGINE_VERSION,
        "template": strategy,
        "strategy_version": STRATEGIES[strategy],
        "execution": execution.as_dict(),
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
        "calibration": calibrated,
        "trades": trades[-500:],
        "settings": {
            "step_minutes": step_minutes(strategy, cfg),
            "reach_atr": cfg.reach_atr,
            "stop_buffer_atr": cfg.stop_buffer_atr,
            "min_rr": cfg.min_rr,
            "max_stop_atr": cfg.max_stop_atr,
            "valid_minutes": cfg.valid_minutes,
            "trigger_valid_minutes": cfg.trigger_valid_minutes,
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
