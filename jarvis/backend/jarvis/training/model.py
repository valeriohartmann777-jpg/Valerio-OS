"""Trains the support/resistance model and judges it honestly.

1. **Choose** (in-sample → out-of-sample): each candidate model is trained on
   data before ``oos_start`` and scored on ``oos_start`` → ``holdout_start``.
   The one with the lowest log loss wins. It has to beat the baseline there
   significantly; the bar is Bonferroni-corrected for the number of candidates.
2. **Judge** (holdout, walk-forward): from ``holdout_start`` on, month by
   month, the chosen model is retrained on everything before the month and
   predicts the month. Every holdout prediction is made by a model that never
   saw that month — and nothing about the holdout influenced any choice.
3. **Use**: the final model is trained on all data.

The **baseline** knows the market, the kind of level, the side and how the
touching candle closed relative to the level (a logistic regression). The
model only counts if it predicts better than that: lower log loss, with
t ≥ 1.645 (one-sided 5%) where touches on the same trading day count as one
cluster, since they aren't independent.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from itertools import pairwise
from statistics import NormalDist
from typing import Any, Literal

import numpy as np

from jarvis.training.dataset import BASELINE_FEATURES, FEATURE_LABELS, FEATURES, Dataset

Status = Literal["confirmed", "unconfirmed", "no_edge", "too_little_data"]

HOLDOUT_T = NormalDist().inv_cdf(0.95)
ALPHA = 0.05
MIN_TRAIN = 1000  # decided touches before out-of-sample
MIN_OOS = 300
SHAPE_FEATURES = ("offset", "bar_range", "wick", "vol_ratio")
_EPS = 1e-6


@dataclass(frozen=True)
class Candidate:
    name: str
    make: Callable[[], Any]


# scikit-learn is imported where it is used: importing it takes about a second,
# and JARVIS starts far more often than it trains.


def _boosting(**params: Any) -> Callable[[], Any]:
    def make() -> Any:
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            early_stopping=False, random_state=0, l2_regularization=1.0, **params
        )

    return make


def _logistic() -> Any:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=0.1, max_iter=2000),
    )


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        "Gradient boosting (small)",
        _boosting(learning_rate=0.05, max_iter=150, max_leaf_nodes=8, min_samples_leaf=200),
    ),
    Candidate(
        "Gradient boosting (larger)",
        _boosting(learning_rate=0.05, max_iter=300, max_leaf_nodes=15, min_samples_leaf=100),
    ),
    Candidate(
        "Gradient boosting (shallow)",
        _boosting(learning_rate=0.03, max_iter=250, max_depth=2, min_samples_leaf=400),
    ),
    Candidate("Logistic regression", _logistic),
)


def make_baseline() -> Any:
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline, make_pipeline
    from sklearn.preprocessing import SplineTransformer

    columns = [FEATURES.index(name) for name in BASELINE_FEATURES]
    offset = FEATURES.index("offset")
    return Pipeline(
        [
            (
                "features",
                ColumnTransformer(
                    [
                        (
                            "offset",
                            make_pipeline(
                                SimpleImputer(strategy="median"),
                                SplineTransformer(n_knots=6, degree=3, extrapolation="constant"),
                            ),
                            [offset],
                        ),
                        (
                            "kind",
                            SimpleImputer(strategy="most_frequent"),
                            [c for c in columns if c != offset],
                        ),
                    ]
                ),
            ),
            ("fit", LogisticRegression(C=1.0, max_iter=2000)),
        ]
    )


# Metrics ---------------------------------------------------------------------------------


def losses(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    q = np.clip(p, _EPS, 1 - _EPS)
    out: np.ndarray = -(y * np.log(q) + (1 - y) * np.log(1 - q))
    return out


def clustered_t(values: np.ndarray, cluster: np.ndarray) -> float:
    """t of the mean of ``values`` with errors clustered by ``cluster``."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = float(values.mean())
    _, inverse = np.unique(cluster, return_inverse=True)
    groups = int(inverse.max()) + 1
    if groups < 2:
        return 0.0
    sums = np.bincount(inverse, weights=values - mean)
    variance = float((sums**2).sum()) / n**2 * groups / (groups - 1)
    if variance <= 0:
        return 0.0
    return max(-99.0, min(99.0, mean / math.sqrt(variance)))


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    if len(np.unique(y)) < 2:
        return None
    from sklearn.metrics import roc_auc_score

    return round(float(roc_auc_score(y, p)), 4)


@dataclass(frozen=True)
class Comparison:
    touches: int
    sessions: int
    held_rate: float | None
    loss_model: float | None
    loss_baseline: float | None
    improvement: float | None  # share of the baseline's log loss the model saves
    t: float
    auc_model: float | None
    auc_baseline: float | None


