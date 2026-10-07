"""Monte Carlo analysis of trade sequences (all in R unless stated).

* reshuffle   - same trades, random order: path risk (drawdown, streaks, time underwater)
* bootstrap   - trades drawn with replacement: uncertainty of the ending result as well
* missing     - randomly drop 5/10/20% of trades: fragility to missed executions
* ruin        - fixed-fractional equity paths: probability of severe drawdowns
"""

from __future__ import annotations

import numpy as np


def _path_stats(paths: np.ndarray) -> dict[str, np.ndarray]:
    """Per-simulation max drawdown, longest losing streak and longest time underwater."""
    sims, n = paths.shape
    cum = np.cumsum(paths, axis=1)
    cum0 = np.concatenate([np.zeros((sims, 1)), cum], axis=1)
    peak = np.maximum.accumulate(cum0, axis=1)
    dd = peak - cum0
    max_dd = dd.max(axis=1)
    streak = np.zeros(sims)
    best_streak = np.zeros(sims)
    under = np.zeros(sims)
    best_under = np.zeros(sims)
    for j in range(n):
        lose = paths[:, j] < 0
        streak = np.where(lose, streak + 1, 0)
        best_streak = np.maximum(best_streak, streak)
        uw = dd[:, j + 1] > 1e-12
        under = np.where(uw, under + 1, 0)
        best_under = np.maximum(best_under, under)
    return {"final": cum[:, -1], "max_dd": max_dd, "max_losing_streak": best_streak, "max_underwater": best_under}


def summarize(dist: np.ndarray, adverse_high: bool = True) -> dict[str, float]:
    """Median and adverse percentiles (75/90/95). For 'higher is worse' metrics use the upper tail."""
    d = np.asarray(dist, float)
    d = d[np.isfinite(d)]
    if len(d) == 0:
        return {}
    qs = (75, 90, 95) if adverse_high else (25, 10, 5)
    out = {"median": float(np.median(d))}
    for q in qs:
        out[f"p{q}"] = float(np.percentile(d, q))
    return out


def mc_reshuffle(r: np.ndarray, n_sims: int = 10_000, seed: int = 0) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(seed)
    x = np.asarray(r, float)
    paths = rng.permuted(np.tile(x, (n_sims, 1)), axis=1)
    st = _path_stats(paths)
    return {
        "max_dd_R": summarize(st["max_dd"]),
        "max_losing_streak": summarize(st["max_losing_streak"]),
        "max_underwater_trades": summarize(st["max_underwater"]),
    }


def mc_bootstrap(r: np.ndarray, n_sims: int = 10_000, n_trades: int | None = None, seed: int = 0) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(seed)
    x = np.asarray(r, float)
    m = n_trades or len(x)
    paths = x[rng.integers(0, len(x), size=(n_sims, m))]
    st = _path_stats(paths)
    return {
        "final_R": summarize(st["final"], adverse_high=False),
        "max_dd_R": summarize(st["max_dd"]),
        "max_losing_streak": summarize(st["max_losing_streak"]),
        "max_underwater_trades": summarize(st["max_underwater"]),
        "p_final_le_0": {"value": float((st["final"] <= 0).mean())},
    }


def mc_missing_trades(r: np.ndarray, frac: float, n_sims: int = 5_000, seed: int = 0) -> dict[str, dict[str, float]]:
    """Randomly remove ``frac`` of trades (winners and losers alike) and recompute."""
    rng = np.random.default_rng(seed)
    x = np.asarray(r, float)
    keep = rng.random((n_sims, len(x))) >= frac
    cnt = keep.sum(axis=1)
    exp = np.where(cnt > 0, (x * keep).sum(axis=1) / np.maximum(cnt, 1), np.nan)
    gp = (np.where(x > 0, x, 0) * keep).sum(axis=1)
    gl = -(np.where(x < 0, x, 0) * keep).sum(axis=1)
    pf = np.where(gl > 0, gp / np.where(gl > 0, gl, 1), np.inf)
    paths = np.where(keep, x, 0.0)
    st = _path_stats(paths)
    return {
        "expectancy_R": summarize(exp, adverse_high=False),
        "profit_factor": summarize(pf, adverse_high=False),
        "max_dd_R": summarize(st["max_dd"]),
        "p_expectancy_le_0": {"value": float((exp <= 0).mean())},
    }


def risk_of_ruin(
    r: np.ndarray,
    risk_fraction: float,
    n_trades: int,
    *,
    n_sims: int = 10_000,
    thresholds: tuple[float, ...] = (0.10, 0.20, 0.30, 0.50),
    compounding: bool = True,
    seed: int = 0,
) -> dict[str, float]:
    """P(peak-to-trough equity drawdown >= threshold) over ``n_trades`` bootstrapped trades."""
    rng = np.random.default_rng(seed)
    x = np.asarray(r, float)
    draws = x[rng.integers(0, len(x), size=(n_sims, n_trades))]
    if compounding:
        eq = np.cumprod(1.0 + risk_fraction * draws, axis=1)
    else:
        eq = 1.0 + risk_fraction * np.cumsum(draws, axis=1)
    eq = np.concatenate([np.ones((n_sims, 1)), np.clip(eq, 1e-12, None)], axis=1)
    peak = np.maximum.accumulate(eq, axis=1)
    dd = (1 - eq / peak).max(axis=1)
    out = {f"p_dd_ge_{int(t * 100)}pct": float((dd >= t).mean()) for t in thresholds}
    out["median_max_dd_pct"] = float(np.median(dd) * 100)
    out["p95_max_dd_pct"] = float(np.percentile(dd, 95) * 100)
    return out
