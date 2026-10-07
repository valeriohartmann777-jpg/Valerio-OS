"""Primary-test statistics for the event studies, all by date-cluster bootstrap.

A :class:`TestInput` holds one observation per row (an event, a control or a day) with
its trading date (the cluster), value, group label and optional stratum / regressors.
``kind`` selects the statistic; ``sign`` orients it so that a positive effect is the
direction the hypothesis predicts. Every statistic except ``median_diff`` is a smooth
function of per-date additive moments, so :func:`edgelab.stats.bootstrap.date_bootstrap`
evaluates it exactly under resampling of whole trading dates.

kinds
-----
diff          mean(a) - mean(b)                                  groups = (a, b)
dod           [mean(a1) - mean(b1)] - [mean(a2) - mean(b2)]      groups = (a1, b1, a2, b2)
strat_diff    sum_s w_s [mean(a,s) - mean(b,s)] / sum_s w_s,     w_s = n_a n_b / (n_a + n_b)
slope         pooled within-stratum OLS slope of value on x
ols           coefficient of x in value ~ 1 + x + x2
median_diff   median(a) - median(b)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..stats.bootstrap import date_bootstrap, date_bootstrap_median_diff

KINDS = ("diff", "dod", "strat_diff", "slope", "ols", "median_diff")


@dataclass
class TestInput:
    __test__ = False

    kind: str
    date: np.ndarray
    value: np.ndarray
    group: np.ndarray
    groups: tuple[str, ...] = ("ev", "ct")
    stratum: np.ndarray | None = None
    x: np.ndarray | None = None
    x2: np.ndarray | None = None
    sign: float = 1.0
    label: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(self.kind)
        self.date = np.asarray(self.date)
        self.value = np.asarray(self.value, float)
        self.group = np.asarray(self.group).astype(str)
        keep = np.isfinite(self.value) & pd.notna(self.date)
        for name in ("x", "x2"):
            arr = getattr(self, name)
            if arr is not None:
                arr = np.asarray(arr, float)
                setattr(self, name, arr)
                keep &= np.isfinite(arr)
        if self.stratum is not None:
            self.stratum = np.asarray(self.stratum).astype(str)
        if not keep.all():
            self.date, self.value, self.group = self.date[keep], self.value[keep], self.group[keep]
            for name in ("x", "x2", "stratum"):
                arr = getattr(self, name)
                if arr is not None:
                    setattr(self, name, arr[keep])

    def subset(self, mask: np.ndarray) -> "TestInput":
        m = np.asarray(mask, bool)
        return TestInput(
            self.kind, self.date[m], self.value[m], self.group[m], self.groups,
            None if self.stratum is None else self.stratum[m],
            None if self.x is None else self.x[m], None if self.x2 is None else self.x2[m],
            self.sign, self.label,
        )

    def n_by_group(self) -> dict[str, int]:
        if self.kind in ("slope", "ols"):
            return {"all": int(len(self.value))}
        return {g: int((self.group == g).sum()) for g in self.groups}

    @property
    def n_dates(self) -> int:
        return int(pd.Series(self.date).nunique())


def _moments(ti: TestInput) -> tuple[dict[str, np.ndarray], callable]:
    v = ti.value
    if ti.kind in ("diff", "dod"):
        mom = {}
        for g in ti.groups:
            ind = (ti.group == g).astype(float)
            mom[f"{g}_s"], mom[f"{g}_n"] = v * ind, ind

        def mean(T, g):
            return T[f"{g}_s"] / T[f"{g}_n"]

        if ti.kind == "diff":
            a, b = ti.groups
            return mom, lambda T: mean(T, a) - mean(T, b)
        a1, b1, a2, b2 = ti.groups
        return mom, lambda T: (mean(T, a1) - mean(T, b1)) - (mean(T, a2) - mean(T, b2))
    if ti.kind == "strat_diff":
        a, b = ti.groups
        strata = sorted(set(ti.stratum.tolist()))
        mom = {}
        for s in strata:
            ins = ti.stratum == s
            for g in (a, b):
                ind = (ins & (ti.group == g)).astype(float)
                mom[f"{s}|{g}_s"], mom[f"{s}|{g}_n"] = v * ind, ind

        def stat(T):
            num = 0.0
            den = 0.0
            for s in strata:
                na, nb = T[f"{s}|{a}_n"], T[f"{s}|{b}_n"]
                ok = (na > 0) & (nb > 0)
                with np.errstate(invalid="ignore", divide="ignore"):
                    w = np.where(ok, na * nb / (na + nb), 0.0)
                    d = np.where(ok, T[f"{s}|{a}_s"] / na - T[f"{s}|{b}_s"] / nb, 0.0)
                num = num + w * d
                den = den + w
            with np.errstate(invalid="ignore", divide="ignore"):
                return np.where(np.asarray(den) > 0, num / den, np.nan)

        return mom, stat
    if ti.kind == "slope":
        x = ti.x
        strata = sorted(set(ti.stratum.tolist())) if ti.stratum is not None else ["all"]
        st = ti.stratum if ti.stratum is not None else np.full(len(v), "all")
        mom = {}
        for s in strata:
            ind = (st == s).astype(float)
            mom[f"{s}|n"], mom[f"{s}|x"], mom[f"{s}|y"] = ind, x * ind, v * ind
            mom[f"{s}|xy"], mom[f"{s}|xx"] = x * v * ind, x * x * ind

        def stat(T):
            num = 0.0
            den = 0.0
            for s in strata:
                n = T[f"{s}|n"]
                with np.errstate(invalid="ignore", divide="ignore"):
                    ok = n > 1
                    num = num + np.where(ok, T[f"{s}|xy"] - T[f"{s}|x"] * T[f"{s}|y"] / np.where(ok, n, 1), 0.0)
                    den = den + np.where(ok, T[f"{s}|xx"] - T[f"{s}|x"] ** 2 / np.where(ok, n, 1), 0.0)
            with np.errstate(invalid="ignore", divide="ignore"):
                return np.where(np.asarray(den) > 0, num / den, np.nan)

        return mom, stat
    if ti.kind == "ols":
        x1, x2 = ti.x, ti.x2
        one = np.ones(len(v))
        mom = {"n": one, "a": x1, "b": x2, "y": v, "aa": x1 * x1, "bb": x2 * x2, "ab": x1 * x2, "ay": x1 * v, "by": x2 * v}

        def stat(T):
            n = np.atleast_1d(T["n"])
            xtx = np.stack([
                np.stack([n, np.atleast_1d(T["a"]), np.atleast_1d(T["b"])], -1),
                np.stack([np.atleast_1d(T["a"]), np.atleast_1d(T["aa"]), np.atleast_1d(T["ab"])], -1),
                np.stack([np.atleast_1d(T["b"]), np.atleast_1d(T["ab"]), np.atleast_1d(T["bb"])], -1),
            ], -2)
            xty = np.stack([np.atleast_1d(T["y"]), np.atleast_1d(T["ay"]), np.atleast_1d(T["by"])], -1)
            out = np.full(len(n), np.nan)
            ok = np.abs(np.linalg.det(xtx)) > 1e-9
            if ok.any():
                out[ok] = np.linalg.solve(xtx[ok], xty[ok][..., None])[:, 1, 0]
            return out if np.ndim(T["n"]) else out[0]

        return mom, stat
    raise ValueError(ti.kind)


def point_estimate(ti: TestInput) -> float:
    """Oriented point estimate (``sign`` applied)."""
    if len(ti.value) == 0:
        return np.nan
    if ti.kind == "median_diff":
        a = ti.group == ti.groups[0]
        b = ti.group == ti.groups[1]
        if not a.any() or not b.any():
            return np.nan
        return float(ti.sign * (np.median(ti.value[a]) - np.median(ti.value[b])))
    mom, stat = _moments(ti)
    tot = {k: np.float64(np.sum(v)) for k, v in mom.items()}  # numpy scalars: 0/0 -> nan, not an exception
    with np.errstate(invalid="ignore", divide="ignore"):
        return float(ti.sign * np.asarray(stat(tot), float))


def evaluate(ti: TestInput, *, n_boot: int = 10_000, seed: int = 0, ci: float = 0.95) -> dict:
    """Oriented effect, percentile CI, two-sided p-value, n per group and n dates."""
    base = {"kind": ti.kind, "label": ti.label, "n_by_group": ti.n_by_group(), "n_obs": int(len(ti.value))}
    if len(ti.value) == 0:
        return {**base, "point": np.nan, "lo": np.nan, "hi": np.nan, "se": np.nan, "p_value": np.nan, "n_dates": 0}
    if ti.kind == "median_diff":
        a = ti.group == ti.groups[0]
        keep = a | (ti.group == ti.groups[1])
        r = date_bootstrap_median_diff(ti.date[keep], ti.value[keep], a[keep], n_boot=n_boot, ci=ci, seed=seed)
    else:
        mom, stat = _moments(ti)
        r = date_bootstrap(ti.date, mom, stat, n_boot=n_boot, ci=ci, seed=seed)
    s = ti.sign
    lo, hi = (s * r["lo"], s * r["hi"]) if s > 0 else (s * r["hi"], s * r["lo"])
    return {**base, "point": s * r["point"], "lo": lo, "hi": hi, "se": r["se"], "p_value": r["p_value"], "n_dates": r["n_dates"]}


def half_split(ti: TestInput) -> tuple[float, float]:
    """Oriented point estimates on the first and second chronological halves of the dates."""
    if len(ti.value) == 0:
        return np.nan, np.nan
    d = pd.to_datetime(pd.Series(ti.date))
    uniq = np.sort(d.unique())
    if len(uniq) < 2:
        return np.nan, np.nan
    cut = uniq[len(uniq) // 2]
    first = (d < cut).to_numpy()
    return point_estimate(ti.subset(first)), point_estimate(ti.subset(~first))
