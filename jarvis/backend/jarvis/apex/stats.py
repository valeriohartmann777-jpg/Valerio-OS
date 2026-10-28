"""Small, explicit statistics: every rate comes with its count and a 95% interval."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score interval of a proportion (95% by default)."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(successes: int, n: int, *, days: int, min_samples: int) -> dict[str, Any]:
    """A counted rate; below ``min_samples`` it is reported as insufficient, never as a number."""
    ci = wilson(successes, n)
    enough = n >= min_samples
    return {
        "p": round(successes / n, 4) if n and enough else None,
        "ci": [round(ci[0], 4), round(ci[1], 4)] if ci and enough else None,
        "n": n,
        "successes": successes,
        "days": days,
        "status": "ok" if enough else "insufficient",
    }


def clustered_mean(values: np.ndarray, clusters: np.ndarray) -> dict[str, Any]:
    """Mean with a cluster-robust standard error (one cluster per trading day: trades on the
    same day aren't independent)."""
    n = len(values)
    if n == 0:
        return {"mean": None, "se": None, "t": None, "ci": None, "n": 0, "clusters": 0}
    mean = float(np.mean(values))
    groups = np.unique(clusters)
    g = len(groups)
    if g < 2:
        return {"mean": round(mean, 4), "se": None, "t": None, "ci": None, "n": n, "clusters": g}
    resid = values - mean
    sums = np.array([resid[clusters == c].sum() for c in groups])
    se = math.sqrt(float(np.sum(sums**2)) * g / (g - 1)) / n
    t = mean / se if se > 0 else None
    return {
        "mean": round(mean, 4),
        "se": round(se, 4),
        "t": round(t, 2) if t is not None else None,
        "ci": [round(mean - Z95 * se, 4), round(mean + Z95 * se, 4)],
        "n": n,
        "clusters": g,
    }
