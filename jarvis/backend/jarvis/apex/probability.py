"""The trade-success probability head: P(target 1 before the stop, after the fill, within the
maximum hold) for one setup, market, data source and version — protocol 1.2.0, section
``probability`` of ``config/apex_validation.yaml`` (pre-registered before any real run).

**Label** (per filled plan): 1 = target 1 first (``win``); 0 = the stop first (``loss`` — a
minute through both counts as the stop) or neither within the maximum hold (``timeout``).
Unfilled plans (``expired``, ``missed``) are outside the event "after the fill": their share
is reported as the fill rate, never as wins or losses.

**Features** (all known when the plan is made, nothing from after it): the break-even
probability after 1.5x costs, zone quality / 100, with the 60-minute trend (1, 0; 0.5 unknown),
long (1 / 0), the stop in ATR(15m).

**Windows** inside the development window only (the holdout is never touched): fit = the first
``fit_share`` of its time, calibration = the next ``calibration_share``, test = the rest. A
trade whose label horizon reaches into the next window is dropped (embargo).

**Model.** L2 logistic regression (C = 1) on standardised features, then Platt scaling fitted on
the calibration window alone, then graded **once** on the test window: Brier score, log loss,
Brier skill against the fit window's base rate, calibration slope and intercept (a logistic
regression of the outcome on logit p), calibration-in-the-large, reliability in tenths with
Wilson intervals, the effective sample (trades of one day are dependent: design effect from the
intra-day correlation), and a **permutation control** — the same pipeline on label shuffles of
the fit and calibration windows; the real skill must beat their 95th percentile.

**Validated** = every pre-registered criterion holds (``criteria``). A plan's probability is the
calibrated model output; its interval and effective sample are those of the test window's
reliability tenth it falls in ("when the model said about this, how often was it right"). An
unvalidated model's number is shown as PROBABILITY NOT VALIDATED and never qualifies a plan.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import numpy as np

from jarvis.apex.setups import EVENT
from jarvis.apex.stats import wilson
from jarvis.settings import ApexProbabilitySettings

if TYPE_CHECKING:  # scikit-learn loads in about a second: only when a head is fitted
    from sklearn.linear_model import LogisticRegression

HEAD_VERSION = "ptp-1.0.0"
FEATURES = ("break_even", "zone_quality", "with_trend", "long", "stop_atr")
EPS = 1e-6


def features(plan: dict[str, Any]) -> list[float] | None:
    """The plan's feature vector, or None when a value is missing (older strategies)."""
    be = plan.get("break_even_probability")
    quality = plan.get("zone_quality")
    risk = plan.get("risk_points")
    atr = plan.get("atr15")
    if be is None or quality is None or risk is None or not atr:
        return None
    trend = plan.get("with_trend")
    return [
        float(be),
        float(quality) / 100.0,
        0.5 if trend is None else float(bool(trend)),
        1.0 if plan.get("direction") == "long" else 0.0,
        float(risk) / float(atr),
    ]


def label(state: str) -> int | None:
    return {"win": 1, "loss": 0, "timeout": 0}.get(state)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-z)))


def _logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(p, EPS, 1 - EPS)
    return np.asarray(np.log(q / (1 - q)))


def effective_sample(y: np.ndarray, days: np.ndarray) -> float:
    """n / design effect, with the intra-day correlation of the outcomes (one-way ANOVA ICC,
    clamped to [0, 1])."""
    n = len(y)
    if n == 0:
        return 0.0
    groups, index = np.unique(days, return_inverse=True)
    k = len(groups)
    if k < 2 or k == n:
        return float(n if k == n else 1)
    sizes = np.bincount(index).astype(float)
    means = np.bincount(index, weights=y) / sizes
    grand = float(np.mean(y))
    msb = float(np.sum(sizes * (means - grand) ** 2)) / (k - 1)
    msw = float(np.sum((y - means[index]) ** 2)) / (n - k)
    m0 = (n - float(np.sum(sizes**2)) / n) / (k - 1)
    denom = msb + (m0 - 1) * msw
    icc = 0.0 if denom <= 0 else min(max((msb - msw) / denom, 0.0), 1.0)
    mbar = n / k
    return round(n / (1 + (mbar - 1) * icc), 2)


def reliability(p: np.ndarray, y: np.ndarray, days: np.ndarray) -> list[dict[str, Any]]:
    """Tenths of the predicted probability: count, effective count, mean prediction, observed
    rate and its Wilson interval."""
    out = []
    which = np.minimum((p * 10).astype(int), 9)
    for b in range(10):
        mask = which == b
        n = int(mask.sum())
        if n == 0:
            out.append({"bin": [b / 10, (b + 1) / 10], "n": 0, "n_eff": 0.0, "predicted": None,
                        "observed": None, "ci": None})  # fmt: skip
            continue
        wins = int(y[mask].sum())
        ci = wilson(wins, n)
        out.append({
            "bin": [b / 10, (b + 1) / 10], "n": n,
            "n_eff": effective_sample(y[mask], days[mask]),
            "predicted": round(float(p[mask].mean()), 4), "observed": round(wins / n, 4),
            "ci": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
        })  # fmt: skip
    return out


