"""Session, weekly and event-anchored VWAP (causal).

All variants use global cumulative sums, so the VWAP at bar ``t`` from anchor ``a``
is ``(CPV[t] - CPV[a-1]) / (CV[t] - CV[a-1])``. A bar's own volume is included,
which is fine because the value is read at the bar's close.

Price input is the bar's typical price (H+L+C)/3, a documented approximation for
bar data; with trade data the exact trade prices would be used instead.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd


def typical_price(bars: pd.DataFrame) -> pd.Series:
    return (bars["high"] + bars["low"] + bars["close"]) / 3.0


def _cums(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tp = typical_price(bars).to_numpy(dtype=float)
    v = np.nan_to_num(bars["volume"].to_numpy(dtype=float))
    cpv = np.concatenate([[0.0], np.cumsum(tp * v)])
    cv = np.concatenate([[0.0], np.cumsum(v)])
    cpv2 = np.concatenate([[0.0], np.cumsum(tp * tp * v)])
    return cpv, cv, cpv2


def vwap_from_anchor_positions(bars: pd.DataFrame, anchor_pos: np.ndarray, band_mults: Sequence[float] = (1.0, 2.0)) -> pd.DataFrame:
    """VWAP of every bar measured from its own anchor position.

    ``anchor_pos[t]`` is the first bar of the window that ends at ``t`` (or -1 for
    no anchor -> NaN). The anchor must itself be causally known at ``t``.
    """
    cpv, cv, cpv2 = _cums(bars)
    n = len(bars)
    t = np.arange(n)
    a = np.asarray(anchor_pos, dtype=np.int64)
    valid = (a >= 0) & (a <= t)
    a_safe = np.where(valid, a, 0)
    vol = cv[t + 1] - cv[a_safe]
    pv = cpv[t + 1] - cpv[a_safe]
    pv2 = cpv2[t + 1] - cpv2[a_safe]
    with np.errstate(invalid="ignore", divide="ignore"):
        vw = np.where(valid & (vol > 0), pv / vol, np.nan)
        var = np.where(valid & (vol > 0), pv2 / vol - vw * vw, np.nan)
    sd = np.sqrt(np.clip(var, 0.0, None))
    out = pd.DataFrame({"vwap": vw, "vwap_sd": sd}, index=bars.index)
    for m in band_mults:
        out[f"vwap_up_{m:g}"] = vw + m * sd
        out[f"vwap_dn_{m:g}"] = vw - m * sd
    return out


def group_anchor_positions(group: pd.Series | np.ndarray) -> np.ndarray:
    """Anchor = first bar of each contiguous run of equal, non-null group keys."""
    g = pd.Series(group).reset_index(drop=True)
    n = len(g)
    valid = g.notna().to_numpy()
    start = np.zeros(n, dtype=bool)
    if n:
        vals = g.to_numpy()
        start[0] = valid[0]
        start[1:] = valid[1:] & ((~valid[:-1]) | (vals[1:] != vals[:-1]))
    pos = np.where(start, np.arange(n), -1)
    pos = pd.Series(pos).replace(-1, np.nan).ffill().fillna(-1).to_numpy(dtype=np.int64)
    pos[~valid] = -1
    return pos


def session_vwap(bars: pd.DataFrame, group: pd.Series, band_mults: Sequence[float] = (1.0, 2.0)) -> pd.DataFrame:
    """VWAP that resets whenever ``group`` changes (e.g. trading_date; NaN outside the session).

    For RTH VWAP pass ``trading_date.where(is_rth)``; for Globex VWAP pass ``trading_date``.
    """
    return vwap_from_anchor_positions(bars, group_anchor_positions(group), band_mults)


def event_anchored_vwap(bars: pd.DataFrame, anchor_pos: np.ndarray, active_from_pos: np.ndarray) -> pd.Series:
    """VWAP anchored at event bars that only become known later.

    Each event ``k`` anchors at ``anchor_pos[k]`` (e.g. a swing pivot) but may only be
    used from ``active_from_pos[k]`` (e.g. its confirmation bar). At each bar the most
    recently *activated* event defines the anchor, so a pivot that is not yet
    confirmed never influences the series.
    """
    n = len(bars)
    anchor_pos = np.asarray(anchor_pos, dtype=np.int64)
    active_from_pos = np.asarray(active_from_pos, dtype=np.int64)
    if np.any(active_from_pos < anchor_pos):
        raise ValueError("an anchor cannot become active before it occurs")
    per_bar = np.full(n, -1, dtype=np.int64)
    order = np.argsort(active_from_pos, kind="stable")
    for k in order:
        if 0 <= active_from_pos[k] < n:
            per_bar[active_from_pos[k]] = anchor_pos[k]
    s = pd.Series(per_bar).replace(-1, np.nan).ffill().fillna(-1).to_numpy(dtype=np.int64)
    return vwap_from_anchor_positions(bars, s, band_mults=())["vwap"].rename("avwap")
