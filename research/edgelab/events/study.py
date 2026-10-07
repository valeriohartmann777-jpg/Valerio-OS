"""Universal event-study framework.

An *event* is a bar position ``pos`` at whose close the event is known, plus the
direction the hypothesis expects (+1 up, -1 down). Outcomes are measured from the
NEXT bar's open (the first executable price), never from the event bar's close,
so the bid/ask bounce and the close-to-open jump cannot inflate results.

For every horizon h (in bars) we record the signed return to the close of bar
``pos + h`` and the MFE/MAE over bars ``pos+1 .. pos+h``, in points and in ATR
units (ATR as of the event bar). With ``session_id`` given, horizons that would
cross into another session are NaN, so overnight gaps never masquerade as edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd
from scipy import stats as sps

DEFAULT_HORIZONS = (1, 2, 3, 5, 10, 20, 30, 60)


def _fwd_max(x: np.ndarray, h: int) -> np.ndarray:
    """out[i] = max(x[i+1 .. i+h])."""
    return pd.Series(x).rolling(h, min_periods=h).max().shift(-h).to_numpy()


def _fwd_min(x: np.ndarray, h: int) -> np.ndarray:
    return pd.Series(x).rolling(h, min_periods=h).min().shift(-h).to_numpy()


def forward_outcomes(
    bars: pd.DataFrame,
    pos: np.ndarray,
    direction: np.ndarray,
    atr_at_event: np.ndarray,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    session_id: np.ndarray | None = None,
) -> pd.DataFrame:
    """Signed forward returns and excursions after each event (see module docstring)."""
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    n = len(c)
    pos = np.asarray(pos, dtype=np.int64)
    d = np.asarray(direction, dtype=float)
    a = np.asarray(atr_at_event, dtype=float)
    entry_pos = pos + 1
    has_entry = entry_pos < n
    ep = np.clip(entry_pos, 0, n - 1)
    entry = np.where(has_entry, o[ep], np.nan)
    sid = None if session_id is None else np.asarray(session_id)
    if sid is not None:
        same = has_entry & (sid[ep] == sid[np.clip(pos, 0, n - 1)])
        entry = np.where(same, entry, np.nan)
    out = pd.DataFrame({"pos": pos, "direction": d, "entry": entry, "atr": a})
    for hz in horizons:
        end = pos + hz
        ok = end < n
        e = np.clip(end, 0, n - 1)
        if sid is not None:
            ok &= sid[e] == sid[ep]
        hmax = _fwd_max(h, hz)[np.clip(pos, 0, n - 1)]
        lmin = _fwd_min(l, hz)[np.clip(pos, 0, n - 1)]
        ret = np.where(ok, d * (c[e] - entry), np.nan)
        mfe = np.where(ok, np.where(d > 0, hmax - entry, entry - lmin), np.nan)
        mae = np.where(ok, np.where(d > 0, entry - lmin, hmax - entry), np.nan)
        out[f"ret_{hz}"] = ret
        out[f"ret_atr_{hz}"] = ret / a
        out[f"mfe_atr_{hz}"] = mfe / a
        out[f"mae_atr_{hz}"] = mae / a
    return out


def describe(x: np.ndarray) -> dict[str, float]:
    """Distribution summary with a one-sample t-test against zero."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n == 0:
        return {"n": 0}
    sd = float(x.std(ddof=1)) if n > 1 else np.nan
    se = sd / np.sqrt(n) if n > 1 else np.nan
    t, p = (sps.ttest_1samp(x, 0.0) if n > 2 else (np.nan, np.nan))
    q = np.quantile(x, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        "n": n,
        "mean": float(x.mean()),
        "median": float(q[2]),
        "std": sd,
        "se": se,
        "t": float(t),
        "p_t": float(p),
        "q05": float(q[0]),
        "q25": float(q[1]),
        "q75": float(q[3]),
        "q95": float(q[4]),
        "p_pos": float((x > 0).mean()),
        "p_neg": float((x < 0).mean()),
        "skew": float(sps.skew(x)) if n > 2 else np.nan,
        "kurt": float(sps.kurtosis(x)) if n > 3 else np.nan,
    }


def cluster_bootstrap_mean_diff(
    x: np.ndarray, gx: np.ndarray, y: np.ndarray, gy: np.ndarray, n_boot: int = 2000, seed: int = 0
) -> dict[str, float]:
    """Bootstrap CI of mean(x) - mean(y), resampling whole clusters (e.g. trading dates).

    Events on the same day are not independent; resampling days keeps that dependence.
    """
    rng = np.random.default_rng(seed)

    def prep(v, g):
        m = np.isfinite(v)
        v, g = v[m], np.asarray(g)[m]
        if len(v) == 0:
            return np.zeros(0), np.zeros(0)
        _, inv = np.unique(g, return_inverse=True)
        sums = np.bincount(inv, weights=v)
        cnts = np.bincount(inv).astype(float)
        return sums, cnts

    sx, cx = prep(np.asarray(x, float), gx)
    sy, cy = prep(np.asarray(y, float), gy)
    if len(sx) == 0 or len(sy) == 0:
        return {"diff": np.nan, "lo": np.nan, "hi": np.nan, "p_boot": np.nan}
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        ix = rng.integers(0, len(sx), len(sx))
        iy = rng.integers(0, len(sy), len(sy))
        diffs[b] = sx[ix].sum() / cx[ix].sum() - sy[iy].sum() / cy[iy].sum()
    point = sx.sum() / cx.sum() - sy.sum() / cy.sum()
    lo, hi = np.quantile(diffs, [0.025, 0.975])
    p = 2 * min((diffs <= 0).mean(), (diffs >= 0).mean())
    return {"diff": float(point), "lo": float(lo), "hi": float(hi), "p_boot": float(min(1.0, p))}