def _logistic(c: float) -> LogisticRegression:
    from sklearn.linear_model import LogisticRegression

    return LogisticRegression(C=c, max_iter=1000)


def _fit(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, LogisticRegression]:
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std < 1e-9] = 1.0
    model = _logistic(1.0)
    model.fit((x - mean) / std, y)
    return mean, std, model


def _platt(raw: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Calibration map sigmoid(a·logit(raw) + b), fitted on the calibration window alone."""
    if len(np.unique(y)) < 2:
        return 1.0, 0.0
    lr = _logistic(1e6)  # effectively unpenalised
    lr.fit(_logit(raw).reshape(-1, 1), y)
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def _slope(p: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    if len(np.unique(y)) < 2:
        return None, None
    lr = _logistic(1e6)
    lr.fit(_logit(p).reshape(-1, 1), y)
    return round(float(lr.coef_[0][0]), 4), round(float(lr.intercept_[0]), 4)


def _brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def _log_loss(p: np.ndarray, y: np.ndarray) -> float:
    q = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def _pipeline(
    xf: np.ndarray, yf: np.ndarray, xc: np.ndarray, yc: np.ndarray, xt: np.ndarray
) -> tuple[dict[str, Any], np.ndarray] | None:
    if len(np.unique(yf)) < 2:
        return None
    mean, std, model = _fit(xf, yf)
    raw_c = model.predict_proba((xc - mean) / std)[:, 1]
    a, b = _platt(raw_c, yc)
    raw_t = model.predict_proba((xt - mean) / std)[:, 1]
    p_t = _sigmoid(a * _logit(raw_t) + b)
    params = {"mean": mean.tolist(), "std": std.tolist(), "coef": model.coef_[0].tolist(),
              "intercept": float(model.intercept_[0]), "platt": {"a": a, "b": b}}  # fmt: skip
    return params, p_t


def fit_head(
    trades: list[dict[str, Any]],
    dev: tuple[int, int],
    horizon_minutes: int,
    cfg: ApexProbabilitySettings,
    *,
    seed: int,
) -> dict[str, Any]:
    """Fit, calibrate and grade the head on the development trades of one setup.

    ``trades``: the judged plans (``evaluate.judge``) with their ``features``, ``at``, ``day``
    and ``state``."""
    start, end = dev
    b1 = start + int((end - start) * cfg.fit_share)
    b2 = start + int((end - start) * (cfg.fit_share + cfg.calibration_share))
    horizon = 60 * horizon_minutes
    inside = [t for t in trades if start <= int(t["at"]) < end]  # never the holdout
    filled = [t for t in inside if label(t["state"]) is not None and t.get("features")]
    unfilled = [t for t in inside if t["state"] in ("expired", "missed")]
    fill_rate = (
        round(len(filled) / (len(filled) + len(unfilled)), 4) if filled or unfilled else None
    )

    def window(lo: int, hi: int) -> list[dict[str, Any]]:
        return [t for t in filled if lo <= int(t["at"]) < hi and int(t["at"]) + horizon <= hi]

    fit, cal, test = window(start, b1), window(b1, b2), window(b2, end)
    counts = {"fit": len(fit), "calibration": len(cal), "test": len(test),
              "embargoed": len(filled) - len(fit) - len(cal) - len(test),
              "unfilled": len(unfilled)}  # fmt: skip
    head: dict[str, Any] = {
        "head_version": HEAD_VERSION,
        "event_definition": EVENT,
        "features": list(FEATURES),
        "windows": {"fit": [start, b1], "calibration": [b1, b2], "test": [b2, end]},
        "counts": counts,
        "fill_rate": fill_rate,
        "validated": False,
    }
    if len(fit) < cfg.min_fit or len(cal) < cfg.min_calibration or len(test) < cfg.min_test:
        head["verdict"] = {
            "code": "INSUFFICIENT_DATA",
            "text": f"fit {len(fit)} (min {cfg.min_fit}), calibration {len(cal)} "
            f"(min {cfg.min_calibration}), test {len(test)} (min {cfg.min_test}) trades — "
            "no model, no probability.",
        }
        return head

    def arrays(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (np.array([r["features"] for r in rows], dtype=float),
                np.array([label(r["state"]) for r in rows], dtype=float),
                np.array([int(r["day"]) for r in rows], dtype=np.int64))  # fmt: skip

    xf, yf, _ = arrays(fit)
    xc, yc, _ = arrays(cal)
    xt, yt, dt = arrays(test)
    done = _pipeline(xf, yf, xc, yc, xt)
    if done is None:
        head["verdict"] = {
            "code": "INSUFFICIENT_DATA",
            "text": "Only one outcome in the fit window — nothing to learn.",
        }
        return head  # fmt: skip
    params, p_t = done
    base_rate = float(np.mean(np.r_[yf, yc]))
    brier = _brier(p_t, yt)
    brier_base = _brier(np.full_like(yt, base_rate), yt)
    skill = 1 - brier / brier_base if brier_base > 0 else 0.0
    slope, intercept = _slope(p_t, yt)
    gap = float(np.mean(p_t) - np.mean(yt))
    n_eff = effective_sample(yt, dt)
    rng = np.random.default_rng(seed)
    null_skill: list[float] = []
    for _ in range(cfg.permutation_runs):
        shuffled = _pipeline(xf, rng.permutation(yf), xc, rng.permutation(yc), xt)
        if shuffled is None:
            continue
        b = _brier(shuffled[1], yt)
        null_skill.append(1 - b / brier_base if brier_base > 0 else 0.0)
    p95 = float(np.quantile(null_skill, 0.95)) if null_skill else None
    criteria = [
        _criterion("TEST_SAMPLE", f"effective test sample >= {cfg.min_effective}",
                   f"{n_eff:g}", n_eff >= cfg.min_effective),
        _criterion("SKILL", "Brier skill > 0 against the base rate", f"{skill:+.4f}", skill > 0),
        _criterion("PERMUTATION", "skill above the 95th percentile of label shuffles",
                   "no shuffles" if p95 is None else f"{skill:+.4f} vs {p95:+.4f}",
                   p95 is not None and skill > p95),
        _criterion("SLOPE", f"calibration slope within 1 ± {cfg.max_slope_error:g}",
                   str(slope), slope is not None and abs(slope - 1) <= cfg.max_slope_error),
        _criterion("CALIBRATION_IN_THE_LARGE", f"|mean p - observed| <= {cfg.max_mean_gap:g}",
                   f"{gap:+.4f}", abs(gap) <= cfg.max_mean_gap),
    ]  # fmt: skip
    validated = all(c["passed"] for c in criteria)
    head.update(
        params=params,
        metrics={
            "brier": round(brier, 5), "brier_base_rate": round(brier_base, 5),
            "brier_skill": round(skill, 5), "log_loss": round(_log_loss(p_t, yt), 5),
            "calibration_slope": slope, "calibration_intercept": intercept,
            "mean_gap": round(gap, 5), "base_rate": round(base_rate, 4),
            "test_win_rate": round(float(np.mean(yt)), 4), "test_effective_sample": n_eff,
        },
        bins=reliability(p_t, yt, dt),
        permutation={"runs": len(null_skill), "p95_skill": None if p95 is None else round(p95, 5)},
        criteria=criteria,
        validated=validated,
        verdict={
            "code": "VALIDATED" if validated else "NOT_VALIDATED",
            "text": "Every pre-registered criterion held on the untouched test window."
            if validated else "Failed: " + ", ".join(c["code"] for c in criteria
                                                     if not c["passed"]) + ".",
        },
    )  # fmt: skip
    return head


def predict(model: dict[str, Any] | None, plan: dict[str, Any]) -> dict[str, Any]:
    """The plan's trade-success probability object (``setups.unvalidated`` shape)."""
    out: dict[str, Any] = {
        "event_definition": EVENT, "value": None, "validated": False, "calibrated": False,
        "effective_sample_size": 0, "uncertainty_lower": None, "uncertainty_upper": None,
        "label": "PROBABILITY NOT VALIDATED", "model_id": None,
    }  # fmt: skip
    x = features(plan)
    if model is None or x is None or "params" not in model:
        return out
    params = model["params"]
    z = (np.array(x) - np.array(params["mean"])) / np.array(params["std"])
    raw = _sigmoid(np.array([float(np.dot(params["coef"], z)) + params["intercept"]]))
    p = float(_sigmoid(params["platt"]["a"] * _logit(raw) + params["platt"]["b"])[0])
    b = min(int(p * 10), 9)
    cell = model["bins"][b]
    validated = bool(model.get("validated"))
    out.update(
        value=round(p, 4),
        validated=validated,
        calibrated=validated,  # calibrated AND the calibration held on the test window
        effective_sample_size=cell["n_eff"],
        uncertainty_lower=cell["ci"][0] if cell["ci"] else None,
        uncertainty_upper=cell["ci"][1] if cell["ci"] else None,
        label=None if validated else "PROBABILITY NOT VALIDATED",
        model_id=model.get("id"),
        bin={"range": cell["bin"], "n": cell["n"], "observed": cell["observed"]},
    )
    return out


def model_id(record: dict[str, Any]) -> str:
    raw = json.dumps({k: record.get(k) for k in ("market", "setup", "version", "source",
                                                  "protocol", "windows", "params")},
                     sort_keys=True, default=str)  # fmt: skip
    return "ptp-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _criterion(code: str, required: str, measured: str, passed: bool) -> dict[str, Any]:
    return {"code": code, "required": required, "measured": measured, "passed": bool(passed)}
