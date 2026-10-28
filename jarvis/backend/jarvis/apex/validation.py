"""The APEX validation protocol — ``docs/APEX_VALIDATION_PROTOCOL.md``.

Every number it judges against is pre-registered in ``config/apex_validation.yaml``; the
strategy rules and parameters are the versioned ones of ``candidates.py`` / ``trigger.py``.

1. **Split.** The trading days of the data, after ``warmup_days``, are cut chronologically into
   a development window and a final holdout (the last ``holdout_share`` of the days at the
   first run for this market and source — then **frozen**, ``apex_holdouts``). The
   development window is divided into ``folds`` consecutive folds.
2. **Embargo.** Decisions whose outcome window (entry validity + maximum hold, at least
   ``embargo_minutes``) would reach past the end of their fold or into the holdout are
   dropped: no label overlaps the next period.
3. **Development.** The registered rules run as if live (``evaluate.collect``) on every
   decision of the development window; outcomes come from ``resolve.py`` with the execution
   model (bid/ask side, costs). Measured: mean R per filled trade with a day-block bootstrap
   95 % interval, per-fold means, mean R with ``cost_stress_factor`` x the costs, every one-step
   parameter neighbour, and a random baseline (same days, random time and direction, the same
   risk and target distance, same costs) — its p-value is judged against ``alpha`` divided
   by the number of trials registered for this market and strategy (``apex_trials``).
4. **Holdout.** Opened only when every development criterion passed with enough evidence,
   and **once** per strategy version and data source (``apex_holdout_opens``).
5. **Verdict** → ``validation_state``: ``OOS_SUPPORTED`` only on real data with the holdout
   passed; ``REJECTED`` when a criterion failed with enough evidence; ``RESEARCH_ONLY`` when
   the evidence is too thin or the data is generated test data (mechanics only).

Nothing here chooses parameters: the neighbours are measured, never adopted.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

import numpy as np

from jarvis.apex import trigger
from jarvis.apex.data import Series
from jarvis.apex.engine import MarketContext
from jarvis.apex.evaluate import (
    NY,
    Cancelled,
    Progress,
    collect,
    decision_times,
    judge,
    step_minutes,
)
from jarvis.apex.execution import Execution
from jarvis.apex.resolve import resolve
from jarvis.learning.market import local_clock
from jarvis.settings import ApexSettings, ApexValidationSettings

FILLED = ("win", "loss", "timeout")
EPOCH = date(1970, 1, 1).toordinal()


# Split --------------------------------------------------------------------------------------


def trading_days(series: Series) -> list[date]:
    """New York weekdays with bars; the last one is left out (it may still be trading)."""
    if len(series.bars) == 0:
        return []
    clock = local_clock(series.bars.t, "America/New_York")
    weekdays = np.unique(clock.day[clock.weekday < 5])
    return [date.fromordinal(EPOCH + int(d)) for d in weekdays[:-1]]


def horizon_minutes(strategy: str, cfg: ApexSettings) -> int:
    valid = cfg.trigger_valid_minutes if strategy == trigger.STRATEGY else cfg.valid_minutes
    return valid + cfg.max_hold_minutes


def midnight(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=NY).timestamp())


def plan_split(
    days: list[date], protocol: ApexValidationSettings, locked: tuple[int, int] | None
) -> dict[str, Any]:
    """Development window, folds and holdout (epoch seconds, New York midnights)."""
    usable = days[protocol.split.warmup_days :]
    if len(usable) < protocol.split.folds + 2:
        raise ValueError(
            f"{len(usable)} trading days after the {protocol.split.warmup_days}-day warm-up — "
            "too few to split into folds and a holdout."
        )
    if locked is None:
        cut = int(len(usable) * (1 - protocol.split.holdout_share))
        holdout = (midnight(usable[cut]), midnight(usable[-1] + timedelta(days=1)))
    else:
        holdout = locked
    dev_days = [d for d in usable if midnight(d) < holdout[0]]
    if len(dev_days) < protocol.split.folds:
        raise ValueError("The frozen holdout leaves too few development days in this data.")
    edges = np.linspace(0, len(dev_days), protocol.split.folds + 1).astype(int)
    # Contiguous: each fold ends where the next begins, the last where the holdout begins.
    starts = [midnight(dev_days[a]) for a in edges[:-1]]
    folds = [(a, b) for a, b in pairwise([*starts, holdout[0]]) if b > a]
    return {"folds": folds, "dev": (folds[0][0], folds[-1][1]), "holdout": holdout}


def embargoed_times(
    window: tuple[int, int],
    ctx: MarketContext,
    step: int,
    embargo_minutes: int,
) -> list[int]:
    start = datetime.fromtimestamp(window[0], NY).date()
    end = datetime.fromtimestamp(window[1], NY).date()
    last = window[1] - 60 * embargo_minutes
    return [t for t in decision_times(start, end, ctx.window, step) if t <= last]


# Statistics ---------------------------------------------------------------------------------


def filled(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in trades if t["state"] in FILLED]


def day_bootstrap(
    r: np.ndarray, days: np.ndarray, resamples: int, seed: int
) -> tuple[float, float] | None:
    """95 % interval of the mean R, resampling whole days (trades of a day are dependent)."""
    if len(r) < 2:
        return None
    unique, index = np.unique(days, return_inverse=True)
    if len(unique) < 2:
        return None
    sums = np.bincount(index, weights=r)
    counts = np.bincount(index).astype(np.float64)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(unique), size=(resamples, len(unique)))
    means = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return round(float(lo), 4), round(float(hi), 4)


def stats(trades: list[dict[str, Any]], resamples: int, seed: int) -> dict[str, Any]:
    done = filled(trades)
    r = np.array([float(t["r"]) for t in done], dtype=np.float64)
    days = np.array([int(t["day"]) for t in done], dtype=np.int64)
    ci = day_bootstrap(r, days, resamples, seed)
    curve = np.cumsum(r) if len(r) else np.zeros(0)
    peak = np.maximum.accumulate(np.r_[0.0, curve])
    gains, losses = float(r[r > 0].sum()), float(-r[r < 0].sum())
    return {
        "candidates": len(trades),
        "filled": len(done),
        "trade_days": len(set(days.tolist())),
        "states": {s: sum(1 for t in trades if t["state"] == s)
                   for s in ("win", "loss", "timeout", "expired", "missed")},
        "mean_r": round(float(r.mean()), 4) if len(r) else None,
        "ci": list(ci) if ci else None,
        "win_rate": round(float(np.mean(r > 0)), 4) if len(r) else None,
        "profit_factor": round(gains / losses, 3) if losses > 0 else None,
        "total_r": round(float(r.sum()), 4),
        "max_drawdown_r": round(float(np.max(peak - np.r_[0.0, curve])), 4) if len(r) else 0.0,
    }  # fmt: skip


def baseline(
    trades: list[dict[str, Any]],
    series: Series,
    execution: Execution,
    times: list[int],
    *,
    runs: int,
    seed: int,
    strategy_mean: float | None,
    hold_minutes: int,
) -> dict[str, Any]:
    """Random time and direction, the same risk and target distance and costs as each real
    trade: does choosing the moment and side beat chance?"""
    done = filled(trades)
    if not done or not times or strategy_mean is None:
        return {"runs": 0, "p_value": None, "mean_r": None}
    rng = np.random.default_rng(seed)
    closes = series.bars.close
    stamps = series.bars.t
    means: list[float] = []
    for _ in range(runs):
        results: list[float] = []
        for t in done:
            when = int(times[int(rng.integers(0, len(times)))])
            k = int(np.searchsorted(stamps, when, side="left")) - 1
            if k < 0:
                continue
            ref = float(closes[k])
            risk = abs(float(t["entry"]) - float(t["stop"]))
            reach = abs(float(t["target"]) - float(t["entry"]))
            long = bool(rng.integers(0, 2))
            sign = 1.0 if long else -1.0
            candidate = {
                "created_at": when,
                "direction": "long" if long else "short",
                "entry": {"type": "market", "price": ref},
                "stop": ref - sign * risk,
                "targets": [{"price": ref + sign * reach}],
                "valid_until": when + 300,
                "max_hold_minutes": hold_minutes,
            }
            out = resolve(
                candidate, series.bars, cost_points=execution.costs,
                now=when + 300 + 60 * hold_minutes, spread=execution.spread,
            )  # fmt: skip
            if out["r"] is not None:
                results.append(float(out["r"]))
        if results:
            means.append(float(np.mean(results)))
    if not means:
        return {"runs": 0, "p_value": None, "mean_r": None}
    beaten = sum(1 for m in means if m >= strategy_mean)
    return {
        "runs": len(means),
        "p_value": round((1 + beaten) / (1 + len(means)), 4),
        "mean_r": round(float(np.mean(means)), 4),
        "p95_r": round(float(np.quantile(means, 0.95)), 4),
    }


# The protocol -------------------------------------------------------------------------------


def neighbours(cfg: ApexSettings, protocol: ApexValidationSettings) -> list[dict[str, float]]:
    out = []
    for name, values in protocol.neighbours.items():
        for value in values:
            if float(getattr(cfg, name)) != float(value):
                out.append({name: float(value)})
    return out


def develop(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    protocol: ApexValidationSettings,
    strategy: str,
    execution: Execution,
    split: dict[str, Any],
    *,
    prior_trials: int,
    seed: int,
    progress: Progress | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Every development measurement and the pre-registered criteria."""
    step = step_minutes(strategy, cfg)
    embargo = max(protocol.split.embargo_minutes, horizon_minutes(strategy, cfg))
    fold_times = [embargoed_times(f, ctx, step, embargo) for f in split["folds"]]
    times = [t for ft in fold_times for t in ft]
    variants: list[dict[str, float]] = [{}, *neighbours(cfg, protocol)]
    ev = protocol.evidence

    def part(i: int) -> Progress:
        def report(x: float) -> None:
            if progress:
                progress((i + x) / (len(variants) + 1))

        return report

    made, skipped = collect(
        series, ctx, cfg, times, strategy, execution, progress=part(0), cancelled=cancelled
    )
    trades = judge(made, series, execution)
    main = stats(trades, ev.bootstrap_resamples, seed)
    for t in trades:
        t["fold"] = next(i for i, f in enumerate(split["folds"]) if f[0] <= t["at"] < f[1])
    folds = [
        {
            "fold": i + 1,
            "start": f[0],
            "end": f[1],
            "decisions": len(fold_times[i]),
            **stats([t for t in trades if t["fold"] == i], ev.bootstrap_resamples, seed),
        }
        for i, f in enumerate(split["folds"])
    ]
    factor = protocol.criteria.cost_stress_factor
    stressed = stats(judge(made, series, execution.stressed(factor)), ev.bootstrap_resamples, seed)
    neighbour_rows = []
    for i, change in enumerate(variants[1:], start=1):
        v_made, _ = collect(
            series, ctx, cfg.model_copy(update=change), times, strategy, execution,
            progress=part(i), cancelled=cancelled,
        )  # fmt: skip
        neighbour_rows.append(
            {"change": change, **stats(judge(v_made, series, execution), 200, seed)}
        )
    if cancelled and cancelled():
        raise Cancelled
    base = baseline(
        trades,
        series,
        execution,
        times,
        runs=ev.baseline_runs,
        seed=seed,
        strategy_mean=main["mean_r"],
        hold_minutes=cfg.max_hold_minutes,
    )
    if progress:
        progress(1.0)
    trials = prior_trials + len(variants)
    alpha = ev.alpha / max(trials, 1)
    criteria = _criteria(protocol, main, folds, stressed, neighbour_rows, base, alpha, trials)
    keys = ("id", "at", "fold", "direction", "entry", "stop", "target", "state", "r",
            "filled_at", "exit_at")  # fmt: skip
    return {
        "decisions": len(times),
        "skipped": skipped,
        "embargo_minutes": embargo,
        "stats": main,
        "folds": folds,
        "cost_stress": {"factor": factor, **stressed},
        "neighbours": neighbour_rows,
        "baseline": base,
        "trials": {
            "before": prior_trials,
            "this_run": len(variants),
            "total": trials,
            "alpha": ev.alpha,
            "alpha_corrected": round(alpha, 6),
        },
        "criteria": criteria,
        "enough": criteria[0]["passed"],
        "passed": all(x["passed"] for x in criteria),
        "variants": variants,
        "ledger": [{k: t[k] for k in keys} for t in trades][-2000:],
    }


