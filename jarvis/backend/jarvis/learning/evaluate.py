"""Judges a strategy on three periods so that luck and overfitting don't count.

- **In-sample** (data_start → oos_start): Claude sees everything and iterates.
- **Out-of-sample** (oos_start → holdout_start): run only for strategies that
  pass in-sample. Claude learns pass/fail, nothing more. The bar rises with
  every out-of-sample evaluation ever made (Bonferroni), so trying many ideas
  can't manufacture a finding.
- **Holdout** (holdout_start → today): run only for validated strategies and
  never shown to Claude — it confirms findings for you.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from statistics import NormalDist
from typing import Literal

from jarvis.learning.backtest import Stats, Trade, simulate, summarize
from jarvis.learning.market import Bars, resample
from jarvis.learning.strategy import Strategy
from jarvis.settings import LearningSettings

Status = Literal["invalid", "rejected", "oos_failed", "validated"]


@dataclass(frozen=True)
class Outcome:
    status: Status
    reason: str
    in_sample: Stats | None = None
    out_of_sample: Stats | None = None
    holdout: Stats | None = None
    holdout_confirmed: bool | None = None
    t_required: float | None = None
    skipped_small_stop: int = 0


def t_required(evaluations: int, alpha: float) -> float:
    """One-sided bar for the ``evaluations``-th out-of-sample test."""
    return NormalDist().inv_cdf(1 - alpha / max(1, evaluations))


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def _months(start: int, end: int) -> float:
    return max(0.0, (end - start) / 86400 / 30.44)


def evaluate(
    strategy: Strategy,
    minute_bars: Bars,
    settings: LearningSettings,
    *,
    oos_evaluations_before: int,
) -> Outcome:
    instrument = settings.instruments[strategy.instrument]
    bars = resample(minute_bars, strategy.bar_minutes)
    sim = simulate(
        strategy,
        bars,
        cost_points=instrument.cost_points,
        min_stop_points=instrument.min_stop_points,
    )
    oos_start, holdout_start = _epoch(settings.oos_start), _epoch(settings.holdout_start)
    first = _epoch(settings.data_start)
    last = int(minute_bars.t[-1]) + 60 if len(minute_bars) else holdout_start

    def period(lo: int, hi: int) -> list[Trade]:
        return [t for t in sim.trades if lo <= t.entry_t < hi]

    gates = settings.gates
    ins = summarize(period(first, oos_start), _months(first, oos_start))
    skipped = sim.skipped_small_stop
    reason = _in_sample_problem(ins, strategy, settings)
    if reason:
        return Outcome("rejected", reason, in_sample=ins, skipped_small_stop=skipped)

    t_bar = t_required(oos_evaluations_before + 1, gates.oos_alpha)
    oos = summarize(period(oos_start, holdout_start), _months(oos_start, holdout_start))
    min_oos = gates.min_oos_trades.get(strategy.style, 25)
    if oos.trades < min_oos:
        reason = f"out-of-sample: only {oos.trades} trades (needs {min_oos})"
    elif oos.avg_r <= 0:
        reason = "out-of-sample: lost money after costs"
    elif oos.profit_factor < gates.oos_min_profit_factor:
        reason = f"out-of-sample: profit factor below {gates.oos_min_profit_factor}"
    elif oos.t_stat < t_bar:
        reason = f"out-of-sample: not significant (t below the current bar of {t_bar:.2f})"
    if reason:
        return Outcome(
            "oos_failed",
            reason,
            in_sample=ins,
            out_of_sample=oos,
            t_required=t_bar,
            skipped_small_stop=skipped,
        )

    hold = summarize(period(holdout_start, last), _months(holdout_start, last))
    confirmed = hold.trades >= max(1, min_oos // 2) and hold.avg_r > 0 and hold.profit_factor > 1.0
    return Outcome(
        "validated",
        "passed in-sample and out-of-sample",
        in_sample=ins,
        out_of_sample=oos,
        holdout=hold,
        holdout_confirmed=confirmed,
        t_required=t_bar,
        skipped_small_stop=skipped,
    )


def _in_sample_problem(stats: Stats, strategy: Strategy, settings: LearningSettings) -> str:
    gates = settings.gates
    needed = gates.min_trades.get(strategy.style, 60)
    if stats.trades < needed:
        return f"in-sample: only {stats.trades} trades (needs {needed})"
    if stats.avg_r < gates.in_sample_min_avg_r:
        need = gates.in_sample_min_avg_r
        return f"in-sample: average {stats.avg_r:+.3f} R after costs (needs {need:+.2f})"
    if stats.profit_factor < gates.in_sample_min_profit_factor:
        need = gates.in_sample_min_profit_factor
        return f"in-sample: profit factor {stats.profit_factor:.2f} (needs {need})"
    if stats.t_stat < gates.in_sample_min_t:
        return f"in-sample: t = {stats.t_stat:.2f}, could be noise (needs {gates.in_sample_min_t})"
    return ""
