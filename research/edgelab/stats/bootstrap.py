"""Bootstrap confidence intervals for trade statistics.

``cluster`` resampling draws whole trading dates (or weeks) with replacement, which
respects the dependence between trades of the same day. Use it whenever a
strategy can trade more than once per day.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

UNCERTAIN = "Evidence of positive expectancy remains statistically uncertain."


def _stat_matrix(samples: np.ndarray, stat: str) -> np.ndarray:
    if stat == "mean":
        return samples.mean(axis=1)
    if stat == "sharpe":
        sd = samples.std(axis=1, ddof=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(sd > 0, samples.mean(axis=1) / sd, np.nan)
    if stat == "pf":
        gp = np.where(samples > 0, samples, 0).sum(axis=1)
        gl = -np.where(samples < 0, samples, 0).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(gl > 0, gp / gl, np.inf)
    if stat == "win_rate":
        return (samples > 0).mean(axis=1)
    raise ValueError(stat)


def bootstrap_ci(
    values: np.ndarray,
    stat: str | Callable[[np.ndarray], float] = "mean",
    *,
    n_boot: int = 10_000,
    ci: float = 0.95,
    seed: int = 0,
    cluster: np.ndarray | None = None,
    chunk: int = 1000,
) -> dict[str, float]:
    """Percentile bootstrap CI. Returns point, lo, hi, se and P(stat <= 0)."""
    x = np.asarray(values, float)
    keep = np.isfinite(x)
    x = x[keep]
    n = len(x)
    if n < 2:
        return {"point": float(x.mean()) if n else np.nan, "lo": np.nan, "hi": np.nan, "se": np.nan, "p_le_0": np.nan, "n": n}
    rng = np.random.default_rng(seed)
    fn = (lambda s: _stat_matrix(s, stat)) if isinstance(stat, str) else (lambda s: np.apply_along_axis(stat, 1, s))
    point = float(fn(x[None, :])[0])
    dist = []
    if cluster is None:
        for start in range(0, n_boot, chunk):
            m = min(chunk, n_boot - start)
            idx = rng.integers(0, n, size=(m, n))
            dist.append(fn(x[idx]))
    else:
        g = np.asarray(cluster)[keep]
        _, inv = np.unique(g, return_inverse=True)
        groups = [np.flatnonzero(inv == k) for k in range(inv.max() + 1)]
        G = len(groups)
        for _ in range(n_boot):
            pick = rng.integers(0, G, size=G)
            s = np.concatenate([x[groups[k]] for k in pick])
            dist.append(np.atleast_1d(fn(s[None, :])))
    d = np.concatenate(dist)
    d = d[np.isfinite(d)] if stat != "pf" else d[~np.isnan(d)]
    alpha = (1 - ci) / 2
    lo, hi = np.quantile(d, [alpha, 1 - alpha]) if len(d) else (np.nan, np.nan)
    return {
        "point": point,
        "lo": float(lo),
        "hi": float(hi),
        "se": float(np.std(d[np.isfinite(d)], ddof=1)) if np.isfinite(d).sum() > 1 else np.nan,
        "p_le_0": float((d <= 0).mean()) if len(d) else np.nan,
        "n": n,
    }


def expectancy_verdict(ci: dict[str, float]) -> str:
    """Plain-language reading of an expectancy CI (the protocol's mandatory wording)."""
    if not np.isfinite(ci.get("lo", np.nan)):
        return "Insufficient sample for a confidence interval."
    if ci["lo"] <= 0 <= ci["hi"]:
        return UNCERTAIN
    if ci["lo"] > 0:
        return "95% CI of expectancy excludes zero (positive)."
    return "95% CI of expectancy excludes zero (negative)."