def _criteria(
    protocol: ApexValidationSettings,
    main: dict[str, Any],
    folds: list[dict[str, Any]],
    stressed: dict[str, Any],
    neighbour_rows: list[dict[str, Any]],
    base: dict[str, Any],
    alpha: float,
    trials: int,
) -> list[dict[str, Any]]:
    """The pre-registered development criteria, in order; EVIDENCE first."""
    ev, c = protocol.evidence, protocol.criteria

    def positive(rows: list[dict[str, Any]]) -> int:
        return sum(1 for row in rows if row["mean_r"] is not None and row["mean_r"] > 0)

    enough = main["filled"] >= ev.min_dev_trades and main["trade_days"] >= ev.min_trade_days
    ci_ok = bool(main["ci"] and main["ci"][0] > c.dev_mean_r_ci_low_above)
    p = base["p_value"]
    beats = (p is not None and p < alpha) if c.beats_baseline else True
    share_n = positive(neighbour_rows) / len(neighbour_rows) if neighbour_rows else 0.0
    share_f = positive(folds) / max(len(folds), 1)
    return [
        _criterion(
            "EVIDENCE",
            f">= {ev.min_dev_trades} filled trades on >= {ev.min_trade_days} days",
            f"{main['filled']} trades on {main['trade_days']} days",
            enough,
        ),
        _criterion(
            "DEV_CI",
            f"95 % interval of mean R above {c.dev_mean_r_ci_low_above:g}",
            _fmt_ci(main),
            ci_ok,
        ),
        _criterion(
            "BASELINE",
            f"random baseline p < {alpha:.4f} (alpha {ev.alpha:g} / {trials} trials)",
            f"p = {p}",
            beats,
        ),
        _criterion(
            "COST_STRESS",
            f"mean R > 0 with {c.cost_stress_factor:g}x costs",
            str(stressed["mean_r"]),
            (stressed["mean_r"] or 0) > 0,
        ),
        _criterion(
            "NEIGHBOURS",
            f">= {c.neighbours_positive_share:.0%} of parameter neighbours with mean R > 0",
            f"{positive(neighbour_rows)}/{len(neighbour_rows)}",
            share_n >= c.neighbours_positive_share,
        ),
        _criterion(
            "FOLDS",
            f">= {c.folds_positive_share:.0%} of folds with mean R > 0",
            f"{positive(folds)}/{len(folds)}",
            share_f >= c.folds_positive_share,
        ),
    ]