def compare(
    y: np.ndarray, p_model: np.ndarray, p_base: np.ndarray, cluster: np.ndarray
) -> Comparison:
    if len(y) == 0:
        return Comparison(0, 0, None, None, None, None, 0.0, None, None)
    model, base = losses(y, p_model), losses(y, p_base)
    loss_model, loss_base = float(model.mean()), float(base.mean())
    return Comparison(
        touches=len(y),
        sessions=len(np.unique(cluster)),
        held_rate=round(float(y.mean()), 4),
        loss_model=round(loss_model, 5),
        loss_baseline=round(loss_base, 5),
        improvement=round((loss_base - loss_model) / loss_base, 5) if loss_base > 0 else None,
        t=round(clustered_t(base - model, cluster), 2),
        auc_model=_auc(y, p_model),
        auc_baseline=_auc(y, p_base),
    )


CALIBRATION_EDGES = (0.0, 0.35, 0.45, 0.55, 0.65, 1.0)


def calibration(y: np.ndarray, p: np.ndarray) -> list[dict[str, Any]]:
    """How often levels held when the model said p — the check that its
    percentages mean what they say."""
    out = []
    for lo, hi in pairwise(CALIBRATION_EDGES):
        member = (p >= lo) & ((p < hi) if hi < 1.0 else (p <= hi))
        count = int(member.sum())
        out.append(
            {
                "from": lo,
                "to": hi,
                "touches": count,
                "predicted": round(float(p[member].mean()), 4) if count else None,
                "held": round(float(y[member].mean()), 4) if count else None,
            }
        )
    return out


# Training --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Periods:
    oos_start: date
    holdout_start: date
    horizon_seconds: int = 3600


@dataclass
class Trained:
    model: Any
    baseline: Any
    report: dict[str, Any]
    shapes: np.ndarray  # samples of the touching candle's shape (SHAPE_FEATURES)
    features: tuple[str, ...] = FEATURES


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def _month_starts(first: int, last: int) -> list[int]:
    """Epoch seconds of the first day of each month from ``first``'s month."""
    start = datetime.fromtimestamp(first, UTC)
    year, month = start.year, start.month
    out = []
    while True:
        moment = _epoch(date(year, month, 1))
        if moment > last:
            break
        out.append(max(moment, first))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _fit(make: Callable[[], Any], x: np.ndarray, y: np.ndarray) -> Any:
    estimator = make()
    estimator.fit(x, y)
    return estimator


def _held(estimator: Any, x: np.ndarray) -> np.ndarray:
    out: np.ndarray = estimator.predict_proba(x)[:, 1]
    return out


