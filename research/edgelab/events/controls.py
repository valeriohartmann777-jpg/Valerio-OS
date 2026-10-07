"""Control groups for event studies.

The question is never "did price move after the event?" but "did price move
DIFFERENTLY after the event than it does at comparable ordinary moments?".
Controls therefore match the event's time of day (intraday volatility and drift
are strongly seasonal) and its direction, and come from other trading dates.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def time_matched_controls(
    labels: pd.DataFrame,
    events: pd.DataFrame,
    *,
    n_per_event: int = 5,
    tolerance_minutes: int = 0,
    bar_minutes: int = 1,
    eligible: np.ndarray | None = None,
    seed: int = 0,
) -> pd.DataFrame:
    """Random bars at the same wall-clock minute (+/- tolerance) on OTHER trading dates.

    Each control inherits the direction of its event, so the control mean is the base
    rate of a same-direction position taken at the same time of day.
    """
    rng = np.random.default_rng(seed)
    mod = labels["minute_of_day"].to_numpy()
    td = labels["trading_date"].to_numpy()
    ok = np.ones(len(labels), bool) if eligible is None else np.asarray(eligible, bool)
    pool: dict[int, np.ndarray] = {}
    for m in np.unique(mod[ok]):
        pool[int(m)] = np.flatnonzero(ok & (mod == m))
    rows_pos, rows_dir, rows_ev = [], [], []
    for k, (p, d) in enumerate(zip(events["pos"].to_numpy(), events["direction"].to_numpy())):
        m0 = int(mod[p])
        cands = [pool.get(m0 + dm) for dm in range(-tolerance_minutes, tolerance_minutes + 1, max(1, bar_minutes))]
        cands = [c for c in cands if c is not None and c.size]
        if not cands:
            continue
        cand = np.concatenate(cands)
        cand = cand[td[cand] != td[p]]
        if cand.size == 0:
            continue
        pick = rng.choice(cand, size=n_per_event, replace=cand.size < n_per_event)
        rows_pos.extend(pick.tolist())
        rows_dir.extend([d] * len(pick))
        rows_ev.extend([k] * len(pick))
    return pd.DataFrame({"pos": np.asarray(rows_pos, dtype=np.int64), "direction": rows_dir, "event_idx": rows_ev})


def unconditional_controls(labels: pd.DataFrame, mask: np.ndarray, direction: int) -> pd.DataFrame:
    """Every eligible bar as a control with a fixed direction (the drift baseline)."""
    pos = np.flatnonzero(np.asarray(mask, bool))
    return pd.DataFrame({"pos": pos, "direction": direction})


def random_level_offsets(distances: np.ndarray, n: int, seed: int = 0) -> np.ndarray:
    """Draw signed offsets with the same |distance| distribution as real levels.

    Used for 'does THIS level matter?' tests: replace PDH by price +/- a distance drawn
    from the empirical distribution of |PDH - price| and rerun the identical study.
    """
    rng = np.random.default_rng(seed)
    d = np.abs(np.asarray(distances, float))
    d = d[np.isfinite(d)]
    return rng.choice(d, size=n, replace=True) * rng.choice([-1.0, 1.0], size=n)
