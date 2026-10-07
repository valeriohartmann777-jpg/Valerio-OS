"""Randomisation (permutation) tests against simple null strategies.

The actual statistic is compared with the same statistic computed after
destroying exactly one ingredient of the strategy:

* random direction  - keeps entry times and exits, flips direction at random
                      (does the event predict direction, or is it R:R engineering?)
* random entry      - keeps the number of trades and time-of-day mix, random dates
* random level      - replaces the reference level by a random level with the same
                      distance distribution (does THIS level matter?)
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd


def permutation_pvalue(actual: float, null: np.ndarray, alternative: str = "greater") -> float:
    """Fraction of null draws at least as extreme as ``actual`` (with +1 smoothing)."""
    null = np.asarray(null, float)
    null = null[np.isfinite(null)]
    if len(null) == 0 or not np.isfinite(actual):
        return np.nan
    if alternative == "greater":
        k = (null >= actual).sum()
    elif alternative == "less":
        k = (null <= actual).sum()
    else:
        k = (np.abs(null) >= abs(actual)).sum()
    return float((k + 1) / (len(null) + 1))


def random_directions(n: int, p_long: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.where(rng.random(n) < p_long, 1, -1)


def random_entry_positions(labels: pd.DataFrame, event_pos: np.ndarray, eligible: np.ndarray, seed: int, tolerance_bars: int = 0) -> np.ndarray:
    """One random eligible bar per event, at the same minute of day, on a different date."""
    rng = np.random.default_rng(seed)
    mod = labels["minute_of_day"].to_numpy()
    td = labels["trading_date"].to_numpy()
    el = np.asarray(eligible, bool)
    by_min: dict[int, np.ndarray] = {}
    out = np.full(len(event_pos), -1, dtype=np.int64)
    for k, p in enumerate(event_pos):
        m = int(mod[p])
        if m not in by_min:
            by_min[m] = np.flatnonzero(el & (mod == m))
        cand = by_min[m]
        cand = cand[td[cand] != td[p]]
        if cand.size:
            out[k] = rng.choice(cand)
    return out


def run_null(statistic: Callable[[int], float], n_draws: int) -> np.ndarray:
    """Evaluate ``statistic(seed)`` for ``n_draws`` seeds (the caller builds the null strategy)."""
    return np.array([statistic(s) for s in range(n_draws)], dtype=float)