def walk_forward(
    data: Dataset, make: Callable[[], Any], start: int, horizon: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Monthly retraining from ``start``: (mask of predicted rows, model, baseline)."""
    mask = data.t >= start
    p_model = np.full(len(data), np.nan)
    p_base = np.full(len(data), np.nan)
    if not mask.any():
        return mask, p_model, p_base
    bounds = [*_month_starts(start, int(data.t.max())), int(data.t.max()) + 1]
    for lo, hi in pairwise(bounds):
        test = (data.t >= lo) & (data.t < hi)
        if not test.any():
            continue
        train = data.t + horizon <= lo  # outcomes known before the month starts
        if len(np.unique(data.y[train])) < 2:
            mask &= ~test
            continue
        p_model[test] = _held(_fit(make, data.x[train], data.y[train]), data.x[test])
        p_base[test] = _held(_fit(make_baseline, data.x[train], data.y[train]), data.x[test])
    return mask & np.isfinite(p_model), p_model, p_base


def train(
    data: Dataset,
    periods: Periods,
    *,
    min_holdout: int = 300,
    candidates: tuple[Candidate, ...] = CANDIDATES,
    progress: Callable[[str, float], None] | None = None,
) -> Trained:
    def step(text: str, share: float) -> None:
        if progress is not None:
            progress(text, share)

    oos_start, holdout_start = _epoch(periods.oos_start), _epoch(periods.holdout_start)
    horizon = periods.horizon_seconds
    ins = data.t + horizon <= oos_start
    oos = (data.t >= oos_start) & (data.t + horizon <= holdout_start)
    report: dict[str, Any] = {
        "touches": {
            "decided": len(data),
            "in_sample": int(ins.sum()),
            "out_of_sample": int(oos.sum()),
            "holdout": int((data.t >= holdout_start).sum()),
            "skipped": dict(data.skipped),
            "by_market": {m: int((data.market == k).sum()) for k, m in enumerate(data.markets)},
        },
        "baseline_features": [FEATURE_LABELS[f] for f in BASELINE_FEATURES],
        "candidates": [],
        "chosen": None,
        "out_of_sample": None,
        "holdout": None,
        "holdout_by_market": {},
        "calibration": [],
        "importance": [],
        "usable_for": [],
        "t_required_oos": round(NormalDist().inv_cdf(1 - ALPHA / len(candidates)), 2),
        "t_required_holdout": round(HOLDOUT_T, 2),
    }
    if ins.sum() < MIN_TRAIN or oos.sum() < MIN_OOS or len(np.unique(data.y[ins])) < 2:
        report["status"] = "too_little_data"
        report["reason"] = (
            f"{int(ins.sum())} touches to learn from and {int(oos.sum())} to choose with — "
            f"needs {MIN_TRAIN} and {MIN_OOS}."
        )
        return Trained(None, None, report, np.empty((0, len(SHAPE_FEATURES))))

    # 1. Choose on out-of-sample.
    x_in, y_in = data.x[ins], data.y[ins]
    x_oos, y_oos = data.x[oos], data.y[oos]
    base_oos = _held(_fit(make_baseline, x_in, y_in), x_oos)
    scored: list[tuple[float, int, Any, np.ndarray]] = []
    for k, candidate in enumerate(candidates):
        step(f"Trying {candidate.name}", 0.1 + 0.3 * k / len(candidates))
        fitted = _fit(candidate.make, x_in, y_in)
        p = _held(fitted, x_oos)
        loss = float(losses(y_oos, p).mean())
        report["candidates"].append({"name": candidate.name, "loss": round(loss, 5)})
        scored.append((loss, k, fitted, p))
    _, best, fitted, p_oos = min(scored, key=lambda s: (s[0], s[1]))
    chosen = candidates[best]
    report["chosen"] = chosen.name
    oos_result = compare(y_oos, p_oos, base_oos, data.cluster[oos])
    report["out_of_sample"] = asdict(oos_result)
    step("Measuring what matters", 0.45)
    report["importance"] = _importance(fitted, x_oos, y_oos)

    # 2. Judge on the holdout, month by month.
    step("Checking on unseen months", 0.5)
    mask, p_model, p_base = walk_forward(data, chosen.make, holdout_start, horizon)
    y_h, pm, pb, cl = data.y[mask], p_model[mask], p_base[mask], data.cluster[mask]
    holdout = compare(y_h, pm, pb, cl)
    report["holdout"] = asdict(holdout)
    report["calibration"] = calibration(y_h, pm)
    months = (data.t[mask] // 86400).astype("datetime64[D]").astype("datetime64[M]")
    report["holdout_months"] = len(np.unique(months))
    for k, market in enumerate(data.markets):
        own = data.market[mask] == k
        report["holdout_by_market"][market] = asdict(compare(y_h[own], pm[own], pb[own], cl[own]))

    oos_ok = (oos_result.improvement or 0) > 0 and oos_result.t >= report["t_required_oos"]
    holdout_ok = (holdout.improvement or 0) > 0 and holdout.t >= HOLDOUT_T
    status: Status
    if holdout.touches < min_holdout:
        status = "too_little_data"
        report["reason"] = f"only {holdout.touches} unseen touches so far (needs {min_holdout})"
    elif not oos_ok:
        status = "no_edge"
        report["reason"] = "not better than the baseline on the data used to choose it"
    elif not holdout_ok:
        status = "unconfirmed"
        report["reason"] = "better when chosen, but not significantly better on unseen months"
    else:
        status = "confirmed"
        report["reason"] = "significantly better than the baseline on unseen months"
    report["status"] = status
    if status == "confirmed":
        report["usable_for"] = [
            m
            for m, result in report["holdout_by_market"].items()
            if (result.get("improvement") or 0) > 0
        ]

    # 3. The model in use learns from everything.
    step("Training on all data", 0.9)
    final = _fit(chosen.make, data.x, data.y)
    baseline = _fit(make_baseline, data.x, data.y)
    rng = np.random.default_rng(0)
    rows = rng.choice(len(data), size=min(200, len(data)), replace=False)
    shapes = data.x[np.ix_(rows, [FEATURES.index(f) for f in SHAPE_FEATURES])]
    return Trained(final, baseline, report, shapes)


def _importance(estimator: Any, x: np.ndarray, y: np.ndarray) -> list[dict[str, Any]]:
    """How much worse the out-of-sample predictions get without each input."""
    from sklearn.inspection import permutation_importance

    found = permutation_importance(
        estimator, x, y, scoring="neg_log_loss", n_repeats=3, random_state=0
    )
    order = np.argsort(-found.importances_mean)
    out = []
    for k in order[:8].tolist():
        value = float(found.importances_mean[k])
        if value <= 0:
            break
        out.append(
            {
                "feature": FEATURES[k],
                "label": FEATURE_LABELS[FEATURES[k]],
                "loss_increase": round(value, 5),
            }
        )
    return out