@dataclass
class EventStudyResult:
    """Per-horizon comparison of event outcomes with control outcomes."""

    name: str
    table: pd.DataFrame
    outcomes: pd.DataFrame
    control_outcomes: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)


def event_study(
    name: str,
    bars: pd.DataFrame,
    events: pd.DataFrame,
    atr_series: pd.Series,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    session_id: np.ndarray | None = None,
    controls: pd.DataFrame | None = None,
    cluster: np.ndarray | None = None,
    n_boot: int = 2000,
    seed: int = 0,
) -> EventStudyResult:
    """Run an event study. ``events``/``controls`` need columns ``pos`` and ``direction``.

    The table reports, per horizon, the event distribution of ATR-normalised signed
    returns, the control distribution, the difference with a cluster-bootstrap CI,
    Cohen's d, and Welch's t-test. Effect sizes matter more than p-values here.
    """
    a = atr_series.to_numpy(float)
    ev = forward_outcomes(bars, events["pos"].to_numpy(), events["direction"].to_numpy(), a[events["pos"].to_numpy()], horizons, session_id)
    ctl = None
    if controls is not None and len(controls):
        ctl = forward_outcomes(bars, controls["pos"].to_numpy(), controls["direction"].to_numpy(), a[controls["pos"].to_numpy()], horizons, session_id)
    gid = cluster if cluster is not None else np.arange(len(bars))
    rows = []
    for hz in horizons:
        col = f"ret_atr_{hz}"
        e = describe(ev[col].to_numpy())
        row = {"horizon": hz, **{f"ev_{k}": v for k, v in e.items()}}
        row["ev_mfe_med"] = float(np.nanmedian(ev[f"mfe_atr_{hz}"])) if len(ev) else np.nan
        row["ev_mae_med"] = float(np.nanmedian(ev[f"mae_atr_{hz}"])) if len(ev) else np.nan
        if ctl is not None:
            cdesc = describe(ctl[col].to_numpy())
            row.update({f"ct_{k}": v for k, v in cdesc.items()})
            x, y = ev[col].to_numpy(), ctl[col].to_numpy()
            bd = cluster_bootstrap_mean_diff(x, gid[ev["pos"].to_numpy()], y, gid[ctl["pos"].to_numpy()], n_boot, seed + hz)
            row.update({"diff_mean": bd["diff"], "diff_lo": bd["lo"], "diff_hi": bd["hi"], "p_boot": bd["p_boot"]})
            xf, yf = x[np.isfinite(x)], y[np.isfinite(y)]
            if len(xf) > 2 and len(yf) > 2:
                pooled = np.sqrt((xf.var(ddof=1) + yf.var(ddof=1)) / 2)
                row["cohens_d"] = float((xf.mean() - yf.mean()) / pooled) if pooled > 0 else np.nan
                row["p_welch"] = float(sps.ttest_ind(xf, yf, equal_var=False).pvalue)
        rows.append(row)
    return EventStudyResult(name=name, table=pd.DataFrame(rows), outcomes=ev, control_outcomes=ctl)


def first_passage(
    bars: pd.DataFrame,
    pos: np.ndarray,
    direction: np.ndarray,
    target_price: np.ndarray,
    stop_price: np.ndarray,
    max_bars: int,
    session_id: np.ndarray | None = None,
    ambiguity: str = "adverse",
) -> pd.DataFrame:
    """Which barrier is hit first after entry at the next bar's open.

    outcome = +1 target first, -1 stop first, 0 neither within ``max_bars`` (or the session).
    When both barriers sit inside the same bar the order is unknown: ``adverse`` (default)
    counts it as a stop, ``favorable`` as a target; ``ambiguous`` flags such cases.
    """
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    n = len(o)
    sid = None if session_id is None else np.asarray(session_id)
    res = np.zeros(len(pos), dtype=np.int8)
    nb = np.full(len(pos), np.nan)
    amb = np.zeros(len(pos), dtype=bool)
    entry = np.full(len(pos), np.nan)
    for k, (p, d, tg, st) in enumerate(zip(pos, direction, target_price, stop_price)):
        s = int(p) + 1
        if s >= n or not (np.isfinite(tg) and np.isfinite(st)):
            continue
        e = min(n, s + max_bars)
        if sid is not None:
            same = np.flatnonzero(sid[s:e] != sid[s])
            if same.size:
                e = s + int(same[0])
        entry[k] = o[s]
        if d > 0:
            t_hit = h[s:e] >= tg
            s_hit = l[s:e] <= st
        else:
            t_hit = l[s:e] <= tg
            s_hit = h[s:e] >= st
        it = int(np.argmax(t_hit)) if t_hit.any() else 10**9
        is_ = int(np.argmax(s_hit)) if s_hit.any() else 10**9
        if it == is_ == 10**9:
            continue
        if it < is_:
            res[k], nb[k] = 1, it + 1
        elif is_ < it:
            res[k], nb[k] = -1, is_ + 1
        else:
            gap_t = (o[s + it] >= tg) if d > 0 else (o[s + it] <= tg)
            gap_s = (o[s + it] <= st) if d > 0 else (o[s + it] >= st)
            if gap_s:
                res[k] = -1
            elif gap_t:
                res[k] = 1
            else:
                amb[k] = True
                res[k] = -1 if ambiguity == "adverse" else 1
            nb[k] = it + 1
    return pd.DataFrame({"pos": np.asarray(pos), "entry": entry, "outcome": res, "bars": nb, "ambiguous": amb})
