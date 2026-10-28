"""Are APEX's stated probabilities right? Calibration of the counted hold rates.

Walk through every decided touch in time order. For each touch, the rate APEX *would have
stated* at that moment is the held share among touches decided before it (same side, sharing
a kind of level) — exactly the rule of ``scenarios.zone_rate``. Compare those stated rates
with what happened:

- **Reliability**: stated rates grouped in bins; per bin the mean stated rate next to the
  observed held share with its 95% Wilson interval. A bin counts as calibrated when the stated
  rate lies inside the observed share's interval at 95% *for all well-filled bins together*
  (Bonferroni over their number) — so one bin out of several isn't flagged by chance alone.
- **Brier score** of the stated rates, and of a simpler reference that only knows the side
  (support / resistance). ``skill`` = 1 - Brier / Brier_reference: above 0 means the zone kinds
  add information, around 0 means they don't.

Touches with fewer than ``min_samples`` comparable earlier touches get no stated rate (as in
the live analysis) and are counted as skipped.
"""

from __future__ import annotations

from itertools import pairwise
from statistics import NormalDist
from typing import Any

import numpy as np

from jarvis.apex.stats import wilson
from jarvis.training.dataset import FEATURES, KIND_GROUPS, Dataset

_COL = {name: k for k, name in enumerate(FEATURES)}
GROUP_COLUMNS = [_COL[g] for g in KIND_GROUPS]
BINS = (0.0, 0.35, 0.45, 0.55, 0.65, 1.0000001)


def calibration(
    data: Dataset, *, horizon_minutes: int, start: int, end: int, min_samples: int
) -> dict[str, Any]:
    """Stated vs. observed hold rates for touches in [start, end) (epoch seconds)."""
    n = len(data)
    if n == 0:
        return _empty()
    order = np.argsort(data.t, kind="stable")
    t = data.t[order]
    y = data.y[order].astype(np.float64)
    support = data.x[order, _COL["support"]] == 1.0
    groups = data.x[order][:, GROUP_COLUMNS] == 1.0
    decided = t + 60 * horizon_minutes
    stated: list[float] = []
    reference: list[float] = []
    outcome: list[float] = []
    side_of: list[bool] = []
    skipped = 0
    for i in np.flatnonzero((t >= start) & (t < end)).tolist():
        before = decided <= t[i]
        same_side = before & (support == support[i])
        comparable = same_side & np.any(groups & groups[i], axis=1)
        k = int(comparable.sum())
        if k < min_samples or int(same_side.sum()) < min_samples:
            skipped += 1
            continue
        stated.append(float(y[comparable].mean()))
        reference.append(float(y[same_side].mean()))
        outcome.append(float(y[i]))
        side_of.append(bool(support[i]))
    if not stated:
        return {**_empty(), "skipped": skipped}
    p, ref, obs = np.array(stated), np.array(reference), np.array(outcome)
    brier = float(np.mean((p - obs) ** 2))
    brier_ref = float(np.mean((ref - obs) ** 2))
    bins: list[dict[str, Any]] = []
    gap = 0.0  # n-weighted |stated - observed|
    filled = sum(1 for lo, hi in pairwise(BINS) if int(((p >= lo) & (p < hi)).sum()) >= min_samples)
    z_all = NormalDist().inv_cdf(1 - 0.05 / (2 * max(filled, 1)))
    for lo, hi in pairwise(BINS):
        mask = (p >= lo) & (p < hi)
        m = int(mask.sum())
        if m == 0:
            continue
        held = int(obs[mask].sum())
        ci = wilson(held, m)
        gap += m * abs(float(p[mask].mean()) - held / m)
        bins.append(
            {
                "range": [round(lo, 2), round(min(hi, 1.0), 2)],
                "n": m,
                "stated": round(float(p[mask].mean()), 4),
                "observed": round(held / m, 4),
                "ci": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
                "inside": _inside(held, m, float(p[mask].mean()), z_all),
            }
        )
    ece = gap / len(p)
    sides = np.array(side_of)
    return {
        "touches": len(p),
        "skipped": skipped,
        "brier": round(brier, 4),
        "brier_reference": round(brier_ref, 4),
        "skill": round(1 - brier / brier_ref, 4) if brier_ref > 0 else None,
        "ece": round(ece, 4),
        "bins": bins,
        "by_side": {side: _side(p, obs, sides == flag) for side, flag in SIDES},
        "verdict": _verdict(bins, len(p), min_samples),
    }


SIDES = (("support", True), ("resistance", False))


def _inside(held: int, n: int, stated: float, z: float) -> bool:
    interval = wilson(held, n, z)
    return bool(interval and interval[0] <= stated <= interval[1])


def _side(p: np.ndarray, obs: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    if not mask.any():
        return {"touches": 0, "stated": None, "observed": None}
    return {
        "touches": int(mask.sum()),
        "stated": round(float(p[mask].mean()), 4),
        "observed": round(float(obs[mask].mean()), 4),
    }


def _verdict(bins: list[dict[str, Any]], touches: int, min_samples: int) -> dict[str, str]:
    if touches < min_samples:
        return {"code": "INSUFFICIENT", "text": "Too few touches to judge the stated rates."}
    big = [b for b in bins if b["n"] >= min_samples]
    if big and all(b["inside"] for b in big):
        return {
            "code": "CALIBRATED",
            "text": "In every well-filled bin the stated rate lies within the observed share's "
            "interval (95% for all bins together).",
        }
    return {
        "code": "MISCALIBRATED",
        "text": "In at least one well-filled bin the observed share differs from the stated rate "
        "beyond chance (95% for all bins together) — treat the stated rates with care.",
    }


def _empty() -> dict[str, Any]:
    return {
        "touches": 0,
        "skipped": 0,
        "brier": None,
        "brier_reference": None,
        "skill": None,
        "ece": None,
        "bins": [],
        "by_side": {},
        "verdict": {"code": "INSUFFICIENT", "text": "No decided touches in the period."},
    }