def holdout_test(
    series: Series,
    ctx: MarketContext,
    cfg: ApexSettings,
    protocol: ApexValidationSettings,
    strategy: str,
    execution: Execution,
    split: dict[str, Any],
    *,
    seed: int,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    embargo = max(protocol.split.embargo_minutes, horizon_minutes(strategy, cfg))
    times = embargoed_times(split["holdout"], ctx, step_minutes(strategy, cfg), embargo)
    made, _ = collect(series, ctx, cfg, times, strategy, execution, cancelled=cancelled)
    trades = judge(made, series, execution)
    result = stats(trades, protocol.evidence.bootstrap_resamples, seed)
    h = protocol.holdout
    enough = result["filled"] >= protocol.evidence.min_holdout_trades
    passed = (
        enough
        and (result["mean_r"] or 0) > h.mean_r_above
        and bool(result["ci"] and result["ci"][0] > h.ci_low_above)
    )
    return {"decisions": len(times), "stats": result, "enough": enough, "passed": passed}


def verdict(dev: dict[str, Any], held: dict[str, Any] | None, *, synthetic: bool) -> dict[str, str]:
    if not dev["enough"]:
        state, code = "RESEARCH_ONLY", "INSUFFICIENT"
        text = "Too few trades in the development window to judge — no conclusion."
    elif not dev["passed"]:
        failed = ", ".join(c["code"] for c in dev["criteria"] if not c["passed"])
        state, code = "REJECTED", "DEV_FAILED"
        text = (
            f"Failed the pre-registered development criteria ({failed}). The holdout stays closed."
        )
    elif held is None:
        state, code = "RESEARCH_ONLY", "HOLDOUT_UNAVAILABLE"
        text = "Development criteria passed; the holdout could not be evaluated."
    elif not held["enough"]:
        state, code = "RESEARCH_ONLY", "HOLDOUT_INSUFFICIENT"
        text = (
            "Development criteria passed, but the holdout has too few trades — forward "
            "paper evidence is the next step."
        )
    elif not held["passed"]:
        state, code = "REJECTED", "HOLDOUT_FAILED"
        text = "Passed development, failed the untouched holdout."
    else:
        state, code = "OOS_SUPPORTED", "HOLDOUT_PASSED"
        text = (
            "Passed development and the untouched holdout. A measurement on this data — "
            "forward paper results decide whether it holds."
        )
    if synthetic:
        state = "RESEARCH_ONLY"
        text += " Generated test data: this checks the mechanics, it says nothing about a market."
    return {"validation_state": state, "code": code, "text": text}


def _criterion(code: str, required: str, measured: str, passed: bool) -> dict[str, Any]:
    return {"code": code, "required": required, "measured": measured, "passed": bool(passed)}


def _fmt_ci(s: dict[str, Any]) -> str:
    if s["mean_r"] is None:
        return "no filled trades"
    ci = s["ci"]
    return f"{s['mean_r']:+.3f} R" + (f" [{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "")


def describe_split(split: dict[str, Any]) -> dict[str, Any]:
    def iso(t: int) -> str:
        return datetime.fromtimestamp(t, UTC).isoformat()

    return {
        "dev": [iso(split["dev"][0]), iso(split["dev"][1])],
        "holdout": [iso(split["holdout"][0]), iso(split["holdout"][1])],
        "folds": [[iso(a), iso(b)] for a, b in split["folds"]],
    }
