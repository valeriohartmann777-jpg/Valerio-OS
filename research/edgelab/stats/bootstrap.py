"""Bootstrap confidence intervals for trade statistics.

``cluster`` resampling draws whole trading dates (or weeks) with replacement, which
respects the dependence between trades of the same day. Use it whenever a
strategy can trade more than once per day.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

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


def _date_codes(dates: np.ndarray) -> tuple[np.ndarray, int]:
    codes, uniq = pd.factorize(pd.Series(np.asarray(dates)), sort=True)
    if (codes < 0).any():
        raise ValueError("cluster keys must not be missing")
    return codes.astype(np.int64), len(uniq)


def date_bootstrap(
    dates: np.ndarray,
    moments: dict[str, np.ndarray],
    stat: Callable[[dict[str, np.ndarray]], np.ndarray],
    *,
    n_boot: int = 10_000,
    ci: float = 0.95,
    seed: int = 0,
    chunk: int = 500,
) -> dict[str, float]:
    """Cluster bootstrap over trading dates for any statistic of ADDITIVE moments.

    ``moments`` holds per-observation additive quantities (e.g. ``value * is_event`` and
    ``is_event``). They are summed per date; each replicate draws D dates with replacement
    (multinomial weights) and evaluates ``stat`` on the weighted totals. Means, differences
    of means, differences of differences, stratified differences, within-stratum slopes and
    OLS coefficients are all smooth functions of such totals, so one routine covers every
    primary test of the event-study protocol. ``stat`` must accept arrays (vectorised) and
    scalars alike.

    Returns point, lo, hi (percentile CI), se, the two-sided percentile p-value
    ``2 * min(P(T* <= 0), P(T* >= 0))``, the number of dates and of finite replicates.
    """
    codes, n_dates = _date_codes(dates)
    keys = list(moments)
    if n_dates == 0:
        return {"point": np.nan, "lo": np.nan, "hi": np.nan, "se": np.nan, "p_value": np.nan, "n_dates": 0, "n_boot_finite": 0}
    per_date = np.column_stack(
        [np.bincount(codes, weights=np.asarray(moments[k], float), minlength=n_dates) for k in keys]
    )
    totals = per_date.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        point = float(np.asarray(stat({k: totals[j] for j, k in enumerate(keys)}), float))
    rng = np.random.default_rng(seed)
    probs = np.full(n_dates, 1.0 / n_dates)
    dist = []
    for start in range(0, n_boot, chunk):
        m = min(chunk, n_boot - start)
        w = rng.multinomial(n_dates, probs, size=m).astype(float)
        t = w @ per_date
        with np.errstate(invalid="ignore", divide="ignore"):
            dist.append(np.asarray(stat({k: t[:, j] for j, k in enumerate(keys)}), float).reshape(-1))
    d = np.concatenate(dist)
    d = d[np.isfinite(d)]
    if len(d) < 2 or not np.isfinite(point):
        return {"point": point, "lo": np.nan, "hi": np.nan, "se": np.nan, "p_value": np.nan, "n_dates": n_dates, "n_boot_finite": int(len(d))}
    alpha = (1 - ci) / 2
    lo, hi = np.quantile(d, [alpha, 1 - alpha])
    p = min(1.0, 2.0 * min((d <= 0).mean(), (d >= 0).mean()))
    return {
        "point": point,
        "lo": float(lo),
        "hi": float(hi),
        "se": float(d.std(ddof=1)),
        "p_value": float(p),
        "n_dates": n_dates,
        "n_boot_finite": int(len(d)),
    }


def _weighted_median(values_sorted: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Row-wise weighted median; ``weights`` is (m, n) aligned with ascending ``values_sorted``."""
    cw = np.cumsum(weights, axis=1)
    half = cw[:, -1:] / 2.0
    idx = (cw >= half).argmax(axis=1)
    out = values_sorted[idx]
    out[cw[:, -1] <= 0] = np.nan
    return out


def date_bootstrap_median_diff(
    dates: np.ndarray,
    values: np.ndarray,
    is_a: np.ndarray,
    *,
    n_boot: int = 10_000,
    ci: float = 0.95,
    seed: int = 0,
    chunk: int = 200,
) -> dict[str, float]:
    """Date-cluster bootstrap of median(a) - median(not a). Medians are not additive, so
    each replicate re-weights observations by their date's multinomial count."""
    v = np.asarray(values, float)
    a = np.asarray(is_a, bool)
    codes, n_dates = _date_codes(dates)
    if n_dates == 0 or a.sum() == 0 or (~a).sum() == 0:
        return {"point": np.nan, "lo": np.nan, "hi": np.nan, "se": np.nan, "p_value": np.nan, "n_dates": n_dates, "n_boot_finite": 0}
    oa, ob = np.argsort(v[a], kind="stable"), np.argsort(v[~a], kind="stable")
    va, vb = v[a][oa], v[~a][ob]
    ca, cb = codes[a][oa], codes[~a][ob]
    point = float(np.median(va) - np.median(vb))
    rng = np.random.default_rng(seed)
    probs = np.full(n_dates, 1.0 / n_dates)
    dist = []
    for start in range(0, n_boot, chunk):
        m = min(chunk, n_boot - start)
        w = rng.multinomial(n_dates, probs, size=m).astype(float)
        dist.append(_weighted_median(va, w[:, ca]) - _weighted_median(vb, w[:, cb]))
    d = np.concatenate(dist)
    d = d[np.isfinite(d)]
    alpha = (1 - ci) / 2
    lo, hi = np.quantile(d, [alpha, 1 - alpha]) if len(d) > 1 else (np.nan, np.nan)
    p = min(1.0, 2.0 * min((d <= 0).mean(), (d >= 0).mean())) if len(d) > 1 else np.nan
    return {"point": point, "lo": float(lo), "hi": float(hi), "se": float(d.std(ddof=1)) if len(d) > 1 else np.nan,
            "p_value": float(p), "n_dates": n_dates, "n_boot_finite": int(len(d))}
